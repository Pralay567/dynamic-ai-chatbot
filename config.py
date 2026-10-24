"""Application configuration.

All settings come from environment variables. For local development they can
also be placed in a ``.env`` file next to this module (see ``.env.example``).
Real environment variables always win over ``.env`` values.

Secrets (the Gemini and OpenRouter API keys) are never given a default,
never printed, and are excluded from ``repr()`` of the config object.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

APP_VERSION = "0.2.0"
BASE_DIR = Path(__file__).resolve().parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_OPENROUTER_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


class ConfigError(ValueError):
    """Raised when configuration is invalid. Messages never include secrets."""


def _get_str(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name)
    return default if value is None else value.strip()


def _get_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    lo: int,
    hi: int,
) -> int:
    raw = _get_str(env, name)

    if raw == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number") from None

    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")

    return value


def _get_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    lo: float,
    hi: float,
) -> float:
    raw = _get_str(env, name)

    if raw == "":
        return default

    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None

    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")

    return value


def _get_bool(
    env: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = _get_str(env, name).lower()

    if name not in env or raw == "":
        return default

    if raw in _TRUE:
        return True

    if raw in _FALSE:
        return False

    raise ConfigError(f"{name} must be true or false")


def _parse_origins(raw: str) -> Tuple[str, ...]:
    origins = []

    for item in raw.split(","):
        item = item.strip().rstrip("/")

        if not item:
            continue

        if item == "*":
            raise ConfigError(
                "CORS_ALLOWED_ORIGINS must list explicit origins; '*' is not "
                "allowed because this API spends your Gemini quota"
            )

        if not _ORIGIN_RE.fullmatch(item):
            raise ConfigError(
                "CORS_ALLOWED_ORIGINS entries must look like "
                "https://example.com or http://localhost:3000 (no path)"
            )

        origins.append(item)

    return tuple(origins)


@dataclass(frozen=True)
class Config:
    # --- AI provider settings ---
    ai_provider: str = "gemini"

    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = ""
    gemini_timeout: float = 20.0

    openrouter_api_key: str = field(default="", repr=False)
    openrouter_model: str = "openrouter/free"

    gemini_max_output_tokens: int = 1000
    default_temperature: float = 0.7

    # --- request limits ---
    max_message_chars: int = 1000
    max_request_bytes: int = 16 * 1024
    rate_limit_per_minute: int = 20
    api_rate_limit_per_minute: int = 120

    # --- storage and conversation memory ---
    database_path: str = "data/chatbot.db"
    max_conversation_messages: int = 400
    history_max_messages: int = 20
    history_max_chars: int = 12000

    # --- web server ---
    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False
    cors_allowed_origins: Tuple[str, ...] = ()
    trusted_proxy_count: int = 0

    # --- logging ---
    log_level: str = "INFO"
    log_file: str = ""

    @property
    def ai_configured(self) -> bool:
        if self.ai_provider == "openrouter":
            return bool(self.openrouter_api_key and self.openrouter_model)

        return bool(self.gemini_api_key and self.gemini_model)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        provider = (
            _get_str(env, "AI_PROVIDER", "gemini") or "gemini"
        ).lower()

        if provider not in {"gemini", "openrouter"}:
            raise ConfigError(
                "AI_PROVIDER must be either gemini or openrouter"
            )

        model = _get_str(env, "GEMINI_MODEL")

        if model.startswith("models/"):
            model = model[len("models/"):]

        if model and not _MODEL_RE.fullmatch(model):
            raise ConfigError(
                "GEMINI_MODEL contains unsupported characters"
            )

        openrouter_model = (
            _get_str(env, "OPENROUTER_MODEL", "openrouter/free")
            or "openrouter/free"
        )

        if not _OPENROUTER_MODEL_RE.fullmatch(openrouter_model):
            raise ConfigError(
                "OPENROUTER_MODEL contains unsupported characters"
            )

        host = _get_str(env, "HOST", "127.0.0.1") or "127.0.0.1"
        debug = _get_bool(env, "APP_DEBUG", False)

        if debug and host not in _LOOPBACK_HOSTS:
            raise ConfigError(
                "APP_DEBUG=true is only allowed when HOST is a loopback "
                "address (the Flask debugger allows code execution)"
            )

        level = (_get_str(env, "LOG_LEVEL", "INFO") or "INFO").upper()

        if level not in {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ConfigError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL"
            )

        return cls(
            ai_provider=provider,
            gemini_api_key=_get_str(env, "GEMINI_API_KEY"),
            gemini_model=model,
            gemini_timeout=_get_float(
                env, "GEMINI_TIMEOUT_SECONDS", 20.0, 1.0, 120.0
            ),
            openrouter_api_key=_get_str(env, "OPENROUTER_API_KEY"),
            openrouter_model=openrouter_model,
            gemini_max_output_tokens=_get_int(
                env, "GEMINI_MAX_OUTPUT_TOKENS", 1000, 16, 8192
            ),
            default_temperature=_get_float(
                env, "DEFAULT_TEMPERATURE", 0.7, 0.0, 1.0
            ),
            max_message_chars=_get_int(
                env, "MAX_MESSAGE_CHARS", 1000, 1, 8000
            ),
            max_request_bytes=_get_int(
                env, "MAX_REQUEST_BYTES", 16 * 1024, 1024, 1024 * 1024
            ),
            rate_limit_per_minute=_get_int(
                env, "RATE_LIMIT_PER_MINUTE", 20, 0, 10000
            ),
            api_rate_limit_per_minute=_get_int(
                env, "API_RATE_LIMIT_PER_MINUTE", 120, 0, 100000
            ),
            database_path=(
                _get_str(env, "DATABASE_PATH", "data/chatbot.db")
                or "data/chatbot.db"
            ),
            max_conversation_messages=_get_int(
                env, "MAX_CONVERSATION_MESSAGES", 400, 2, 5000
            ),
            history_max_messages=_get_int(
                env, "HISTORY_MAX_MESSAGES", 20, 0, 200
            ),
            history_max_chars=_get_int(
                env, "HISTORY_MAX_CHARS", 12000, 1000, 200000
            ),
            host=host,
            port=_get_int(env, "PORT", 5000, 1, 65535),
            debug=debug,
            cors_allowed_origins=_parse_origins(
                _get_str(env, "CORS_ALLOWED_ORIGINS")
            ),
            trusted_proxy_count=_get_int(
                env, "TRUSTED_PROXY_COUNT", 0, 0, 10
            ),
            log_level=level,
            log_file=_get_str(env, "LOG_FILE", "logs/chatbot.log"),
        )


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    """Build a Config.

    With no argument, reads ``.env`` (if present) and then ``os.environ``.
    Passing ``env`` explicitly (used by tests) skips ``.env`` entirely.
    """
    if env is None:
        dotenv_file = BASE_DIR / ".env"

        if dotenv_file.exists():
            try:
                from dotenv import load_dotenv
            except ImportError:
                raise ConfigError(
                    "A .env file exists but python-dotenv is not installed. "
                    "Run: pip install -r requirements.txt"
                ) from None

            load_dotenv(dotenv_file, override=False)

        env = os.environ

    return Config.from_env(env)