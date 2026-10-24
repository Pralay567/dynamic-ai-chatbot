import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import requests

import server
from config import Config
from db import Database, DatabaseError
from prompts import BASE_SYSTEM_PROMPT

from helpers import (
    FakeResponse,
    FakeSession,
    make_app,
    make_config,
    ok_response,
    reset_logging,
)

CID_A = "3f1b2c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
CID_B = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def chat(client, message, cid=CID_A, **extra):
    body = {"message": message, **extra}
    if cid is not None:
        body["conversation_id"] = cid
    return client.post("/api/v1/chat", data=json.dumps(body), content_type="application/json")


def sent_contents(session, call=-1):
    return session.calls[call][1]["json"]["contents"]


def texts(contents):
    return [(c["role"], c["parts"][0]["text"]) for c in contents]


class Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)
        self.session = FakeSession()

    def build(self, **config_overrides):
        app, self.session = make_app(config=make_config(**config_overrides), session=self.session)
        self.app = app
        self.client = app.test_client()
        return self.client


class MultiTurnMemoryTests(Base):
    def test_first_message_is_stored_and_uses_the_system_prompt(self):
        client = self.build()
        r = chat(client, "Hello")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(body["persisted"])
        self.assertIsInstance(body["message_id"], int)
        self.assertEqual(body["conversation_id"], CID_A)
        self.assertEqual(texts(sent_contents(self.session)), [("user", "Hello")])
        payload = self.session.calls[0][1]["json"]
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], BASE_SYSTEM_PROMPT)

    def test_follow_up_receives_earlier_turns_in_order_with_gemini_roles(self):
        client = self.build()
        self.session.result = ok_response("Paris is the capital of France.")
        chat(client, "What is the capital of France?")
        self.session.result = ok_response("About 2.1 million people.")
        chat(client, "How many people live there?")
        self.session.result = ok_response("Third answer")
        chat(client, "And what is it famous for?")
        self.assertEqual(
            texts(sent_contents(self.session)),
            [
                ("user", "What is the capital of France?"),
                ("model", "Paris is the capital of France."),
                ("user", "How many people live there?"),
                ("model", "About 2.1 million people."),
                ("user", "And what is it famous for?"),
            ],
        )

    def test_conversations_are_isolated_from_each_other(self):
        client = self.build()
        chat(client, "secret-for-A", cid=CID_A)
        chat(client, "question-for-B", cid=CID_B)
        self.assertEqual(texts(sent_contents(self.session)), [("user", "question-for-B")])
        chat(client, "second-for-B", cid=CID_B)
        flat = json.dumps(sent_contents(self.session))
        self.assertNotIn("secret-for-A", flat)
        a = client.get(f"/api/v1/conversations/{CID_A}/messages").get_json()["messages"]
        b = client.get(f"/api/v1/conversations/{CID_B}/messages").get_json()["messages"]
        self.assertTrue(all("B" not in m["content"].replace("Hello from the fake AI", "") for m in a))
        self.assertEqual(len(a), 2)
        self.assertEqual(len(b), 4)

    def test_without_a_conversation_id_nothing_is_stored_or_remembered(self):
        client = self.build()
        r1 = chat(client, "one", cid=None).get_json()
        chat(client, "two", cid=None)
        self.assertEqual(texts(sent_contents(self.session)), [("user", "two")])
        self.assertEqual((r1["persisted"], r1["conversation_id"], r1["message_id"]), (False, None, None))

    def test_remember_context_off_sends_no_history_but_still_stores(self):
        client = self.build()
        chat(client, "first")
        chat(client, "second", use_history=False)
        self.assertEqual(texts(sent_contents(self.session)), [("user", "second")])
        messages = client.get(f"/api/v1/conversations/{CID_A}/messages").get_json()["messages"]
        self.assertEqual(len(messages), 4)

    def test_history_is_bounded_by_message_count(self):
        client = self.build(history_max_messages=2)
        for text in ("q1", "q2", "q3"):
            chat(client, text)
        self.assertEqual(
            texts(sent_contents(self.session)),
            [("user", "q2"), ("model", "Hello from the fake AI"), ("user", "q3")],
        )

    def test_history_is_bounded_by_characters(self):
        # The budget applies to stored history only (not the new message).
        client = self.build(history_max_chars=1000)
        self.session.result = ok_response("A" * 940)
        chat(client, "B" * 50)  # stored pair = 990 chars
        self.session.result = ok_response("short")
        chat(client, "next question")  # 990 <= 1000: whole pair is sent as history
        self.assertEqual(len(sent_contents(self.session)), 3)
        chat(client, "another")  # history would be 990 + 18 = 1008 > 1000
        # The oldest user message no longer fits, and its orphaned reply is dropped too,
        # so the AI never receives a history that starts with its own answer.
        self.assertEqual(
            texts(sent_contents(self.session)),
            [("user", "next question"), ("model", "short"), ("user", "another")],
        )

    def test_uppercase_uuid_is_accepted_and_normalised(self):
        client = self.build()
        r = chat(client, "hi", cid=CID_A.upper())
        self.assertEqual(r.get_json()["conversation_id"], CID_A)
        chat(client, "again", cid=CID_A)
        self.assertEqual(len(sent_contents(self.session)), 3)  # same conversation

    def test_conversation_length_limit(self):
        client = self.build(max_conversation_messages=4)
        chat(client, "one")
        chat(client, "two")
        calls_before = len(self.session.calls)
        r = chat(client, "three")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "conversation_full")
        self.assertEqual(len(self.session.calls), calls_before)
        self.assertEqual(chat(client, "other chat", cid=CID_B).status_code, 200)


