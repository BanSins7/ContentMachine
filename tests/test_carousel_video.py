"""Offline and optional real FFmpeg tests for Carousel Studio MP4 pipeline."""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import carousel_video as video


def png(width=600, height=600):
    def chunk(name, content):
        body = name + content
        return struct.pack(">I", len(content)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)
    row = b"\x00" + b"\x40\xa0\xda" * width
    pixels = row * height
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(pixels, level=6)) + chunk(b"IEND", b""))


PNG = png()
DATA = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


class FakeHandler:
    def __init__(self, payload=None):
        raw = json.dumps(payload).encode("utf-8") if payload is not None else b"{}"
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(raw))}
        self.code = None
        self.body = None
        self.reply_headers = {}

    def json_response(self, code, body):
        self.code, self.body = code, body

    def send_response(self, code):
        self.code = code

    def send_header(self, key, value):
        self.reply_headers[key] = value

    def end_headers(self):
        pass


class FakePool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


class VideoStudioTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CM_CAROUSEL_VIDEO_DIR": self.folder.name, "CM_FFMPEG_PATH": ""})
        self.env.start()
        video.ACTIVE.clear()

    def tearDown(self):
        video.ACTIVE.clear()
        self.env.stop()
        self.folder.cleanup()

    def payload(self, durations=(1.25, 2.5)):
        return {"carousel_id": "test_carousel", "slides": [
            {"duration_seconds": seconds, "image": DATA} for seconds in durations
        ]}

    def test_validates_png_and_duration(self):
        name, frames = video.validate_request(self.payload())
        self.assertEqual(name, "test_carousel")
        self.assertEqual([seconds for _, seconds in frames], [1.25, 2.5])
        self.assertEqual(frames[0][0], PNG)

    def test_rejects_invalid_inputs(self):
        invalid = [
            {"carousel_id": "../escape", "slides": [{"duration_seconds": 1, "image": DATA}]},
            {"carousel_id": "okay", "slides": []},
            {"carousel_id": "okay", "slides": [{"duration_seconds": -2, "image": DATA}]},
            {"carousel_id": "okay", "slides": [{"duration_seconds": True, "image": DATA}]},
            {"carousel_id": "okay", "slides": [{"duration_seconds": 1, "image": "file:///etc/passwd"}]},
            {"carousel_id": "okay", "slides": [{"duration_seconds": 1, "image": "data:image/png;base64,AAAA"}]},
            {"carousel_id": "okay", "slides": [{"duration_seconds": 1, "image": DATA}] * 41},
            {"carousel_id": "okay", "slides": [{"duration_seconds": 30, "image": DATA}] * 21},
        ]
        for data in invalid:
            with self.subTest(value=str(data)[:100]):
                with self.assertRaises(video.VideoError):
                    video.validate_request(data)

    def test_ffmpeg_absence_is_explicit(self):
        with patch.object(video.shutil, "which", return_value=None):
            handler = FakeHandler(self.payload())
            self.assertTrue(video.handle_post(handler, "/api/carousel-video/render"))
        self.assertEqual(handler.code, 503)
        self.assertEqual(handler.body["error"]["code"], "ffmpeg_missing")

    def test_json_upload_and_ffmpeg_render_without_shell(self):
        handler, fake_pool = FakeHandler(self.payload()), FakePool()
        with patch.object(video, "ffmpeg_binary", return_value="/fake/ffmpeg"), patch.object(video, "POOL", fake_pool):
            self.assertTrue(video.handle_post(handler, "/api/carousel-video/render"))
        self.assertEqual(handler.code, 202)
        job = handler.body["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["duration_seconds"], 3.75)
        self.assertEqual(len(fake_pool.calls), 1)

        fake_result = MagicMock(returncode=0)
        def encode(args, **kwargs):
            self.assertIsInstance(args, list)
            self.assertEqual(args[0], "/fake/ffmpeg")
            self.assertIn("libx264", args)
            self.assertIn("-t", args)
            self.assertEqual(args[args.index("-t") + 1], "3.750")
            self.assertEqual(kwargs["cwd"], video.job_dir(job["id"]))
            (kwargs["cwd"] / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypisomMOCKEDMP4")
            return fake_result

        with patch.object(video.subprocess, "run", side_effect=encode):
            video.render_job(*fake_pool.calls[0][1])

        complete = video.read_job(job["id"])
        self.assertEqual(complete["status"], "succeeded")
        self.assertTrue((video.job_dir(job["id"]) / "video.mp4").is_file())
        self.assertFalse(list(video.job_dir(job["id"]).glob("frame_*.png")))

        status = FakeHandler()
        self.assertTrue(video.handle_get(status, urlparse("/api/carousel-video/jobs/" + job["id"])))
        self.assertEqual(status.body["job"]["status"], "succeeded")
        download = FakeHandler()
        self.assertTrue(video.handle_get(download, urlparse("/api/carousel-video/files/" + job["id"] + "/video")))
        self.assertEqual(download.code, 200)
        self.assertEqual(download.reply_headers["Content-Type"], "video/mp4")
        self.assertIn(b"ftyp", download.wfile.getvalue())
        ranged = FakeHandler()
        ranged.headers["Range"] = "bytes=4-7"
        self.assertTrue(video.handle_get(ranged, urlparse("/api/carousel-video/files/" + job["id"] + "/video")))
        self.assertEqual(ranged.code, 206)
        self.assertEqual(ranged.wfile.getvalue(), b"ftyp")
        self.assertEqual(ranged.reply_headers["Accept-Ranges"], "bytes")
        bad_range = FakeHandler()
        bad_range.headers["Range"] = "bytes=999999-"
        self.assertTrue(video.handle_get(bad_range, urlparse("/api/carousel-video/files/" + job["id"] + "/video")))
        self.assertEqual(bad_range.code, 416)

    def test_recovers_interrupted_task(self):
        job_id = "c" * 32
        video.ACTIVE.add(job_id)
        video.write_job({"id": job_id, "status": "running", "created_at": video.now()})
        video.ACTIVE.clear()
        job = video.read_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "interrupted")

    def test_output_path_is_isolated(self):
        with self.assertRaises(video.VideoError):
            video.job_dir("../hello")
        h = FakeHandler()
        self.assertTrue(video.handle_get(h, urlparse("/api/carousel-video/jobs/" + "f" * 32)))
        self.assertEqual(h.code, 404)
        self.assertFalse(video.handle_get(FakeHandler(), urlparse("/api/carousel-video/unknown")))

    @unittest.skipUnless(shutil.which("ffmpeg"), "Optional local FFmpeg unavailable")
    def test_actual_ffmpeg_encodes_mp4(self):
        name, frames = video.validate_request(self.payload(durations=(0.4, 0.4)))
        job_id = "a" * 32
        video.ACTIVE.add(job_id)
        video.write_job({"id": job_id, "carousel_id": name, "status": "queued",
                         "created_at": video.now(), "slides": 2, "duration_seconds": 0.8})
        video.render_job(job_id, frames, video.ffmpeg_binary())
        result = video.read_job(job_id)
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        content = (video.job_dir(job_id) / "video.mp4").read_bytes()
        self.assertIn(b"ftyp", content[:16])
        self.assertGreater(len(content), 1000)


if __name__ == "__main__":
    unittest.main()
