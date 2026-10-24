"""SQLite persistence for conversations, messages and feedback.

Standard library only. Design notes:

* One short-lived connection per operation (safe with Flask's threaded server).
* Schema changes are numbered migrations tracked with ``PRAGMA user_version``.
* A user message and its AI reply are written together in ONE transaction, so a
  conversation's history always alternates user / assistant even when requests
  overlap. A turn whose AI call failed keeps only the user message, flagged
  ``status='failed'``; failed messages are never sent to the AI as context.
* Conversation ids are random UUIDs created by the browser. Knowing the id is the
  only access control (there are no accounts in this version), so ids are never
  listed or searchable and every query is scoped to a single id.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

from config import BASE_DIR

MAX_FEEDBACK_COMMENT_CHARS = 500


class DatabaseError(Exception):
    """The database could not be opened or migrated."""


MIGRATIONS = [
    (
        1,
        """
        CREATE TABLE conversations (
            id          TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content         TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'failed')),
            error_code      TEXT,
            latency_ms      INTEGER,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX idx_messages_conversation ON messages(conversation_id, id);
        CREATE TABLE feedback (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id      INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            rating          INTEGER NOT NULL CHECK (rating IN (-1, 1)),
            comment         TEXT,
            created_at      TEXT NOT NULL
        );
        """,
    ),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Union[str, Path], *, base_dir: Path = BASE_DIR) -> None:
        self._keeper: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        if str(path) == ":memory:":
            # A named shared-cache in-memory database, kept alive by one open connection.
            self._target = f"file:chatbot_mem_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._is_memory = True
            self.path: Optional[Path] = None
        else:
            file_path = Path(path)
            if not file_path.is_absolute():
                file_path = base_dir / file_path
            self.path = file_path
            self._target = str(file_path)
            self._uri = False
            self._is_memory = False

    # ------------------------------------------------------------ lifecycle
    def initialize(self) -> None:
        """Create the file/folder if needed and apply any pending migrations."""
        try:
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._is_memory and self._keeper is None:
                self._keeper = sqlite3.connect(self._target, uri=True, check_same_thread=False)
            with self._connect() as conn:
                if not self._is_memory:
                    conn.execute("PRAGMA journal_mode=WAL")
                self._migrate(conn)
        except (sqlite3.Error, OSError) as exc:
            raise DatabaseError(
                f"Could not open or prepare the database ({type(exc).__name__}). "
                "Check DATABASE_PATH and folder permissions."
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._keeper is not None:
                self._keeper.close()
                self._keeper = None

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current > LATEST_VERSION:
            raise DatabaseError(
                f"The database is from a newer version of this app (schema {current}, "
                f"this app supports {LATEST_VERSION})."
            )
        for version, script in MIGRATIONS:
            if version > current:
                conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")

    @property
    def schema_version(self) -> int:
        with self._connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._target, uri=self._uri, timeout=5.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # --------------------------------------------------------- conversations
    @staticmethod
    def _touch_conversation(conn: sqlite3.Connection, conversation_id: str, now: str) -> None:
        conn.execute(
            "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
            (conversation_id, now, now),
        )

    def add_turn(
        self,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        *,
        latency_ms: Optional[int] = None,
    ) -> int:
        """Store a user message and its reply atomically. Returns the reply's id."""
        now = _now()
        with self._transaction() as conn:
            self._touch_conversation(conn, conversation_id, now)
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, status, created_at) "
                "VALUES (?, 'user', ?, 'ok', ?)",
                (conversation_id, user_text, now),
            )
            cursor = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, status, latency_ms, created_at) "
                "VALUES (?, 'assistant', ?, 'ok', ?, ?)",
                (conversation_id, assistant_text, latency_ms, now),
            )
            return int(cursor.lastrowid)

    def add_failed_user_message(self, conversation_id: str, user_text: str, error_code: str) -> None:
        now = _now()
        with self._transaction() as conn:
            self._touch_conversation(conn, conversation_id, now)
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, status, error_code, created_at) "
                "VALUES (?, 'user', ?, 'failed', ?, ?)",
                (conversation_id, user_text, error_code, now),
            )

    def count_messages(self, conversation_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            return int(row[0])

    def get_history(
        self, conversation_id: str, *, max_messages: int, max_chars: int
    ) -> List[Dict[str, str]]:
        """Most recent completed messages that fit both limits, oldest first.

        Walks backwards from the newest message and stops at the first one that would
        exceed ``max_chars``; the result never starts with an assistant message.
        Returns ``[{"role": "user"|"assistant", "content": str}, ...]``.
        """
        if max_messages <= 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE conversation_id = ? AND status = 'ok' ORDER BY id DESC LIMIT ?",
                (conversation_id, max_messages),
            ).fetchall()
        selected: List[Dict[str, str]] = []
        used = 0
        for row in rows:  # newest first
            size = len(row["content"])
            if used + size > max_chars:
                break
            used += size
            selected.append({"role": row["role"], "content": row["content"]})
        selected.reverse()
        while selected and selected[0]["role"] != "user":
            selected.pop(0)
        return selected

    def list_messages(self, conversation_id: str, *, limit: int) -> List[Dict[str, Any]]:
        """Completed messages for display, oldest first (the newest ``limit`` of them)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, created_at FROM messages "
                "WHERE conversation_id = ? AND status = 'ok' ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def delete_conversation(self, conversation_id: str) -> int:
        """Delete a conversation with its messages and feedback. Returns messages removed."""
        with self._transaction() as conn:
            removed = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()[0]
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            return int(removed)

    # -------------------------------------------------------------- feedback
    def set_feedback(
        self,
        conversation_id: str,
        message_id: int,
        rating: int,
        comment: Optional[str] = None,
    ) -> None:
        """Create or replace the feedback for one assistant message.

        Raises ``ValueError`` for a bad rating/comment and ``LookupError`` if the
        message is not a completed assistant message of that conversation.
        """
        if rating not in (-1, 1):
            raise ValueError("rating must be 1 (helpful) or -1 (not helpful)")
        if comment is not None:
            comment = comment.strip() or None
        if comment is not None and len(comment) > MAX_FEEDBACK_COMMENT_CHARS:
            raise ValueError(f"comment must be at most {MAX_FEEDBACK_COMMENT_CHARS} characters")
        now = _now()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ? "
                "AND role = 'assistant' AND status = 'ok'",
                (message_id, conversation_id),
            ).fetchone()
            if row is None:
                raise LookupError("no such assistant message in this conversation")
            conn.execute(
                "INSERT INTO feedback (message_id, conversation_id, rating, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(message_id) DO UPDATE SET rating = excluded.rating, "
                "comment = excluded.comment, created_at = excluded.created_at",
                (message_id, conversation_id, rating, comment, now),
            )

    def get_feedback(self, message_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT message_id, conversation_id, rating, comment, created_at "
                "FROM feedback WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return dict(row) if row else None
