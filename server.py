
"""Flask backend for the Dynamic AI Chatbot.

Endpoints
---------
GET  /                              Web interface (static/index.html)
POST /api/v1/chat                   Chat API
POST /chat                          Legacy chat alias
GET  /api/v1/health                 Health check
GET  /healthz                       Health check alias

Run locally with ``python run.py`` or ``python server.py``.
For production WSGI, point the server at ``wsgi:app``.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from ai_provider import GeminiClient, OpenRouterClient
from chat_service import ChatService
from config import APP_VERSION, Config, ConfigError, load_config
from db import Database, DatabaseError
from errors import ApiError
from logging_setup import configure_logging
from ratelimit import SlidingWindowLimiter


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LEGACY_CHAT_PATH = "/chat"
def get_current_datetime_context() -> str:
    """Generate fresh Indian date/time context for the AI."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))

    return (
        "\n\n[CURRENT DATE AND TIME INFORMATION]\n"
        f"Current date: {now.strftime('%A, %d %B %Y')}\n"
        f"Current time: {now.strftime('%I:%M:%S %p')}\n"
        "Timezone: India Standard Time (Asia/Kolkata, UTC+05:30)\n"
        "When asked about today's date, the current day, "
        "or the current time in India, use this information. "
        "Do not claim that the current date or time is unavailable.\n"
        "[END DATE AND TIME INFORMATION]"
    )

_HTTP_ERROR_CODES = {
    400: ("bad_request", "The request could not be understood."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "That method is not allowed for this URL."),
    413: ("payload_too_large", "The request is too large."),
    415: (
        "unsupported_media_type",
        "Send the request as JSON with Content-Type: application/json.",
    ),
}

_CONVERSATION_PATH_RE = re.compile(r"(/api/v1/conversations/)([^/]+)")


# ---------------------------------------------------------------- validation

def _read_json_object() -> Dict[str, Any]:
    if not request.is_json:
        raise ApiError(
            "unsupported_media_type",
            "Send the request as JSON with Content-Type: application/json.",
            415,
        )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(
            "invalid_json",
            "The request body must be a JSON object.",
            400,
        )

    return data


def _validate_message(value: Any, max_chars: int) -> str:
    if value is None:
        raise ApiError("empty_message", "Please enter a message.", 400)

    if not isinstance(value, str):
        raise ApiError("invalid_message", "'message' must be a string.", 400)

    cleaned = _CONTROL_CHARS_RE.sub("", value).strip()

    if not cleaned:
        raise ApiError("empty_message", "Please enter a message.", 400)

    if len(cleaned) > max_chars:
        raise ApiError(
            "message_too_long",
            f"Messages can be at most {max_chars} characters.",
            400,
        )

    return cleaned


def _validate_temperature(value: Any, default: float) -> float:
    if value is None:
        return default

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(
            "invalid_temperature",
            "'temperature' must be a number.",
            400,
        )

    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ApiError(
            "invalid_temperature",
            "'temperature' must be between 0 and 1.",
            400,
        )

    return float(value)


def _validate_conversation_id(value: Any) -> str:
    """Accept only a random version-4 UUID."""
    if not isinstance(value, str) or len(value) > 64:
        raise ApiError(
            "invalid_conversation_id",
            "'conversation_id' must be a UUID string.",
            400,
        )

    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ApiError(
            "invalid_conversation_id",
            "'conversation_id' must be a UUID string.",
            400,
        ) from None

    if parsed.version != 4:
        raise ApiError(
            "invalid_conversation_id",
            "'conversation_id' must be a random (version 4) UUID.",
            400,
        )

    return str(parsed)


def _validate_use_history(value: Any) -> bool:
    if value is None:
        return True

    if not isinstance(value, bool):
        raise ApiError(
            "invalid_use_history",
            "'use_history' must be true or false.",
            400,
        )

    return value


def _conversation_ref(conversation_id: str) -> str:
    """Return a short, non-secret reference for logging."""
    return conversation_id[:8]


def _redact_conversation_id(path: str) -> str:
    """Avoid logging full conversation IDs."""
    return _CONVERSATION_PATH_RE.sub(
        lambda match: match.group(1) + match.group(2)[:8] + "...",
        path,
    )


# ---------------------------------------------------------------- app factory

