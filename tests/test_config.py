import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config as config_module
from config import Config, ConfigError, load_config

from helpers import FAKE_KEY


class ConfigTests(unittest.TestCase):
    def test_defaults_are_safe(self):
        cfg = Config.from_env({})
        self.assertFalse(cfg.ai_configured)
        self.assertEqual(cfg.gemini_model, "")  # no built-in model name
        self.assertFalse(cfg.debug)
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.cors_allowed_origins, ())
        self.assertEqual(cfg.rate_limit_per_minute, 20)
        self.assertEqual(cfg.max_message_chars, 1000)

    def test_parses_values(self):
        cfg = Config.from_env(
            {
                "GEMINI_API_KEY": FAKE_KEY,
                "GEMINI_MODEL": "models/some-model-1",
                "PORT": "8080",
                "RATE_LIMIT_PER_MINUTE": "0",
                "CORS_ALLOWED_ORIGINS": "https://a.example.com/, http://localhost:3000",
                "APP_DEBUG": "true",
                "LOG_LEVEL": "debug",
            }
        )
        self.assertTrue(cfg.ai_configured)
        self.assertEqual(cfg.gemini_model, "some-model-1")  # "models/" prefix removed
        self.assertEqual(cfg.port, 8080)
        self.assertEqual(cfg.rate_limit_per_minute, 0)
        self.assertEqual(cfg.cors_allowed_origins, ("https://a.example.com", "http://localhost:3000"))
        self.assertTrue(cfg.debug)
        self.assertEqual(cfg.log_level, "DEBUG")

    def test_ai_not_configured_without_model_or_key(self):
        self.assertFalse(Config.from_env({"GEMINI_API_KEY": FAKE_KEY}).ai_configured)
        self.assertFalse(Config.from_env({"GEMINI_MODEL": "m"}).ai_configured)

    def test_rejects_bad_model_name(self):
        for bad in ("a/b", "x y", "m?key=1", "../etc", "m:generate"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                Config.from_env({"GEMINI_MODEL": bad})

    def test_rejects_wildcard_and_malformed_origins(self):
        for bad in ("*", "example.com", "https://example.com/path", "ftp://x.com"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                Config.from_env({"CORS_ALLOWED_ORIGINS": bad})

    def test_debug_requires_loopback_host(self):
        with self.assertRaises(ConfigError):
            Config.from_env({"APP_DEBUG": "true", "HOST": "0.0.0.0"})
        Config.from_env({"APP_DEBUG": "true", "HOST": "localhost"})  # allowed

    def test_rejects_invalid_numbers_without_echoing_value(self):
        for name, value in (
            ("PORT", "abc"),
            ("PORT", "70000"),
            ("GEMINI_TIMEOUT_SECONDS", "0.25"),
            ("RATE_LIMIT_PER_MINUTE", "-1"),
            ("DEFAULT_TEMPERATURE", "2"),
            ("APP_DEBUG", "maybe"),
            ("LOG_LEVEL", "LOUD"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ConfigError) as ctx:
                    Config.from_env({name: value})
                self.assertNotIn(value, str(ctx.exception))

    def test_storage_and_memory_settings(self):
        cfg = Config.from_env({})
        self.assertEqual(
            (cfg.database_path, cfg.history_max_messages, cfg.history_max_chars, cfg.max_conversation_messages, cfg.api_rate_limit_per_minute),
            ("data/chatbot.db", 20, 12000, 400, 120),
        )
        cfg = Config.from_env(
            {"DATABASE_PATH": "x/y.db", "HISTORY_MAX_MESSAGES": "0", "HISTORY_MAX_CHARS": "2000",
             "MAX_CONVERSATION_MESSAGES": "10", "API_RATE_LIMIT_PER_MINUTE": "0"}
        )
        self.assertEqual(
            (cfg.database_path, cfg.history_max_messages, cfg.history_max_chars, cfg.max_conversation_messages, cfg.api_rate_limit_per_minute),
            ("x/y.db", 0, 2000, 10, 0),
        )
        for name, value in (("HISTORY_MAX_MESSAGES", "-1"), ("HISTORY_MAX_MESSAGES", "500"), ("HISTORY_MAX_CHARS", "10"),
                            ("MAX_CONVERSATION_MESSAGES", "1"), ("API_RATE_LIMIT_PER_MINUTE", "x")):
            with self.subTest(name=name, value=value), self.assertRaises(ConfigError):
                Config.from_env({name: value})

    def test_repr_hides_the_key(self):
        self.assertNotIn(FAKE_KEY, repr(Config.from_env({"GEMINI_API_KEY": FAKE_KEY})))

    def test_load_config_with_explicit_env_skips_dotenv(self):
        cfg = load_config({"GEMINI_MODEL": "explicit-model"})
        self.assertEqual(cfg.gemini_model, "explicit-model")

    def test_load_config_reads_dotenv_and_real_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text(
                "GEMINI_MODEL=from-dotenv\nPORT=6001\n", encoding="utf-8"
            )
            with mock.patch.object(config_module, "BASE_DIR", Path(tmp)), mock.patch.dict(
                os.environ, {"PORT": "7002"}, clear=False
            ):
                os.environ.pop("GEMINI_MODEL", None)
                cfg = load_config()
        self.assertEqual(cfg.gemini_model, "from-dotenv")
        self.assertEqual(cfg.port, 7002)  # real environment variable overrides .env


if __name__ == "__main__":
    unittest.main()
