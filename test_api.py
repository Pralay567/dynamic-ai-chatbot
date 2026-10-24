"""
Manual LIVE smoke test for your Gemini configuration.

This sends ONE real request to Google using the key and model from your
environment / .env file. It is NOT part of the automated test suite (those are in
the tests/ folder and never touch the network), and it is deliberately named
without a ``test_`` function so test runners do not collect it.

Usage:
    python test_api.py
"""

import sys

from ai_provider import GeminiClient
from config import ConfigError, load_config
from errors import AIError


def run_live_check() -> bool:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"❌ Configuration error: {exc}")
        return False

    if not config.ai_configured:
        print("❌ GEMINI_API_KEY and GEMINI_MODEL must both be set (see .env.example).")
        return False

    print("🧪 Testing Gemini configuration (one live request)...")
    print("🔑 API key: set (value not shown)")
    print(f"🤖 Model: {config.gemini_model}")

    client = GeminiClient(
        config.gemini_api_key,
        config.gemini_model,
        timeout=config.gemini_timeout,
        max_output_tokens=64,
    )
    try:
        result = client.generate(
            [{"role": "user", "parts": [{"text": 'Reply with exactly: "API working perfectly!"'}]}],
            temperature=0.0,
        )
    except AIError as exc:
        print(f"❌ API FAILED: {exc.code} (HTTP {exc.status})")
        print(f"   Detail: {exc.detail}")
        return False

    print(f"✅ API SUCCESS in {result.latency_ms} ms")
    print(f"🤖 AI response: {result.text}")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_live_check() else 1)