def create_app(
    config: Optional[Config] = None,
    ai_client: Optional[Any] = None,
    database: Optional[Database] = None,
) -> Flask:
    config = config or load_config()
    logger = configure_logging(config)

    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = config.max_request_bytes

    if config.trusted_proxy_count:
        proxy_count = config.trusted_proxy_count
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_count,
            x_proto=proxy_count,
            x_host=proxy_count,
        )  # type: ignore[method-assign]

    # Preserve injected clients used by tests and integrations.
    # Otherwise, select the provider configured through AI_PROVIDER.
    provider_name = getattr(config, "ai_provider", "gemini").lower()

    if ai_client is not None:
        client = ai_client

    elif provider_name == "openrouter":
        client = OpenRouterClient(
            config.openrouter_api_key,
            config.openrouter_model,
            timeout=config.gemini_timeout,
            max_output_tokens=config.gemini_max_output_tokens,
        )

    else:
        client = GeminiClient(
            config.gemini_api_key,
            config.gemini_model,
            timeout=config.gemini_timeout,
            max_output_tokens=config.gemini_max_output_tokens,
        )

    limiter = SlidingWindowLimiter(config.rate_limit_per_minute)
    api_limiter = SlidingWindowLimiter(config.api_rate_limit_per_minute)

    db = database if database is not None else Database(config.database_path)
    db.initialize()

    service = ChatService(client, db, config)

    app.extensions["chatbot_config"] = config
    app.extensions["chatbot_limiter"] = limiter
    app.extensions["chatbot_api_limiter"] = api_limiter
    app.extensions["chatbot_db"] = db

    if not client.configured:
        logger.warning(
            "AI provider is not configured: check the API key and model "
            "for the selected provider %s. Chat requests will return HTTP 503.",
            provider_name,
        )

    # ------------------------------------------------------------ hooks

    @app.before_request
    def _start_request() -> None:
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = (
            incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        )
        g.started = time.perf_counter()

    @app.after_request
    def _finish_request(response):
        request_id = getattr(g, "request_id", None)

        if request_id:
            response.headers["X-Request-ID"] = request_id

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")

        if response.mimetype == "application/json":
            response.headers["Cache-Control"] = "no-store"

        origin = request.headers.get("Origin")
        if origin:
            response.headers.add("Vary", "Origin")

            if origin in config.cors_allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Expose-Headers"] = (
                    "X-Request-ID, Retry-After"
                )

                if request.method == "OPTIONS":
                    response.headers["Access-Control-Allow-Methods"] = (
                        "GET, POST, DELETE, OPTIONS"
                    )
                    response.headers["Access-Control-Allow-Headers"] = (
                        "Content-Type, X-Request-ID"
                    )
                    response.headers["Access-Control-Max-Age"] = "600"

        started = getattr(g, "started", None)
        duration_ms = (
            int((time.perf_counter() - started) * 1000)
            if started is not None
            else -1
        )

        level = logging.DEBUG if request.endpoint == "static" else logging.INFO
        conv_ref = getattr(g, "conv_ref", None)

        logger.log(
            level,
            "request_id=%s method=%s path=%r status=%s duration_ms=%s%s",
            request_id,
            request.method,
            _redact_conversation_id(request.path),
            response.status_code,
            duration_ms,
            f" conv={conv_ref}" if conv_ref else "",
        )

        return response

    # ------------------------------------------------------------ errors

    def _error_response(
        code: str,
        message: str,
        status: int,
        headers=None,
    ):
        body: Dict[str, Any] = {
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(g, "request_id", None),
            }
        }

        if request.path == _LEGACY_CHAT_PATH:
            body["reply"] = message

        response = jsonify(body)
        response.status_code = status

        for key, value in (headers or {}).items():
            response.headers[key] = value

        return response

    @app.errorhandler(ApiError)
    def _handle_api_error(exc: ApiError):
        log = logger.error if exc.status >= 500 else logger.info

        log(
            "request_id=%s api_error code=%s status=%s detail=%s",
            getattr(g, "request_id", None),
            exc.code,
            exc.status,
            exc.detail or "-",
        )

        return _error_response(
            exc.code,
            exc.message,
            exc.status,
            exc.headers,
        )

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):
        status = exc.code or 500
        code, message = _HTTP_ERROR_CODES.get(
            status,
            (
                exc.name.lower().replace(" ", "_"),
                "The request could not be completed.",
            ),
        )

        headers = {}
        valid_methods = getattr(exc, "valid_methods", None)

        if status == 405 and valid_methods:
            headers["Allow"] = ", ".join(valid_methods)

        return _error_response(code, message, status, headers)

    @app.errorhandler(sqlite3.Error)
    def _handle_storage_error(exc: sqlite3.Error):
        logger.error(
            "request_id=%s storage error: %s",
            getattr(g, "request_id", None),
            type(exc).__name__,
        )

        return _error_response(
            "storage_unavailable",
            "Conversation storage is temporarily unavailable. Please try again.",
            503,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        logger.exception(
            "request_id=%s unhandled error",
            getattr(g, "request_id", None),
        )

        return _error_response(
            "internal_error",
            "Something went wrong on the server. Please try again.",
            500,
        )

    # ------------------------------------------------------------ routes

    @app.route("/")
    def home():
        return app.send_static_file("index.html")

    @app.route("/api/v1/health")
    @app.route("/healthz")
    def health():
        return jsonify(
            {
                "status": "ok" if client.configured else "degraded",
                "version": APP_VERSION,
                "ai": {
                    "provider": provider_name,
                    "configured": client.configured,
                },
                "time": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
        )

    def _enforce_rate_limit(which: SlidingWindowLimiter) -> None:
        allowed, retry_after = which.check(
            request.remote_addr or "unknown"
        )

        if not allowed:
            raise ApiError(
                "rate_limited",
                "Too many requests. Please wait a moment and try again.",
                429,
                headers={"Retry-After": str(retry_after)},
                detail=f"rate limit exceeded; retry_after={retry_after}",
            )

    @app.route("/api/v1/chat", methods=["POST"])
    @app.route(_LEGACY_CHAT_PATH, methods=["POST"])
    def chat():
        _enforce_rate_limit(limiter)

        payload = _read_json_object()
        message = _validate_message(
            payload.get("message"),
            config.max_message_chars,
        )
        temperature = _validate_temperature(
            payload.get("temperature"),
            config.default_temperature,
        )
        use_history = _validate_use_history(payload.get("use_history"))

        conversation_id = None

        if payload.get("conversation_id") is not None:
            conversation_id = _validate_conversation_id(
                payload["conversation_id"]
            )
            g.conv_ref = _conversation_ref(conversation_id)

        result = service.respond(
            message,
            temperature,
            conversation_id=conversation_id,
            use_history=use_history,
        )

        return jsonify(
            {
                "reply": result.reply,
                "request_id": g.request_id,
                "latency_ms": result.latency_ms,
                "conversation_id": result.conversation_id,
                "message_id": result.message_id,
                "persisted": result.persisted,
                "ai": {
                    "provider": provider_name,
                    "finish_reason": result.finish_reason,
                },
            }
        )

    @app.route(
        "/api/v1/conversations/<conversation_id>/messages",
        methods=["GET"],
    )
    def conversation_messages(conversation_id: str):
        _enforce_rate_limit(api_limiter)

        conversation_id = _validate_conversation_id(conversation_id)
        g.conv_ref = _conversation_ref(conversation_id)

        raw_limit = request.args.get("limit", "200")

        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            limit = 0

        if not 1 <= limit <= config.max_conversation_messages:
            raise ApiError(
                "invalid_limit",
                "'limit' must be a whole number between 1 and "
                f"{config.max_conversation_messages}.",
                400,
            )

        messages = db.list_messages(conversation_id, limit=limit)

        if not messages:
            raise ApiError(
                "conversation_not_found",
                "No such conversation.",
                404,
            )

        return jsonify(
            {
                "conversation_id": conversation_id,
                "messages": messages,
            }
        )

    @app.route(
        "/api/v1/conversations/<conversation_id>",
        methods=["DELETE"],
    )
    def delete_conversation(conversation_id: str):
        _enforce_rate_limit(api_limiter)

        conversation_id = _validate_conversation_id(conversation_id)
        g.conv_ref = _conversation_ref(conversation_id)

        removed = db.delete_conversation(conversation_id)

        logger.info(
            "request_id=%s conversation deleted conv=%s messages_removed=%s",
            g.request_id,
            g.conv_ref,
            removed,
        )

        return Response(status=204)

    return app


# Lazy app creation for ``from server import app`` and WSGI servers.
_default_app: Optional[Flask] = None


def __getattr__(name: str):
    global _default_app

    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app

    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        app = create_app(config)
    except DatabaseError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 2

    app.run(
        host=config.host,
        port=config.port,
        debug=config.debug,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
