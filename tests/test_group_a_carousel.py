"""GA16 rights/real-byte OCR/A004 regressions. PG17 runs only in disposable CI."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
import os
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.media_manifest import build_manifest, open_verified
from intelligence.group_a.extractors.carousel import (
    CarouselError, extract_photo_carousel, validate_photo_observation,
)
from intelligence.group_a.extractors.carousel_writer import persist_photo_carousel
from intelligence.group_a.extractors.ocr import OCRConfig

try:
    from PIL import Image
except ImportError:
    Image = None


def _raw_image(size, *, fmt="PNG", orientation=None):
    picture = Image.new("RGB", size, "white")
    with BytesIO() as buffer:
        options = {}
        if orientation is not None:
            exif = Image.Exif()
            exif[274] = orientation
            options["exif"] = exif
        picture.save(buffer, format=fmt, **options)
        return buffer.getvalue()


def _word(text, line="l000001", x=2):
    return {"text": text, "line_ref": line, "confidence_pct": "91.30",
            "bbox": {"x": x, "y": 2, "width": 16, "height": 8}}


def _response(words=(), state=None, languages=("eng", "rus")):
    return {"state": state or ("text_observed" if words else "no_text_detected"),
            "words": list(words), "languages_used": list(languages),
            "engine_rotation": 0, "error_code": None}


class FakeOCR:
    identity = {"engine": "tesseract", "engine_version": "5.5.0",
                "model_sha256": "c" * 64, "device": "cpu"}

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def process(self, png, config):
        self.calls += 1
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AssertionError("GA16 must transcode images to PNG before OCR")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@unittest.skipUnless(Image is not None, "Pillow not installed")
class GA16PhotoCarouselTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project_id = "a" * 32
        self.run_id = str(uuid4())
        self.video_id = 71
        self.source_id = "ga16-test"
        self.policy_id = 23
        self.verified_at = __import__("datetime").datetime(
            2026, 10, 27, tzinfo=__import__("datetime").timezone.utc
        )
        self.rights = True
        self.rights_calls = []

    def rights_check(self, scope):
        self.rights_calls.append(scope)
        return self.rights

    def fixture(self, images, *, kind="photo_carousel"):
        assets = []
        for i, (raw, extension) in enumerate(images, 1):
            path = self.root / f"slide{i}.{extension}"
            path.write_bytes(raw)
            assets.append({
                "media_asset_id": i, "video_id": self.video_id,
                "source_id": self.source_id, "media_kind": "image",
                "local_path": str(path), "sha256": sha256(raw).hexdigest(),
                "byte_size": len(raw),
            })
        pointer = {
            "schema_version": "qdp-handoff/v1",
            "event_id": str(uuid4()), "project_id": self.project_id,
            "qdp_run_id": self.run_id, "video_id": self.video_id,
            "analysis_group": "A", "input_sha256": "b" * 64,
        }
        return build_manifest(pointer, source_id=self.source_id,
                              source_policy_version_id=self.policy_id,
                              content_kind=kind, ordered_assets=assets,
                              roots=[str(self.root)], verified_at=self.verified_at)

    @staticmethod
    def open_refs(handles, root):
        @contextmanager
        def open_ref(ref):
            with open_verified(handles[ref], roots=[str(root)]) as source:
                yield source
        return open_ref

    def test_single_image_post_empty_ocr_is_measured_zero(self):
        manifest, handles = self.fixture([(_raw_image((200, 100)), "png")],
                                         kind="image_post")
        observation = extract_photo_carousel(
            manifest, open_ref=self.open_refs(handles, self.root),
            verify_current=self.rights_check,
            backend=FakeOCR([_response()]))
        self.assertEqual(observation["analysis_state"], "completed")
        self.assertEqual(observation["slide_count"], 1)
        self.assertEqual(observation["total_screen_word_count"], 0)
        self.assertIsNone(observation["first_text_slide"])
        self.assertEqual(observation["items"][0]["aspect_ratio"], "2:1")
        self.assertEqual(observation["items"][0]["on_screen_text"], "")
        self.assertIsNone(observation["items"][0]["duration_sec"])
        self.assertIsNone(observation["cta_slide_index"])
        self.assertEqual(observation["has_audio"], "unknown")
        self.assertEqual(len(self.rights_calls), 2)
        self.assertEqual(validate_photo_observation(manifest, observation), observation)

    def test_ordered_photo_ocr_first_text_and_sum(self):
        manifest, handles = self.fixture([
            (_raw_image((100, 100)), "png"),
            (_raw_image((100, 150)), "png"),
            (_raw_image((300, 200)), "png"),
        ])
        engine = FakeOCR([_response(),
                          _response([_word("Привет"), _word("мир", x=25)]),
                          _response([_word("Hello")])])
        observation = extract_photo_carousel(
            manifest, open_ref=self.open_refs(handles, self.root),
            verify_current=self.rights_check, backend=engine)
        self.assertEqual(engine.calls, 3)
        self.assertEqual([x["slide_index"] for x in observation["items"]], [1, 2, 3])
        self.assertEqual([x["aspect_ratio"] for x in observation["items"]],
                         ["1:1", "2:3", "3:2"])
        self.assertEqual(observation["first_text_slide"], 2)
        self.assertEqual(observation["total_screen_word_count"], 3)
        self.assertEqual(observation["items"][1]["on_screen_text"], "Привет мир")
        self.assertEqual(observation["items"][1]["ocr_confidence_pct"], "91.30")
        self.assertEqual(len(self.rights_calls), 6)
        validate_photo_observation(manifest, observation)

    def test_absent_ocr_keeps_word_count_and_first_text_unknown(self):
        manifest, handles = self.fixture([
            (_raw_image((100, 100)), "png"),
            (_raw_image((150, 100)), "png"),
        ])
        o = extract_photo_carousel(manifest,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)
        self.assertEqual(o["analysis_state"], "partial")
        self.assertEqual(o["text_status"], "unavailable")
        self.assertIsNone(o["total_screen_word_count"])
        self.assertIsNone(o["first_text_slide"])
        self.assertTrue(all(x["screen_word_count"] is None for x in o["items"]))
        validate_photo_observation(manifest, o)

    def test_partial_language_is_not_full_text_coverage(self):
        manifest, handles = self.fixture([(_raw_image((160, 80)), "png")],
                                         kind="image_post")
        engine = FakeOCR([_response([_word("Hello")],
                                   state="partial_language", languages=("eng",))])
        o = extract_photo_carousel(manifest,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check,
                                  backend=engine)
        self.assertEqual(o["text_status"], "partial")
        self.assertIsNone(o["total_screen_word_count"])
        self.assertIsNone(o["items"][0]["screen_word_count"])
        self.assertEqual(o["items"][0]["on_screen_text"], "Hello")
        validate_photo_observation(manifest, o)

    def test_exif_orientation_affects_ratio(self):
        jpeg = _raw_image((120, 80), fmt="JPEG", orientation=6)
        manifest, handles = self.fixture([(jpeg, "jpg")], kind="image_post")
        o = extract_photo_carousel(manifest,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)
        self.assertEqual((o["items"][0]["width"], o["items"][0]["height"]),
                         (80, 120))
        self.assertEqual(o["items"][0]["aspect_ratio"], "2:3")

    def test_revoked_media_rejected_before_read(self):
        manifest, handles = self.fixture([(_raw_image((100, 100)), "png")],
                                         kind="image_post")
        self.rights = False
        with self.assertRaisesRegex(CarouselError, "media_rights_unverified"):
            extract_photo_carousel(manifest,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)

    def test_swapped_or_duplicate_indices_and_mixed_rejected(self):
        manifest, handles = self.fixture([
            (_raw_image((100, 100)), "png"),
            (_raw_image((100, 100)), "png"),
        ])
        bad = dict(manifest, items=[dict(x) for x in manifest["items"]])
        bad["items"][1]["index"] = 1
        bad["manifest_sha256"] = canonical_sha256({
            k: v for k, v in bad.items() if k != "manifest_sha256"
        })
        with self.assertRaises(CarouselError):
            extract_photo_carousel(bad,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)
        mixed = dict(manifest, content_kind="mixed_carousel")
        mixed["manifest_sha256"] = canonical_sha256({
            k: v for k, v in mixed.items() if k != "manifest_sha256"
        })
        with self.assertRaisesRegex(CarouselError, "mixed_or_video_out_of_scope"):
            extract_photo_carousel(mixed,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)

    def test_actual_file_mutation_invalidates_descriptor(self):
        manifest, handles = self.fixture([(_raw_image((100, 100)), "png")],
                                         kind="image_post")
        (self.root / "slide1.png").write_bytes(_raw_image((200, 200)))
        with self.assertRaisesRegex(CarouselError, "image_ref_unavailable"):
            extract_photo_carousel(manifest,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)

    def test_observation_digest_and_aggregate_forgery_rejected(self):
        manifest, handles = self.fixture([(_raw_image((100, 100)), "png")],
                                         kind="image_post")
        original = extract_photo_carousel(
            manifest, open_ref=self.open_refs(handles, self.root),
            verify_current=self.rights_check)
        forged = dict(original, total_screen_word_count=0)
        with self.assertRaisesRegex(CarouselError, "photo_observation_digest_mismatch"):
            validate_photo_observation(manifest, forged)
        forged["observation_sha256"] = canonical_sha256({
            k: v for k, v in forged.items() if k != "observation_sha256"
        })
        with self.assertRaisesRegex(CarouselError, "photo_observation_truth_invalid"):
            validate_photo_observation(manifest, forged)

    def test_backend_exception_does_not_turn_into_empty_text(self):
        manifest, handles = self.fixture([(_raw_image((100, 100)), "png")],
                                         kind="image_post")
        o = extract_photo_carousel(
            manifest, open_ref=self.open_refs(handles, self.root),
            verify_current=self.rights_check,
            backend=FakeOCR([RuntimeError("private path must not leak")]))
        self.assertEqual(o["analysis_state"], "partial")
        self.assertEqual(o["items"][0]["reason_code"], "ocr_backend_failed")
        self.assertIsNone(o["items"][0]["screen_word_count"])

    def test_animation_rejected_not_falsely_still(self):
        frames = [Image.new("RGB", (80, 80), color) for color in ("red", "blue")]
        with BytesIO() as buffer:
            frames[0].save(buffer, "GIF", save_all=True,
                           append_images=frames[1:], duration=100)
            gif = buffer.getvalue()
        manifest, handles = self.fixture([(gif, "gif")], kind="image_post")
        with self.assertRaisesRegex(CarouselError, "animated_image_out_of_scope"):
            extract_photo_carousel(manifest,
                                  open_ref=self.open_refs(handles, self.root),
                                  verify_current=self.rights_check)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN") and Image is not None,
                     "Disposable PostgreSQL DSN / Pillow not configured")
class GA16PostgresTests(unittest.TestCase):
    fixture = GA16PhotoCarouselTests.fixture
    open_refs = staticmethod(GA16PhotoCarouselTests.open_refs)
    rights_check = GA16PhotoCarouselTests.rights_check
    """Real A004 trigger/view/rollback/replay test on disposable PG17 only."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence.migrate import apply_migrations
        cls.pg = psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=True)
        apply_migrations(cls.pg)

    @classmethod
    def tearDownClass(cls):
        cls.pg.close()

    def setUp(self):
        GA16PhotoCarouselTests.setUp(self)
        self.source_id = "ga16-" + uuid4().hex
        token = uuid4().hex
        self.video_id = self.pg.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
               VALUES ('instagram',%s,%s,'photo_carousel') RETURNING id""",
            (token, "https://www.instagram.com/p/" + token)
        ).fetchone()[0]
        self.pg.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'test','owned','enabled',TRUE)""", (self.source_id,))
        self.policy_id = self.pg.execute(
            """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
               VALUES (%s,1,TRUE,'{"can_analyze_media":true}'::jsonb) RETURNING id""",
            (self.source_id,)
        ).fetchone()[0]

    def tearDown(self):
        self.pg.execute("DELETE FROM videos WHERE id=%s", (self.video_id,))
        self.pg.execute("DELETE FROM sources WHERE id=%s", (self.source_id,))
        # TemporaryDirectory cleanup is registered by pure fixture setUp.

    def test_pg17_atomic_insert_summary_replay_and_revoke(self):
        raw_images = [(_raw_image((100,100)), "png"),
                      (_raw_image((100,150)), "png")]
        manifest, handles = self.fixture(raw_images)
        ids = []
        for asset in manifest["items"]:
            path = self.root / f"slide{asset['index']}.png"
            aid = self.pg.execute(
                """INSERT INTO media_assets(video_id,source_id,media_kind,
                   local_path,sha256,permission)
                   VALUES (%s,%s,'image',%s,%s,'analysis_allowed') RETURNING id""",
                (self.video_id, self.source_id, str(path), asset["sha256"])
            ).fetchone()[0]
            ids.append(aid)
        # Recreate a manifest with real DB IDs (not fixture 1..N), while
        # continuing to verify exactly the same authorized on-disk bytes.
        rows = [
            {"media_asset_id": ids[i], "video_id": self.video_id,
             "source_id": self.source_id, "media_kind": "image",
             "local_path": str(self.root / f"slide{i+1}.png"),
             "sha256": sha256(raw_images[i][0]).hexdigest(),
             "byte_size": len(raw_images[i][0])}
            for i in range(2)
        ]
        pointer = {"schema_version": "qdp-handoff/v1", "event_id": str(uuid4()),
                   "project_id": self.project_id, "qdp_run_id": self.run_id,
                   "video_id": self.video_id, "analysis_group": "A",
                   "input_sha256": "b"*64}
        manifest, handles = build_manifest(
            pointer, source_id=self.source_id, source_policy_version_id=self.policy_id,
            content_kind="photo_carousel", ordered_assets=rows,
            roots=[str(self.root)], verified_at=self.verified_at)
        engine = FakeOCR([_response(),
                          _response([_word("Hello")])])
        observed = extract_photo_carousel(
            manifest, open_ref=self.open_refs(handles, self.root),
            verify_current=self.rights_check, backend=engine)
        first = persist_photo_carousel(self.pg, manifest, observed,
                                       verify_current=self.rights_check)
        same = persist_photo_carousel(self.pg, manifest, observed,
                                      verify_current=self.rights_check)
        self.assertEqual(first, same)
        summary = self.pg.execute(
            """SELECT state,slide_count,first_text_slide,total_screen_word_count
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",
            (first,)
        ).fetchone()
        self.assertEqual(summary, ("completed", 2, 2, 1))
        rows_db = self.pg.execute(
            """SELECT slide_index,media_asset_id,duration_sec,screen_word_count
               FROM carousel_feature_a_items WHERE analysis_id=%s
               ORDER BY slide_index""", (first,)
        ).fetchall()
        self.assertEqual(rows_db, [(1, ids[0], None, 0), (2, ids[1], None, 1)])
        self.pg.execute("UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",
                        (self.source_id,))
        with self.assertRaises(CarouselError):
            persist_photo_carousel(self.pg, manifest, observed,
                                   verify_current=self.rights_check)

