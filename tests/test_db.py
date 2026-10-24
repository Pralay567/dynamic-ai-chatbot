import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from db import LATEST_VERSION, MAX_FEEDBACK_COMMENT_CHARS, Database, DatabaseError

import helpers  # noqa: F401  (puts project root on sys.path)


class DbTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sub" / "test.db"
        self.db = Database(self.path)
        self.db.initialize()

    def add_turns(self, conversation_id, count, prefix="q"):
        ids = []
        for i in range(1, count + 1):
            ids.append(self.db.add_turn(conversation_id, f"{prefix}{i}", f"a{i}", latency_ms=10 * i))
        return ids


class LifecycleTests(DbTestCase):
    def test_initialize_creates_folder_file_and_schema(self):
        self.assertTrue(self.path.exists())
        self.assertEqual(self.db.schema_version, LATEST_VERSION)
        with closing(sqlite3.connect(self.path)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"conversations", "messages", "feedback"} <= tables)

    def test_initialize_twice_is_safe_and_keeps_data(self):
        self.db.add_turn("c1", "hello", "hi")
        self.db.initialize()
        Database(self.path).initialize()
        self.assertEqual(self.db.count_messages("c1"), 2)

    def test_database_from_a_newer_app_is_refused(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 5}")
        with self.assertRaises(DatabaseError) as ctx:
            Database(self.path).initialize()
        self.assertIn("newer version", str(ctx.exception))

    def test_unusable_path_gives_readable_error_without_leaking_the_path(self):
        blocker = self.path.parent / "not_a_folder"
        blocker.write_text("x")
        bad = Database(blocker / "inner" / "x.db")
        with self.assertRaises(DatabaseError) as ctx:
            bad.initialize()
        self.assertIn("DATABASE_PATH", str(ctx.exception))
        self.assertNotIn(str(blocker), str(ctx.exception))

    def test_data_survives_a_new_database_object(self):
        self.db.add_turn("c1", "remember me", "ok")
        reopened = Database(self.path)
        reopened.initialize()
        self.assertEqual(
            [m["content"] for m in reopened.list_messages("c1", limit=10)], ["remember me", "ok"]
        )

    def test_memory_databases_are_independent(self):
        a, b = Database(":memory:"), Database(":memory:")
        a.initialize()
        b.initialize()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.add_turn("c1", "x", "y")
        self.assertEqual(a.count_messages("c1"), 2)
        self.assertEqual(b.count_messages("c1"), 0)

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db._connect() as conn:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES ('ghost', 'user', 'x', 'now')"
                )


class MessageTests(DbTestCase):
    def test_turn_is_stored_in_order_with_ids_and_timestamps(self):
        reply_id = self.db.add_turn("c1", "question", "answer", latency_ms=123)
        messages = self.db.list_messages("c1", limit=10)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual([m["content"] for m in messages], ["question", "answer"])
        self.assertEqual(messages[1]["id"], reply_id)
        self.assertTrue(messages[0]["created_at"].endswith("+00:00"))
        with self.db._connect() as conn:
            row = conn.execute("SELECT latency_ms FROM messages WHERE id = ?", (reply_id,)).fetchone()
        self.assertEqual(row[0], 123)

    def test_unicode_and_sql_metacharacters_round_trip_verbatim(self):
        tricky = ["नमस्ते 😊 हिंदी", "'; DROP TABLE messages;--", 'quote " and \\ backslash\nnewline']
        for text in tricky:
            self.db.add_turn("c1", text, text[::-1])
        stored = [m["content"] for m in self.db.list_messages("c1", limit=50)]
        self.assertEqual(stored[::2], tricky)
        self.assertEqual(self.db.count_messages("c1"), 6)

    def test_failed_user_message_is_recorded_but_never_shown_or_used_as_context(self):
        self.db.add_turn("c1", "ok question", "ok answer")
        self.db.add_failed_user_message("c1", "this one failed", "ai_timeout")
        self.assertEqual(self.db.count_messages("c1"), 3)
        shown = [m["content"] for m in self.db.list_messages("c1", limit=10)]
        self.assertEqual(shown, ["ok question", "ok answer"])
        history = self.db.get_history("c1", max_messages=10, max_chars=1000)
        self.assertNotIn("this one failed", [m["content"] for m in history])
        with self.db._connect() as conn:
            row = conn.execute("SELECT status, error_code FROM messages WHERE content = 'this one failed'").fetchone()
        self.assertEqual((row["status"], row["error_code"]), ("failed", "ai_timeout"))

    def test_list_messages_returns_the_newest_n_oldest_first(self):
        self.add_turns("c1", 5)
        shown = [m["content"] for m in self.db.list_messages("c1", limit=3)]
        self.assertEqual(shown, ["a4", "q5", "a5"])

    def test_conversations_are_isolated(self):
        self.add_turns("A", 2, prefix="alpha")
        self.add_turns("B", 1, prefix="beta")
        a = [m["content"] for m in self.db.list_messages("A", limit=50)]
        b = [m["content"] for m in self.db.list_messages("B", limit=50)]
        self.assertTrue(all("beta" not in c for c in a))
        self.assertTrue(all("alpha" not in c for c in b))
        self.assertEqual(self.db.list_messages("nobody", limit=10), [])
        self.assertEqual(self.db.get_history("nobody", max_messages=10, max_chars=1000), [])