class RefreshAndRestartTests(Base):
    def test_history_endpoint_returns_the_stored_chat_after_a_refresh(self):
        client = self.build()
        self.session.result = ok_response("answer one")
        chat(client, "question one")
        r = client.get(f"/api/v1/conversations/{CID_A}/messages")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["conversation_id"], CID_A)
        self.assertEqual(
            [(m["role"], m["content"]) for m in body["messages"]],
            [("user", "question one"), ("assistant", "answer one")],
        )
        self.assertTrue(all({"id", "created_at"} <= set(m) for m in body["messages"]))

    def test_conversation_survives_a_server_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "chat.db")
            app1, session1 = make_app(config=make_config(database_path=db_path), session=FakeSession(ok_response("first answer")))
            chat(app1.test_client(), "first question")
            reset_logging()  # "stop the server"

            session2 = FakeSession(ok_response("second answer"))
            app2, _ = make_app(config=make_config(database_path=db_path), session=session2)
            client2 = app2.test_client()
            chat(client2, "follow-up")
            self.assertEqual(
                texts(sent_contents(session2)),
                [("user", "first question"), ("model", "first answer"), ("user", "follow-up")],
            )
            self.assertEqual(
                len(client2.get(f"/api/v1/conversations/{CID_A}/messages").get_json()["messages"]), 4
            )
            reset_logging()

    def test_history_limit_parameter(self):
        client = self.build()
        for text in ("q1", "q2"):
            chat(client, text)
        r = client.get(f"/api/v1/conversations/{CID_A}/messages?limit=1")
        self.assertEqual([m["role"] for m in r.get_json()["messages"]], ["assistant"])

    def test_unknown_conversation_is_404(self):
        client = self.build()
        r = client.get(f"/api/v1/conversations/{CID_B}/messages")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "conversation_not_found")


class DeleteTests(Base):
    def test_delete_removes_only_that_conversation_and_is_idempotent(self):
        client = self.build()
        chat(client, "a1", cid=CID_A)
        chat(client, "b1", cid=CID_B)
        r = client.delete(f"/api/v1/conversations/{CID_A}")
        self.assertEqual((r.status_code, r.data), (204, b""))
        self.assertEqual(client.get(f"/api/v1/conversations/{CID_A}/messages").status_code, 404)
        self.assertEqual(client.get(f"/api/v1/conversations/{CID_B}/messages").status_code, 200)
        self.assertEqual(client.delete(f"/api/v1/conversations/{CID_A}").status_code, 204)
        chat(client, "fresh start", cid=CID_A)
        self.assertEqual(texts(sent_contents(self.session)), [("user", "fresh start")])

    def test_deleting_a_conversation_that_never_existed_is_a_success(self):
        client = self.build()
        self.assertEqual(client.delete(f"/api/v1/conversations/{CID_B}").status_code, 204)


