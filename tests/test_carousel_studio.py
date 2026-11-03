"""Offline tests for JSON -> LLM carousel rewrite API (no paid calls)."""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import carousel_studio as studio


class FakeHandler:
    def __init__(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.rfile = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}
        self.status = None
        self.result = None

    def json_response(self, status, value):
        self.status, self.result = status, value


class CarouselStudioTests(unittest.TestCase):
    def payload(self):
        return {
            "language": "ru", "model": "openai/gpt-4.1-mini",
            "slides": [
                {"id": "1", "role": "hook", "texts": ["Утром лицо выглядело отёкшим"],
                 "max_chars": [100]},
                {"id": "2", "role": "cta", "texts": ["Я пользуюсь приложением", "Вот мой ритуал"],
                 "max_chars": [100, 80]},
            ],
        }

    def test_parses_two_slides_and_defaults(self):
        slides, model, key, language = studio.parse_rewrite_payload(self.payload())
        self.assertEqual(len(slides), 2)
        self.assertEqual((model, key, language), ("openai/gpt-4.1-mini", "", "ru"))
        self.assertEqual(slides[1]["texts"][1], "Вот мой ритуал")

    def test_rejects_duplicate_slide_ids(self):
        payload = self.payload()
        payload["slides"][1]["id"] = "1"
        with self.assertRaises(studio.CarouselError) as error:
            studio.parse_rewrite_payload(payload)
        self.assertEqual(error.exception.code, "invalid_slide_id")

    def test_rejects_invalid_model_key_or_empty_text(self):
        for field, value in [("model", "../bad?model"), ("api_key", "wrong-secret"),
                             ("slides", [{"id": "1", "texts": [""]}])]:
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = value
                with self.assertRaises(studio.CarouselError):
                    studio.parse_rewrite_payload(payload)

    def test_rejects_malformed_llm_output_and_wrong_order(self):
        slides, _, _, _ = studio.parse_rewrite_payload(self.payload())
        for result in [
            {"slides": []},
            {"slides": [{"id": "2", "texts": ["oops"]}, {"id": "1", "texts": ["oops", "oops"]}]},
            {"slides": [{"id": "1", "texts": ["a" * 101]}, {"id": "2", "texts": ["x", "y"]}]},
            {"slides": [{"id": "1", "texts": ["good"]}, {"id": "2", "texts": [""]}]}
        ]:
            with self.subTest(result=result):
                with self.assertRaises(studio.CarouselError):
                    studio.validate_rewrite(slides, result)

    def test_real_openrouter_request_shape_with_mock(self):
        payload = self.payload()
        slides, model, _, language = studio.parse_rewrite_payload(payload)
        provider_result = {
            "choices": [{"message": {"content": json.dumps({
                "slides": [{"id": "1", "texts": ["С утра лицо казалось отёкшим"]},
                           {"id": "2", "texts": ["Мне нравится приложение", "Это мой ежедневный ритуал"]}]
            }, ensure_ascii=False)}}],
            "usage": {"cost": 0.0042, "prompt_tokens": 250, "completion_tokens": 110}
        }
        response = MagicMock()
        response.read.return_value = json.dumps(provider_result, ensure_ascii=False).encode("utf-8")
        connection = MagicMock()
        connection.__enter__.return_value = response
        with patch.object(studio, "urlopen", return_value=connection) as network:
            result = studio.rewrite(slides, model, "test-private-key", language)
        request = network.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, studio.ENDPOINT)
        self.assertEqual(body["model"], "openai/gpt-4.1-mini")
        self.assertEqual(body["response_format"]["type"], "json_object")
        self.assertIn("Bearer ", request.get_header("Authorization"))
        self.assertEqual(len(result["slides"]), 2)
        self.assertEqual(result["usage"]["cost_usd"], 0.0042)
        self.assertNotIn("test-private-key", json.dumps(result))

    def test_missing_key_provides_informative_error(self):
        handler = FakeHandler(self.payload())
        with patch.object(studio, "configured_api_key", return_value=""):
            self.assertTrue(studio.handle_post(handler, "/api/carousel-studio/rewrite"))
        self.assertEqual(handler.status, 503)
        self.assertEqual(handler.result["error"]["code"], "key_missing")

    def test_manual_key_used_only_in_transient_request(self):
        payload = self.payload()
        payload["api_key"] = "sk-or-v1-test-credential"
        handler = FakeHandler(payload)
        result = {"slides": [{"id": "1", "texts": ["test"]}, {"id": "2", "texts": ["test", "test"]}]}
        with patch.object(studio, "rewrite", return_value=result) as rewrite:
            self.assertTrue(studio.handle_post(handler, "/api/carousel-studio/rewrite"))
        self.assertEqual(handler.status, 200)
        self.assertEqual(rewrite.call_args.args[2], "sk-or-v1-test-credential")
        self.assertNotIn("sk-or-v1-test-credential", json.dumps(handler.result))

    def test_api_rejects_oversized_request(self):
        handler = FakeHandler(self.payload())
        handler.headers["Content-Length"] = str(studio.MAX_BODY + 1)
        self.assertTrue(studio.handle_post(handler, "/api/carousel-studio/rewrite"))
        self.assertEqual(handler.status, 400)
        self.assertEqual(handler.result["error"]["code"], "invalid_size")

    def test_status_has_no_key(self):
        from urllib.parse import urlparse
        handler = FakeHandler({})
        with patch.object(studio, "configured_api_key", return_value="secret"):
            self.assertTrue(studio.handle_get(handler, urlparse("/api/carousel-studio/status")))
        self.assertEqual(handler.result, {"configured": True, "default_model": studio.DEFAULT_MODEL})
        self.assertNotIn("secret", json.dumps(handler.result))

    def test_unhandled_route_returns_false(self):
        self.assertFalse(studio.handle_post(FakeHandler({}), "/api/carousel-studio/unknown"))


if __name__ == "__main__":
    unittest.main()
