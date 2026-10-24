import json
import tempfile
import unittest
from pathlib import Path

import requests

from helpers import (
    FAKE_KEY,
    FakeResponse,
    FakeSession,
    make_app,
    make_app_with_client,
    make_config,
    ok_response,
    reset_logging,
)

CHAT_PATHS = ("/api/v1/chat", "/chat")


def post_json(client, path, body, **kwargs):
    return client.post(path, data=json.dumps(body), content_type="application/json", **kwargs)


class ChatSuccessTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)

    def test_success_on_both_endpoints(self):
        for path in CHAT_PATHS:
            with self.subTest(path=path):
                app, session = make_app(session=FakeSession(ok_response("Hi there")))
                response = post_json(app.test_client(), path, {"message": "  hello  "})
                self.assertEqual(response.status_code, 200)
                body = response.get_json()
                self.assertEqual(body["reply"], "Hi there")
                self.assertEqual(body["ai"]["provider"], "gemini")
                self.assertEqual(body["request_id"], response.headers["X-Request-ID"])
                sent = session.calls[0][1]["json"]["contents"]
                self.assertEqual(sent, [{"role": "user", "parts": [{"text": "hello"}]}])

    def test_temperature_is_forwarded_or_defaulted(self):
        app, session = make_app()
        client = app.test_client()
        post_json(client, "/api/v1/chat", {"message": "a", "temperature": 0.2})
        post_json(client, "/api/v1/chat", {"message": "b"})
        temps = [c[1]["json"]["generationConfig"]["temperature"] for c in session.calls]
        self.assertEqual(temps, [0.2, 0.7])

    def test_request_id_header_is_echoed_only_if_well_formed(self):
        app, _ = make_app()
        client = app.test_client()
        good = post_json(client, "/api/v1/chat", {"message": "a"}, headers={"X-Request-ID": "abc-12345_XYZ"})
        self.assertEqual(good.headers["X-Request-ID"], "abc-12345_XYZ")
        bad = post_json(client, "/api/v1/chat", {"message": "a"}, headers={"X-Request-ID": "bad id!"})
        self.assertRegex(bad.headers["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_control_characters_are_stripped(self):
        app, session = make_app()
        post_json(app.test_client(), "/api/v1/chat", {"message": "hi\x00 there\x07"})
        self.assertEqual(session.calls[0][1]["json"]["contents"][0]["parts"][0]["text"], "hi there")

    def test_security_headers_and_no_store(self):
        app, _ = make_app()
        response = post_json(app.test_client(), "/api/v1/chat", {"message": "a"})
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)
        self.app, self.session = make_app(config=make_config(max_message_chars=1000, max_request_bytes=2048))
        self.client = self.app.test_client()

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], code)
        self.assertTrue(error["request_id"])
        self.assertEqual(self.session.calls, [], "invalid input must never reach the AI provider")

    def test_not_json_content_type(self):
        r = self.client.post("/api/v1/chat", data="message=hi", content_type="text/plain")
        self.assert_error(r, 415, "unsupported_media_type")

    def test_malformed_and_non_object_json(self):
        r = self.client.post("/api/v1/chat", data="{not json", content_type="application/json")
        self.assert_error(r, 400, "invalid_json")
        r = self.client.post("/api/v1/chat", data="[1,2]", content_type="application/json")
        self.assert_error(r, 400, "invalid_json")

    def test_message_problems(self):
        cases = [
            ({}, "empty_message"),
            ({"message": None}, "empty_message"),
            ({"message": ""}, "empty_message"),
            ({"message": "   \n\t "}, "empty_message"),
            ({"message": 123}, "invalid_message"),
            ({"message": ["a"]}, "invalid_message"),
            ({"message": "x" * 1001}, "message_too_long"),
        ]
        for body, code in cases:
            with self.subTest(body=str(body)[:30]):
                self.assert_error(post_json(self.client, "/api/v1/chat", body), 400, code)

    def test_message_at_exact_limit_is_accepted(self):
        r = post_json(self.client, "/api/v1/chat", {"message": "x" * 1000})
        self.assertEqual(r.status_code, 200)

    def test_temperature_problems(self):
        for value in (-0.1, 1.1, "0.5", True, [0.5]):
            with self.subTest(value=value):
                r = post_json(self.client, "/api/v1/chat", {"message": "hi", "temperature": value})
                self.assert_error(r, 400, "invalid_temperature")
        r = self.client.post(
            "/api/v1/chat", data='{"message":"hi","temperature":NaN}', content_type="application/json"
        )
        self.assert_error(r, 400, "invalid_temperature")

    def test_oversized_body_is_413(self):
        big = '{"message":"' + "a" * 5000 + '"}'
        r = self.client.post("/api/v1/chat", data=big, content_type="application/json")
        self.assert_error(r, 413, "payload_too_large")


