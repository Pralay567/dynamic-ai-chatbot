import unittest

import requests

from ai_provider import GeminiClient
from errors import AIError

from helpers import FAKE_KEY, FakeResponse, FakeSession, ok_response


def client_with(result, **kwargs):
    session = FakeSession(result)
    return GeminiClient(FAKE_KEY, "test-model", session=session, **kwargs), session


USER = [{"role": "user", "parts": [{"text": "hi"}]}]


class RequestShapeTests(unittest.TestCase):
    def test_key_is_sent_in_header_never_in_url_or_body(self):
        client, session = client_with(ok_response())
        client.generate(USER, temperature=0.3)
        url, kwargs = session.calls[0]
        self.assertEqual(
            url,
            "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
        )
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], FAKE_KEY)
        self.assertNotIn("key=", url)
        self.assertNotIn(FAKE_KEY, str(kwargs["json"]))
        self.assertIn("timeout", kwargs)

    def test_payload_contents_and_generation_config(self):
        client, session = client_with(ok_response(), max_output_tokens=321)
        client.generate(USER, temperature=0.3)
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["contents"], USER)
        self.assertEqual(payload["generationConfig"]["temperature"], 0.3)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 321)
        self.assertNotIn("systemInstruction", payload)

    def test_system_instruction_included_when_given(self):
        client, session = client_with(ok_response())
        client.generate(USER, temperature=0.5, system_instruction="Be brief.")
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "Be brief."}]})

    def test_not_configured_makes_no_request(self):
        session = FakeSession()
        for key, model in (("", "m"), (FAKE_KEY, ""), ("", "")):
            with self.subTest(key=bool(key), model=model):
                client = GeminiClient(key, model, session=session)
                with self.assertRaises(AIError) as ctx:
                    client.generate(USER, temperature=0.5)
                self.assertEqual(ctx.exception.code, "ai_not_configured")
                self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(session.calls, [])

    def test_repr_hides_key(self):
        client, _ = client_with(ok_response())
        self.assertNotIn(FAKE_KEY, repr(client))


class SuccessParsingTests(unittest.TestCase):
    def test_returns_text_and_metadata(self):
        client, _ = client_with(ok_response("  Answer  ", "STOP"))
        result = client.generate(USER, temperature=0.5)
        self.assertEqual(result.text, "Answer")
        self.assertEqual(result.finish_reason, "STOP")
        self.assertEqual(result.model, "test-model")
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_joins_text_parts_and_skips_thought_parts(self):
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "hidden", "thought": True},
                            {"text": "Hello "},
                            {"text": "world"},
                        ]
                    }
                }
            ]
        }
        client, _ = client_with(FakeResponse(200, body))
        self.assertEqual(client.generate(USER, temperature=0.5).text, "Hello world")

    def test_truncated_answer_is_returned_with_finish_reason(self):
        client, _ = client_with(ok_response("partial", "MAX_TOKENS"))
        result = client.generate(USER, temperature=0.5)
        self.assertEqual((result.text, result.finish_reason), ("partial", "MAX_TOKENS"))

    def assert_error(self, result, code, status):
        client, _ = client_with(result)
        with self.assertRaises(AIError) as ctx:
            client.generate(USER, temperature=0.5)
        self.assertEqual((ctx.exception.code, ctx.exception.status), (code, status))
        return ctx.exception

    def test_bad_success_bodies_are_errors_not_replies(self):
        self.assert_error(FakeResponse(200), "ai_bad_response", 502)  # not JSON
        self.assert_error(FakeResponse(200, ["x"]), "ai_bad_response", 502)  # wrong shape
        self.assert_error(FakeResponse(200, {}), "ai_empty_response", 502)
        self.assert_error(FakeResponse(200, {"candidates": []}), "ai_empty_response", 502)
        self.assert_error(
            FakeResponse(200, {"candidates": [{"content": {"parts": []}}]}),
            "ai_empty_response",
            502,
        )

    def test_safety_blocks_are_reported_as_blocked(self):
        self.assert_error(
            FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}), "ai_blocked", 422
        )
        self.assert_error(
            FakeResponse(200, {"candidates": [{"finishReason": "SAFETY"}]}), "ai_blocked", 422
        )


class HttpErrorMappingTests(unittest.TestCase):
    def check(self, response, code, status):
        client, _ = client_with(response)
        with self.assertRaises(AIError) as ctx:
            client.generate(USER, temperature=0.5)
        self.assertEqual((ctx.exception.code, ctx.exception.status), (code, status))
        return ctx.exception

    def test_status_mapping(self):
        def err(msg="x"):
            return {"error": {"message": msg}}

        cases = [
            (FakeResponse(401, err()), "ai_auth_error", 503),
            (FakeResponse(403, err()), "ai_auth_error", 503),
            (FakeResponse(400, err("API key not valid. Please pass a valid API key.")), "ai_auth_error", 503),
            (FakeResponse(400, err("bad field")), "ai_bad_request", 502),
            (FakeResponse(404, err("model not found")), "ai_model_unavailable", 503),
            (FakeResponse(429, err()), "ai_rate_limited", 503),
            (FakeResponse(500, err()), "ai_upstream_error", 502),
            (FakeResponse(503, err()), "ai_upstream_error", 502),
            (FakeResponse(500), "ai_upstream_error", 502),  # non-JSON error body
        ]
        for response, code, status in cases:
            with self.subTest(status=response.status_code, code=code):
                self.check(response, code, status)

    def test_retry_after_is_passed_through_only_when_numeric(self):
        exc = self.check(FakeResponse(429, {}, headers={"Retry-After": "7"}), "ai_rate_limited", 503)
        self.assertEqual(exc.headers, {"Retry-After": "7"})
        exc = self.check(FakeResponse(429, {}, headers={"Retry-After": "soon"}), "ai_rate_limited", 503)
        self.assertEqual(exc.headers, {})

    def test_upstream_text_never_reaches_public_message(self):
        exc = self.check(
            FakeResponse(500, {"error": {"message": "INTERNAL-UPSTREAM-DETAIL"}}),
            "ai_upstream_error",
            502,
        )
        self.assertNotIn("INTERNAL-UPSTREAM-DETAIL", exc.message)
        self.assertIn("INTERNAL-UPSTREAM-DETAIL", exc.detail)  # kept for server logs

    def test_model_404_detail_points_at_configuration(self):
        exc = self.check(FakeResponse(404, {}), "ai_model_unavailable", 503)
        self.assertIn("GEMINI_MODEL", exc.detail)

    def test_network_failures(self):
        exc = self.check(requests.exceptions.Timeout("slow"), "ai_timeout", 504)
        self.assertNotIn(FAKE_KEY, exc.detail)
        exc = self.check(requests.exceptions.ConnectionError("down"), "ai_unreachable", 502)
        self.assertNotIn(FAKE_KEY, exc.detail)
        self.check(requests.exceptions.RequestException("odd"), "ai_unreachable", 502)


if __name__ == "__main__":
    unittest.main()