class ValidationTests(Base):
    def test_bad_conversation_ids_are_rejected_everywhere(self):
        client = self.build()
        v1 = str(uuid.uuid1())
        for bad in ("not-a-uuid", "", 123, ["x"], v1, "a" * 100, "3f1b2c4d5e6f4a7b8c9d0e1f2a3b4c5d" + "z"):
            with self.subTest(bad=str(bad)[:20]):
                r = chat(client, "hi", cid=bad)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"]["code"], "invalid_conversation_id")
        for path_id in ("not-a-uuid", v1, "123"):
            self.assertEqual(client.get(f"/api/v1/conversations/{path_id}/messages").status_code, 400)
            self.assertEqual(client.delete(f"/api/v1/conversations/{path_id}").status_code, 400)
        self.assertEqual(self.session.calls, [])

    def test_use_history_must_be_boolean(self):
        client = self.build()
        for bad in ("yes", 1, "true"):
            r = chat(client, "hi", use_history=bad)
            self.assertEqual(r.get_json()["error"]["code"], "invalid_use_history")
        self.assertEqual(self.session.calls, [])

    def test_history_limit_must_be_sane(self):
        client = self.build()
        for bad in ("0", "abc", "-1", "99999", ""):
            with self.subTest(bad=bad):
                r = client.get(f"/api/v1/conversations/{CID_A}/messages?limit={bad}")
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["error"]["code"], "invalid_limit")

    def test_invalid_input_stores_nothing(self):
        client = self.build()
        chat(client, "   ")
        chat(client, "x" * 5000)
        self.assertEqual(client.get(f"/api/v1/conversations/{CID_A}/messages").status_code, 404)


class FailureTests(Base):
    def test_failed_turn_is_not_remembered_and_does_not_poison_later_turns(self):
        client = self.build()
        chat(client, "good question")
        self.session.result = FakeResponse(500, {"error": {"message": "boom"}})
        r = chat(client, "this will fail")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("reply", r.get_json())
        self.session.result = ok_response("recovered")
        chat(client, "retry")
        self.assertEqual(
            texts(sent_contents(self.session)),
            [("user", "good question"), ("model", "Hello from the fake AI"), ("user", "retry")],
        )
        shown = client.get(f"/api/v1/conversations/{CID_A}/messages").get_json()["messages"]
        self.assertNotIn("this will fail", [m["content"] for m in shown])
        # the failure is still on record (for the error statistics added in a later stage)
        db = self.app.extensions["chatbot_db"]
        with db._connect() as conn:
            row = conn.execute("SELECT status, error_code FROM messages WHERE content = 'this will fail'").fetchone()
        self.assertEqual((row["status"], row["error_code"]), ("failed", "ai_upstream_error"))

    def test_first_ever_message_failing_leaves_nothing_to_restore(self):
        client = self.build()
        self.session.result = requests.exceptions.Timeout()
        self.assertEqual(chat(client, "hi").status_code, 504)
        self.assertEqual(client.get(f"/api/v1/conversations/{CID_A}/messages").status_code, 404)

    def test_reply_is_still_returned_when_storing_it_fails(self):
        client = self.build()
        with mock.patch.object(Database, "add_turn", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertLogs("chatbot", level="ERROR"):
                r = chat(client, "hello")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["reply"], "Hello from the fake AI")
        self.assertEqual((body["persisted"], body["message_id"]), (False, None))

    def test_storage_failure_before_the_ai_call_is_503_and_skips_the_ai(self):
        client = self.build()
        with mock.patch.object(Database, "count_messages", side_effect=sqlite3.OperationalError("locked")):
            with self.assertLogs("chatbot", level="ERROR"):
                r = chat(client, "hello")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"]["code"], "storage_unavailable")
        self.assertEqual(self.session.calls, [])

    def test_storage_failure_on_read_endpoints_is_503_without_details(self):
        client = self.build()
        with mock.patch.object(Database, "list_messages", side_effect=sqlite3.OperationalError("secret path /x/y")):
            with self.assertLogs("chatbot", level="ERROR"):
                r = client.get(f"/api/v1/conversations/{CID_A}/messages")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(b"secret path", r.data)
        with mock.patch.object(Database, "delete_conversation", side_effect=sqlite3.OperationalError("x")):
            with self.assertLogs("chatbot", level="ERROR"):
                self.assertEqual(client.delete(f"/api/v1/conversations/{CID_A}").status_code, 503)

    def test_original_ai_error_is_kept_even_if_recording_the_failure_also_fails(self):
        client = self.build()
        self.session.result = FakeResponse(500, {})
        with mock.patch.object(Database, "add_failed_user_message", side_effect=sqlite3.OperationalError("x")):
            with self.assertLogs("chatbot", level="ERROR"):
                r = chat(client, "hello")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["error"]["code"], "ai_upstream_error")

    def test_unusable_database_path_stops_startup_with_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file.txt"
            blocker.write_text("x")
            config = make_config(database_path=str(blocker / "inner" / "x.db"))
            with self.assertRaises(DatabaseError):
                server.create_app(config)
            reset_logging()
            with mock.patch.object(server, "load_config", return_value=config):
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertEqual(server.main(), 2)
            reset_logging()
        self.assertIn("Storage error", err.getvalue())


