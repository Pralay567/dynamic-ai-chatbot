import logging
import sys
import unittest

from logging_setup import REDACTED, RedactingFormatter, redact

from helpers import FAKE_KEY


class RedactTests(unittest.TestCase):
    def test_google_style_key_is_redacted(self):
        self.assertNotIn(FAKE_KEY, redact(f"failed with {FAKE_KEY} here"))

    def test_url_key_parameter_and_header_are_redacted(self):
        out = redact("GET https://x.test/v1?alt=json&key=SECRETVALUE123&z=1")
        self.assertNotIn("SECRETVALUE123", out)
        self.assertIn("z=1", out)
        out = redact("headers={'x-goog-api-key': 'ANOTHERSECRET99'}")
        self.assertNotIn("ANOTHERSECRET99", out)

    def test_configured_secret_in_any_format_is_redacted(self):
        secret = "not-google-shaped-secret-123"
        self.assertEqual(redact(f"a {secret} b", [secret]), f"a {REDACTED} b")

    def test_tiny_or_empty_secrets_are_ignored(self):
        self.assertEqual(redact("abc abc", ["abc", ""]), "abc abc")

    def test_formatter_redacts_tracebacks(self):
        formatter = RedactingFormatter("%(message)s", secrets=["tb-secret-value-1"])
        try:
            raise RuntimeError("boom tb-secret-value-1 and " + FAKE_KEY)
        except RuntimeError:
            record = logging.LogRecord(
                "t", logging.ERROR, __file__, 1, "failure %s", ("tb-secret-value-1",), sys.exc_info()
            )
        text = formatter.format(record)
        self.assertNotIn("tb-secret-value-1", text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertIn("RuntimeError", text)


if __name__ == "__main__":
    unittest.main()
