"""Shared test helpers. Nothing here touches the network or a real API key."""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_provider import GeminiClient  # noqa: E402
from config import Config  # noqa: E402
from logging_setup import LOGGER_NAME  # noqa: E402
from server import create_app  # noqa: E402

# A fake key shaped like a real one. Assembled at runtime so that neither this source
# file nor its compiled .pyc contains the literal (the secret-scan test would flag it).
FAKE_KEY = "".join(("AI", "za", "x" * 35))
_NO_JSON = object()


class FakeResponse:
    def __init__(self, status_code=200, json_body=_NO_JSON, headers=None):
        self.status_code = status_code
        self._json = json_body
        self.headers = headers or {}

    def json(self):
        if self._json is _NO_JSON:
            raise ValueError("no JSON body")
        return self._json


class FakeSession:
    """Stands in for requests.Session. Records calls; returns/raises a canned result."""

    def __init__(self, result=None):
        self.result = result if result is not None else ok_response()
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def ok_response(text="Hello from the fake AI", finish_reason="STOP"):
    return FakeResponse(
        200,
        {
            "candidates": [
                {"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}
            ]
        },
    )


def make_config(**overrides):
    values = dict(
        gemini_api_key=FAKE_KEY,
        gemini_model="test-model",
        rate_limit_per_minute=0,
        api_rate_limit_per_minute=0,
        database_path=":memory:",
        log_file="",
        log_level="CRITICAL",
    )
    values.update(overrides)
    return Config(**values)


_OPEN_DATABASES = []


def make_app(config=None, session=None):
    config = config or make_config()
    session = session or FakeSession()
    client = GeminiClient(config.gemini_api_key, config.gemini_model, session=session)
    app = create_app(config, ai_client=client)
    _OPEN_DATABASES.append(app.extensions["chatbot_db"])
    return app, session


def make_app_with_client(config, client):
    """Like make_app, for tests that supply their own (fake) AI client object."""
    app = create_app(config, ai_client=client)
    _OPEN_DATABASES.append(app.extensions["chatbot_db"])
    return app


def reset_logging():
    """Per-test cleanup: close log files and in-memory databases.

    Closing files matters on Windows before temp folders are removed.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    while _OPEN_DATABASES:
        _OPEN_DATABASES.pop().close()
