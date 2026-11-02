"""Offline regression tests for AI Avatar Studio (never call paid APIs)."""
import base64
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import avatar_studio as studio

# Valid tiny PNG fixture; the tests never send it to OpenAI.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlDdyQAAAAASUVORK5CYII=")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


class FakeHandler:
    def __init__(self, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else b"{}"
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        self.response = None
        self.code = None
        self.response_headers = {}

    def json_response(self, status, body):
        self.code, self.response = status, body

    def send_response(self, status):
        self.code = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


class FakePool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


class AvatarStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CM_AVATAR_STUDIO_DIR": self.temp.name})
        self.env.start()
        studio.ACTIVE.clear()

    def tearDown(self):
        studio.ACTIVE.clear()
        self.env.stop()
        self.temp.cleanup()

    def payload(self, **changes):
        result = {"prompt": "Natural skin texture, preserve the facial identity of the reference photo.",
                  "quality": "xhigh", "size": "1024x1536", "reference": DATA_URL}
        result.update(changes)
        return result

    def test_acceptable_reference_and_prompt(self):
        prompt, quality, size, data, extension, mime = studio.parse_payload(self.payload())
        self.assertTrue(prompt.startswith("Natural"))
        self.assertEqual(quality, "xhigh")
        self.assertEqual(size, "1024x1536")
        self.assertEqual(data, PNG)
        self.assertEqual((extension, mime), ("png", "png"))

    def test_rejects_mismatched_mime_and_corrupted_base64(self):
        with self.assertRaises(studio.StudioError) as wrong:
            studio.parse_payload(self.payload(reference="data:image/jpeg;base64," +
                                            base64.b64encode(PNG).decode("ascii")))
        self.assertEqual(wrong.exception.code, "invalid_reference")
        with self.assertRaises(studio.StudioError):
            studio.parse_payload(self.payload(reference="data:image/png;base64,XXXX==$$"))

    def test_validates_limits_without_provider_call(self):
        for changes in ({"quality": "extreme"}, {"size": "../etc/passwd"},
                        {"prompt": "short"}, {"reference": ""}):
            with self.subTest(changes=changes):
                with self.assertRaises(studio.StudioError):
                    studio.parse_payload(self.payload(**changes))

    def test_multipart_uses_official_image_array_field(self):
        body, kind = studio.multipart({"model": studio.MODEL, "prompt": "test"}, PNG, "png")
        self.assertIn(b'name="image[]"; filename="reference.png"', body)
        self.assertIn(b'name="model"', body)
        self.assertIn(PNG, body)
        self.assertTrue(kind.startswith("multipart/form-data; boundary="))

    def test_provider_errors_are_human_readable(self):
        err = studio.provider_error(429, b'{"error":{"code":"insufficient_quota","message":"quota"}}', "req-test")
        self.assertEqual(err.code, "insufficient_quota")
        self.assertIn("лимит", str(err))
        self.assertEqual(err.request_id, "req-test")
        self.assertIn("API-ключ", str(studio.provider_error(401, b"{}")))

    def test_no_api_key_does_not_create_paid_job(self):
        handler = FakeHandler(self.payload())
        with patch.object(studio, "api_key", return_value=""):
            studio.handle_post(handler, "/api/avatar-studio/generate")
        self.assertEqual(handler.code, 503)
        self.assertEqual(handler.response["error"]["code"], "key_missing")
        self.assertFalse(list(studio.root_dir().glob("*")) if studio.root_dir().exists() else [])

    def test_job_persistence_and_polling(self):
        handler = FakeHandler(self.payload())
        pool = FakePool()
        with patch.object(studio, "api_key", return_value="test-private-token"), patch.object(studio, "POOL", pool):
            self.assertTrue(studio.handle_post(handler, "/api/avatar-studio/generate"))
        self.assertEqual(handler.code, 202)
        job = handler.response["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(pool.calls), 1)
        self.assertTrue((studio.location(job["id"]) / "reference.png").is_file())
        self.assertNotIn("test-private-token", json.dumps(job))
        with patch.object(studio, "call_openai",
                          return_value={"image": PNG, "usage": {"output_tokens": 1234},
                                        "estimated_cost_usd": 0.037, "request_id": "req-test"}):
            studio.run(*pool.calls[0][1])
        self.assertEqual(studio.load_job(job["id"])["status"], "succeeded")
        self.assertTrue((studio.location(job["id"]) / "result.png").is_file())

        from urllib.parse import urlparse
        status = FakeHandler()
        studio.handle_get(status, urlparse("/api/avatar-studio/jobs/" + job["id"]))
        self.assertEqual(status.response["job"]["status"], "succeeded")
        image = FakeHandler()
        studio.handle_get(image, urlparse("/api/avatar-studio/files/" + job["id"] + "/result"))
        self.assertEqual(image.code, 200)
        self.assertEqual(image.wfile.getvalue(), PNG)
        self.assertEqual(image.response_headers["Content-Type"], "image/png")

        history = FakeHandler()
        studio.handle_get(history, urlparse("/api/avatar-studio/history"))
        self.assertEqual(history.response["jobs"][0]["id"], job["id"])

    def test_detects_interrupted_job_after_restart(self):
        job_id = "f" * 32
        studio.ACTIVE.add(job_id)
        studio.save_job({"id": job_id, "status": "running", "created_at": studio.now(),
                         "reference_ext": "png"})
        studio.ACTIVE.clear()
        recovered = studio.load_job(job_id)
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error"]["code"], "interrupted")

    def test_blocks_path_traversal_and_unknown_file(self):
        from urllib.parse import urlparse
        handler = FakeHandler()
        studio.handle_get(handler, urlparse("/api/avatar-studio/files/../../secret"))
        self.assertEqual(handler.code, 404)
        handler = FakeHandler()
        studio.handle_get(handler, urlparse("/api/avatar-studio/jobs/" + "a" * 32))
        self.assertEqual(handler.code, 404)
        self.assertEqual(list(studio.root_dir().iterdir()), [])


if __name__ == "__main__":
    unittest.main()
