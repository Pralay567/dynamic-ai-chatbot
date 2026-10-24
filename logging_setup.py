"""Logging configuration with secret redaction.

Rules this module enforces:
  * Any text that looks like a Google API key is replaced before it is written.
  * The configured API key (whatever its format) is also replaced.
  * Tracebacks pass through the same redaction.

Application code additionally never logs user message text or client IPs.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from config import BASE_DIR, Config

LOGGER_NAME = "chatbot"
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
_HEADER_RE = re.compile(r"(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.IGNORECASE)
_KEY_PARAM_RE = re.compile(r"([?&]key=)[^&\s'\"]+", re.IGNORECASE)
REDACTED = "[REDACTED]"


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    text = _GOOGLE_KEY_RE.sub(REDACTED, text)
    text = _HEADER_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _KEY_PARAM_RE.sub(lambda m: m.group(1) + REDACTED, text)
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, REDACTED)
    return text


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: Iterable[str] = ()) -> None:
        super().__init__(fmt)
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets)


def configure_logging(config: Config) -> logging.Logger:
    """Configure the ``chatbot`` logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, config.log_level))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        secrets=[config.gemini_api_key],
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    if config.log_file:
        path = Path(config.log_file)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Keep third-party HTTP libraries from logging request details.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logger