class LimitsAndCorsTests(Base):
    def test_conversation_endpoints_have_their_own_rate_limit(self):
        client = self.build(api_rate_limit_per_minute=2, rate_limit_per_minute=0)
        url = f"/api/v1/conversations/{CID_A}/messages"
        statuses = [client.get(url).status_code for _ in range(3)]
        self.assertEqual(statuses, [404, 404, 429])
        self.assertEqual(client.delete(f"/api/v1/conversations/{CID_A}").status_code, 429)
        self.assertEqual(chat(client, "chat is not affected").status_code, 200)

    def test_chat_limit_does_not_block_loading_history(self):
        client = self.build(rate_limit_per_minute=1, api_rate_limit_per_minute=0)
        chat(client, "one")
        self.assertEqual(chat(client, "two").status_code, 429)
        self.assertEqual(client.get(f"/api/v1/conversations/{CID_A}/messages").status_code, 200)

    def test_cors_preflight_allows_delete_for_allowed_origins_only(self):
        client = self.build(cors_allowed_origins=("https://app.example.com",))
        r = client.options(
            f"/api/v1/conversations/{CID_A}",
            headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "DELETE"},
        )
        self.assertIn("DELETE", r.headers["Access-Control-Allow-Methods"])
        r = client.options(
            f"/api/v1/conversations/{CID_A}",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "DELETE"},
        )
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)


class PrivacyTests(Base):
    def test_log_never_contains_the_full_conversation_id_or_message_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            client = self.build(log_file=str(log_path), log_level="INFO")
            chat(client, "very-private-text-424242")
            client.get(f"/api/v1/conversations/{CID_A}/messages")
            self.session.result = FakeResponse(500, {})
            chat(client, "another-private-text-737373")
            client.delete(f"/api/v1/conversations/{CID_A}")
            reset_logging()
            log = log_path.read_text(encoding="utf-8")
        self.assertNotIn(CID_A, log)
        self.assertNotIn("very-private-text-424242", log)
        self.assertNotIn("another-private-text-737373", log)
        self.assertIn(f"conv={CID_A[:8]}", log)
        self.assertIn("conversation deleted", log)
        self.assertIn(f"/api/v1/conversations/{CID_A[:8]}...", log)

    def test_default_database_location_is_inside_the_project_data_folder_and_git_ignored(self):
        cfg = Config.from_env({})
        self.assertEqual(cfg.database_path, "data/chatbot.db")
        ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/*.db*", ignore)
        self.assertIn("*.db-wal", ignore)


if __name__ == "__main__":
    unittest.main()