class HistoryTests(DbTestCase):
    def roles_and_text(self, history):
        return [(m["role"], m["content"]) for m in history]

    def test_history_is_oldest_first_and_alternates(self):
        self.add_turns("c1", 3)
        history = self.db.get_history("c1", max_messages=50, max_chars=10_000)
        self.assertEqual(
            self.roles_and_text(history),
            [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2"), ("user", "q3"), ("assistant", "a3")],
        )

    def test_message_limit_never_starts_with_an_assistant_message(self):
        self.add_turns("c1", 5)
        # last 3 messages are a4, q5, a5 -> the leading assistant message is dropped
        history = self.db.get_history("c1", max_messages=3, max_chars=10_000)
        self.assertEqual(self.roles_and_text(history), [("user", "q5"), ("assistant", "a5")])
        history = self.db.get_history("c1", max_messages=4, max_chars=10_000)
        self.assertEqual(
            self.roles_and_text(history),
            [("user", "q4"), ("assistant", "a4"), ("user", "q5"), ("assistant", "a5")],
        )

    def test_character_budget_keeps_the_most_recent_messages(self):
        self.db.add_turn("c1", "x" * 100, "y" * 100)
        self.db.add_turn("c1", "recent question", "recent answer")
        budget = len("recent question") + len("recent answer") + 5
        history = self.db.get_history("c1", max_messages=50, max_chars=budget)
        self.assertEqual(
            self.roles_and_text(history),
            [("user", "recent question"), ("assistant", "recent answer")],
        )

    def test_budget_smaller_than_the_latest_pair_gives_empty_history(self):
        self.db.add_turn("c1", "q" * 50, "a" * 50)
        self.assertEqual(self.db.get_history("c1", max_messages=50, max_chars=10), [])

    def test_zero_messages_means_no_history(self):
        self.add_turns("c1", 2)
        self.assertEqual(self.db.get_history("c1", max_messages=0, max_chars=10_000), [])


class DeleteTests(DbTestCase):
    def test_delete_removes_messages_and_feedback_but_only_for_that_conversation(self):
        a_ids = self.add_turns("A", 2)
        self.add_turns("B", 1)
        self.db.set_feedback("A", a_ids[0], 1, "nice")
        self.assertEqual(self.db.delete_conversation("A"), 4)
        self.assertEqual(self.db.count_messages("A"), 0)
        self.assertIsNone(self.db.get_feedback(a_ids[0]))
        self.assertEqual(self.db.count_messages("B"), 2)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversations WHERE id='A'").fetchone()[0], 0)

    def test_deleting_an_unknown_conversation_is_harmless(self):
        self.assertEqual(self.db.delete_conversation("never-existed"), 0)


class FeedbackTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.reply_id = self.db.add_turn("c1", "q", "a")
        self.user_id = self.reply_id - 1

    def test_set_get_and_replace(self):
        self.db.set_feedback("c1", self.reply_id, 1, "  great  ")
        fb = self.db.get_feedback(self.reply_id)
        self.assertEqual((fb["rating"], fb["comment"], fb["conversation_id"]), (1, "great", "c1"))
        self.db.set_feedback("c1", self.reply_id, -1)
        fb = self.db.get_feedback(self.reply_id)
        self.assertEqual((fb["rating"], fb["comment"]), (-1, None))
        with self.db._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 1)

    def test_blank_comment_becomes_none(self):
        self.db.set_feedback("c1", self.reply_id, 1, "   ")
        self.assertIsNone(self.db.get_feedback(self.reply_id)["comment"])

    def test_bad_values_are_rejected(self):
        for rating in (0, 2, "1", None, True and 5):
            with self.subTest(rating=rating), self.assertRaises(ValueError):
                self.db.set_feedback("c1", self.reply_id, rating)
        with self.assertRaises(ValueError):
            self.db.set_feedback("c1", self.reply_id, 1, "x" * (MAX_FEEDBACK_COMMENT_CHARS + 1))
        self.db.set_feedback("c1", self.reply_id, 1, "x" * MAX_FEEDBACK_COMMENT_CHARS)

    def test_feedback_only_on_assistant_messages_of_the_same_conversation(self):
        other_reply = self.db.add_turn("c2", "q", "a")
        for conv, message_id in (("c1", self.user_id), ("c2", self.reply_id), ("c1", other_reply), ("c1", 99999)):
            with self.subTest(conv=conv, message_id=message_id), self.assertRaises(LookupError):
                self.db.set_feedback(conv, message_id, 1)


class ConcurrencyTests(DbTestCase):
    def test_parallel_writers_do_not_corrupt_or_lock(self):
        errors = []

        def worker(conversation_id, turns):
            try:
                for i in range(turns):
                    self.db.add_turn(conversation_id, f"{conversation_id}-q{i}", f"{conversation_id}-a{i}")
                    self.db.get_history(conversation_id, max_messages=10, max_chars=5000)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(f"own{n}", 15)) for n in range(6)]
        threads += [threading.Thread(target=worker, args=("shared", 15)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        for n in range(6):
            self.assertEqual(self.db.count_messages(f"own{n}"), 30)
        shared = self.db.list_messages("shared", limit=500)
        self.assertEqual(len(shared), 4 * 15 * 2)
        # overlapping turns never interleave a user message with someone else's reply
        self.assertEqual([m["role"] for m in shared], ["user", "assistant"] * 60)


if __name__ == "__main__":
    unittest.main()