class AiFailureTests(unittest.TestCase):
    """The core guarantee: an AI failure is never a 200 'reply'."""

    def setUp(self):
        self.addCleanup(reset_logging)

    def call(self, result, path="/api/v1/chat"):
        app, session = make_app(session=FakeSession(result))
        return post_json(app.test_client(), path, {"message": "hello"})

    def test_failures_use_error_statuses_without_a_reply(self):
        cases = [
            (FakeResponse(500, {"error": {"message": "x"}}), 502, "ai_upstream_error"),
            (FakeResponse(401, {}), 503, "ai_auth_error"),
            (FakeResponse(404, {}), 503, "ai_model_unavailable"),
            (FakeResponse(429, {}, headers={"Retry-After": "5"}), 503, "ai_rate_limited"),
            (requests.exceptions.Timeout(), 504, "ai_timeout"),
            (requests.exceptions.ConnectionError(), 502, "ai_unreachable"),
            (FakeResponse(200, {"candidates": []}), 502, "ai_empty_response"),
            (FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}), 422, "ai_blocked"),
        ]
        for result, status, code in cases:
            with self.subTest(code=code):
                response = self.call(result)
                self.assertEqual(response.status_code, status)
                body = response.get_json()
                self.assertEqual(body["error"]["code"], code)
                self.assertNotIn("reply", body)

    def test_legacy_endpoint_also_uses_error_status_and_adds_reply_text(self):
        response = self.call(FakeResponse(500, {}), path="/chat")
        self.assertEqual(response.status_code, 502)
        body = response.get_json()
        self.assertEqual(body["reply"], body["error"]["message"])  # old clients still get text

    def test_retry_after_header_is_forwarded(self):
        response = self.call(FakeResponse(429, {}, headers={"Retry-After": "5"}))
        self.assertEqual(response.headers["Retry-After"], "5")

    def test_upstream_details_and_key_never_leak_to_client(self):
        response = self.call(FakeResponse(500, {"error": {"message": "UPSTREAM-SECRET-DETAIL"}}))
        self.assertNotIn(b"UPSTREAM-SECRET-DETAIL", response.data)
        self.assertNotIn(FAKE_KEY.encode(), response.data)

    def test_missing_ai_configuration_returns_503_without_calling_provider(self):
        config = make_config(gemini_api_key="", gemini_model="")
        app, session = make_app(config=config)
        response = post_json(app.test_client(), "/api/v1/chat", {"message": "hi"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"], "ai_not_configured")
        self.assertEqual(session.calls, [])

    def test_unexpected_exception_becomes_generic_500(self):
        class Exploding:
            configured = True

            def generate(self, *args, **kwargs):
                raise RuntimeError("boom-internal-detail")

        app = make_app_with_client(make_config(), Exploding())
        with self.assertLogs("chatbot", level="ERROR"):
            response = post_json(app.test_client(), "/api/v1/chat", {"message": "hi"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"]["code"], "internal_error")
        self.assertNotIn(b"boom-internal-detail", response.data)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)

    def test_health_when_configured(self):
        app, session = make_app()
        for path in ("/api/v1/health", "/healthz"):
            with self.subTest(path=path):
                response = app.test_client().get(path)
                self.assertEqual(response.status_code, 200)
                body = response.get_json()
                self.assertEqual(body["status"], "ok")
                self.assertTrue(body["ai"]["configured"])
                self.assertNotIn(FAKE_KEY.encode(), response.data)
                self.assertNotIn(b"test-model", response.data)
        self.assertEqual(session.calls, [], "health must never call Gemini")

    def test_health_when_not_configured_is_degraded_but_200(self):
        app, _ = make_app(config=make_config(gemini_api_key="", gemini_model=""))
        response = app.test_client().get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "degraded")
        self.assertFalse(response.get_json()["ai"]["configured"])


class StaticAndErrorTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)
        self.client = make_app()[0].test_client()

    def test_home_page_and_assets_are_served(self):
        with self.client.get("/") as home:
            self.assertEqual(home.status_code, 200)
            self.assertIn(b"Dynamic AI Chatbot", home.data)
        for asset in ("/app.js", "/style.css"):
            with self.client.get(asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_public_assets_contain_no_secrets(self):
        with self.client.get("/app.js") as response:
            js = response.data
        self.assertNotIn(b"AIza", js)
        self.assertNotIn(b"apiKey", js)

    def test_server_side_files_are_not_served(self):
        for path in ("/server.py", "/config.py", "/.env", "/../config.py", "/%2e%2e/config.py", "/tests/helpers.py"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_unknown_path_is_json_404_with_request_id(self):
        response = self.client.get("/definitely-missing")
        self.assertEqual(response.status_code, 404)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "not_found")
        self.assertTrue(error["request_id"])


class CorsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)

    def preflight(self, client, origin):
        return client.options(
            "/api/v1/chat",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    def test_default_is_same_origin_only(self):
        client = make_app()[0].test_client()
        response = self.preflight(client, "https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_allowed_origin_gets_cors_headers(self):
        client = make_app(config=make_config(cors_allowed_origins=("https://app.example.com",)))[0].test_client()
        response = self.preflight(client, "https://app.example.com")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://app.example.com")
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", response.headers["Access-Control-Allow-Headers"])
        self.assertIn("Origin", response.headers["Vary"])

    def test_other_origin_is_not_allowed_even_when_a_list_exists(self):
        client = make_app(config=make_config(cors_allowed_origins=("https://app.example.com",)))[0].test_client()
        response = self.preflight(client, "https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertIn("Origin", response.headers["Vary"])


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)

    def test_limit_returns_429_with_retry_after_and_skips_provider(self):
        app, session = make_app(config=make_config(rate_limit_per_minute=2))
        client = app.test_client()
        statuses = [post_json(client, "/api/v1/chat", {"message": "hi"}).status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
        blocked = post_json(client, "/chat", {"message": "hi"})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.get_json()["error"]["code"], "rate_limited")
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
        self.assertEqual(len(session.calls), 2)

    def test_health_and_static_are_not_rate_limited(self):
        app, _ = make_app(config=make_config(rate_limit_per_minute=1))
        client = app.test_client()
        for _ in range(5):
            self.assertEqual(client.get("/healthz").status_code, 200)
            with client.get("/") as home:
                self.assertEqual(home.status_code, 200)

    def test_different_clients_have_separate_limits(self):
        app, _ = make_app(config=make_config(rate_limit_per_minute=1))
        client = app.test_client()
        a = post_json(client, "/api/v1/chat", {"message": "x"}, environ_base={"REMOTE_ADDR": "10.0.0.1"})
        b = post_json(client, "/api/v1/chat", {"message": "x"}, environ_base={"REMOTE_ADDR": "10.0.0.2"})
        again = post_json(client, "/api/v1/chat", {"message": "x"}, environ_base={"REMOTE_ADDR": "10.0.0.1"})
        self.assertEqual((a.status_code, b.status_code, again.status_code), (200, 200, 429))


class LoggingPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_logging)

    def test_log_file_has_request_lines_but_no_message_text_ip_or_key(self):
        secret_text = "my-very-private-message-98765"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "chatbot.log"
            config = make_config(log_file=str(log_path), log_level="INFO")
            session = FakeSession(FakeResponse(500, {"error": {"message": f"upstream {FAKE_KEY}"}}))
            app, _ = make_app(config=config, session=session)
            client = app.test_client()
            post_json(client, "/api/v1/chat", {"message": secret_text}, environ_base={"REMOTE_ADDR": "203.0.113.77"})
            client.get("/healthz")
            reset_logging()  # flush and release the file (needed on Windows)
            text = log_path.read_text(encoding="utf-8")
        self.assertIn("method=POST path='/api/v1/chat' status=502", text)
        self.assertIn("path='/healthz' status=200", text)
        self.assertIn("ai_upstream_error", text)
        self.assertNotIn(secret_text, text)
        self.assertNotIn("203.0.113.77", text)
        self.assertNotIn(FAKE_KEY, text)

    def test_log_injection_in_path_is_neutralised(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "chatbot.log"
            app, _ = make_app(config=make_config(log_file=str(log_path), log_level="INFO"))
            app.test_client().get("/x%0AFAKE-LOG-LINE%0D%0Astatus=200")
            reset_logging()
            lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(line.startswith("FAKE-LOG-LINE") for line in lines))


if __name__ == "__main__":
    unittest.main()
