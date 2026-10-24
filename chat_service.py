"""Chat orchestration: conversation memory + the AI provider.

Request handlers stay thin; this module decides what history the AI sees and
what gets stored. Later stages (intents, FAQ rules, fallbacks) plug in here.

Memory model
------------
* A conversation is identified by a client-supplied random UUID. Without one the
  request is stateless: nothing is read from or written to the database.
* The AI receives, oldest first, the most recent *completed* messages of that
  conversation (bounded by ``history_max_messages`` and ``history_max_chars``),
  followed by the new user message. "Relevant" here means "most recent that fit
  the budget"; there is no semantic search over older messages.
* A user message and its reply are stored together after the AI answers. If the
  AI fails, only the user message is stored, flagged failed, and it is never
  sent to the AI as context later.
* If storing fails AFTER the AI answered, the answer is still returned with
  ``persisted: false`` rather than being thrown away.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ai_provider import GeminiClient
from config import Config
from db import Database
from errors import AIError, ApiError
from logging_setup import LOGGER_NAME
from prompts import BASE_SYSTEM_PROMPT

logger = logging.getLogger(LOGGER_NAME)

_ROLE_TO_GEMINI = {"user": "user", "assistant": "model"}


@dataclass(frozen=True)
class ChatResult:
    reply: str
    finish_reason: Optional[str]
    latency_ms: int
    conversation_id: Optional[str]
    message_id: Optional[int]  # id of the stored assistant message (None if not stored)
    persisted: bool


def build_contents(history: List[Dict[str, str]], message: str) -> List[Dict[str, Any]]:
    """Turn stored history plus the new message into Gemini ``contents``."""
    contents: List[Dict[str, Any]] = [
        {"role": _ROLE_TO_GEMINI[m["role"]], "parts": [{"text": m["content"]}]} for m in history
    ]
    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def _storage_unavailable() -> ApiError:
    return ApiError(
        "storage_unavailable",
        "Conversation storage is temporarily unavailable. Please try again.",
        503,
    )


class ChatService:
    def __init__(self, client: GeminiClient, database: Database, config: Config) -> None:
        self._client = client
        self._db = database
        self._config = config

    def respond(
        self,
        message: str,
        temperature: float,
        conversation_id: Optional[str] = None,
        use_history: bool = True,
    ) -> ChatResult:
        history: List[Dict[str, str]] = []
        if conversation_id:
            try:
                if self._db.count_messages(conversation_id) >= self._config.max_conversation_messages:
                    raise ApiError(
                        "conversation_full",
                        "This conversation has reached its length limit. "
                        "Clear the chat to start a new one.",
                        409,
                    )
                if use_history:
                    history = self._db.get_history(
                        conversation_id,
                        max_messages=self._config.history_max_messages,
                        max_chars=self._config.history_max_chars,
                    )
            except sqlite3.Error as exc:
                logger.error("storage read failed: %s", type(exc).__name__)
                raise _storage_unavailable() from None

        try:
            result = self._client.generate(
                build_contents(history, message),
                temperature=temperature,
                system_instruction=BASE_SYSTEM_PROMPT,
            )
        except AIError as exc:
            if conversation_id:
                try:
                    self._db.add_failed_user_message(conversation_id, message, exc.code)
                except sqlite3.Error as db_exc:
                    logger.error("could not record failed turn: %s", type(db_exc).__name__)
            raise

        message_id: Optional[int] = None
        persisted = False
        if conversation_id:
            try:
                message_id = self._db.add_turn(
                    conversation_id, message, result.text, latency_ms=result.latency_ms
                )
                persisted = True
            except sqlite3.Error as exc:
                logger.error("could not store turn (reply still returned): %s", type(exc).__name__)

        return ChatResult(
            reply=result.text,
            finish_reason=result.finish_reason,
            latency_ms=result.latency_ms,
            conversation_id=conversation_id,
            message_id=message_id,
            persisted=persisted,
        )
