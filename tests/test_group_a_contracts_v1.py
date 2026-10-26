"""GA-MOD-01: contract truth, canonical IDs and schema adversarial regression."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import unittest
from uuid import uuid4

from intelligence.group_a.contracts_v1 import (
    ContractViolation, canonical_json_bytes, canonical_sha256,
    ensure_same_lineage, group_a_to_b_v1, media_manifest_v1,
    peer_state, qdp_handoff_v1, stream_truth_v1, REQUIRED_STREAMS,
)

try:
    import jsonschema
except ImportError:
    jsonschema = None

SCHEMAS = Path(__file__).resolve().parents[1] / "intelligence" / "group_a" / "schemas"
PROJECT = "a" * 32
QDP = str(uuid4())
EVENT = str(uuid4())
HASH = "c" * 64


def pointer():
    return dict(schema_version="qdp-handoff/v1", event_id=EVENT,
                project_id=PROJECT, qdp_run_id=QDP, video_id=123,
                analysis_group="A", input_sha256=HASH)


def manifest(kind="video"):
    items = [
        dict(index=1, kind="video", media_asset_id=33, sha256="d" * 64,
             byte_size=100, verified_local_path_ref="ga-local:a1", duration_sec="5.000")
    ]
    if kind == "image_post":
        items = [dict(index=1, kind="image", media_asset_id=33, sha256="d" * 64,
                      byte_size=100, verified_local_path_ref="ga-local:a1")]
    if kind == "photo_carousel":
        items = [
            dict(index=i, kind="image", media_asset_id=i + 100,
                 sha256=("a" if i == 1 else "b") * 64, byte_size=100,
                 verified_local_path_ref=f"ga-local:item-{i}")
            for i in (1, 2)
        ]
    if kind == "mixed_carousel":
        items = [
            dict(index=1, kind="image", media_asset_id=101, sha256="a" * 64,
                 byte_size=100, verified_local_path_ref="ga-local:image"),
            dict(index=2, kind="video", media_asset_id=102, sha256="b" * 64,
                 byte_size=150, verified_local_path_ref="ga-local:video", duration_sec="3.000"),
        ]
    data = dict(schema_version="ga-media-manifest/v1", project_id=PROJECT,
                qdp_run_id=QDP, video_id=123, qdp_input_sha256=HASH,
                content_kind=kind, source_id="source:1",
                source_policy_version_id=40, verified_at="2026-10-26T09:00:00Z",
                items=items)
    return dict(**data, manifest_sha256=canonical_sha256(data))


def refresh_hash(m):
    m["manifest_sha256"] = canonical_sha256({
        k: v for k, v in m.items() if k != "manifest_sha256"})
    return m


def status(name="not_run"):
    if name == "complete":
        return {"status": "complete", "evidence_count": 1,
                "verified_absence": False, "reason_code": None}
    return {"status": name, "evidence_count": None,
            "verified_absence": False, "reason_code": name + "_pending"}


def result(m, complete=False):
    isvideo = m["content_kind"] in ("video", "slideshow_video")
    streams = {name: status("complete" if complete else "not_run")
               for name in REQUIRED_STREAMS[m["content_kind"]]}
    return dict(schema_version="group-a-to-b/v1", project_id=m["project_id"],
                qdp_run_id=m["qdp_run_id"], video_id=m["video_id"],
                qdp_input_sha256=m["qdp_input_sha256"],
                manifest_sha256=m["manifest_sha256"],
                content_kind=m["content_kind"],
                analysis_state="completed" if complete else "partial",
                video_a_analysis_id=301 if isvideo else None,
                carousel_a_analysis_id=None if isvideo else 302,
                method_version="group-a-local-video-v1",
                streams=streams, evidence=[])


class GAV1Contracts(unittest.TestCase):
    def reject(self, fn, code=None):
        with self.assertRaises(ContractViolation) as ctx:
            fn()
        if code:
            self.assertEqual(ctx.exception.code, code)

    def test_good_qdp_pointer_and_exact_field_names(self):
        self.assertEqual(qdp_handoff_v1(pointer()), pointer())
        for field, value in (
            ("project_id", PROJECT.upper()),
            ("qdp_run_id", QDP.upper()),
            ("event_id", EVENT.upper()),
            ("video_id", True),
            ("analysis_group", "B"),
            ("input_sha256", HASH.upper()),
            ("schema_version", "qdp-handoff/v2"),
        ):
            p = pointer()
            p[field] = value
            with self.subTest(field=field):
                self.reject(lambda: qdp_handoff_v1(p))
        p = pointer()
        p["preparation_input_sha256"] = p.pop("input_sha256")
        self.reject(lambda: qdp_handoff_v1(p), "missing_field")
        p = pointer()
        p["untrusted_project"] = "other"
        self.reject(lambda: qdp_handoff_v1(p), "unexpected_field")

    def test_manifest_five_kinds_and_verified_digest(self):
        for kind in ("video", "slideshow_video", "image_post",
                     "photo_carousel", "mixed_carousel"):
            m = manifest(kind)
            with self.subTest(kind=kind):
                self.assertEqual(media_manifest_v1(m), m)
        m = manifest()
        m["items"][0]["byte_size"] += 1
        self.reject(lambda: media_manifest_v1(m), "manifest_digest_mismatch")

    def test_manifest_rejects_bad_order_asset_duplicate_and_local_path(self):
        for mutation, code in (
            (lambda m: m["items"][0].update(index=2), "noncanonical_slide_order"),
            (lambda m: m["items"][1].update(media_asset_id=101), "duplicate_media_asset"),
            (lambda m: m["items"][0].update(verified_local_path_ref="/etc/passwd"),
             "invalid_field"),
            (lambda m: m["items"][0].update(byte_size=0), "invalid_media_bytes"),
            (lambda m: m["items"][1].update(duration_sec="2.000"), "image_has_duration"),
            (lambda m: m.update(content_kind="video"), "kind_item_mismatch"),
        ):
            m = manifest("photo_carousel")
            mutation(m)
            refresh_hash(m)
            with self.subTest(code=code, m=m):
                self.reject(lambda: media_manifest_v1(m), code)

    def test_truth_of_silence_is_not_asr_failure(self):
        confirmed_silent = {"status": "complete", "evidence_count": 0,
                            "verified_absence": True, "reason_code": None}
        self.assertEqual(stream_truth_v1(confirmed_silent)["evidence_count"], 0)
        asr_failed = {"status": "unavailable", "evidence_count": None,
                      "verified_absence": False, "reason_code": "asr_error"}
        self.assertEqual(stream_truth_v1(asr_failed)["status"], "unavailable")
        for bad in (
            dict(confirmed_silent, verified_absence=False),
            dict(asr_failed, evidence_count=0),
            dict(asr_failed, verified_absence=True),
            dict(asr_failed, reason_code=None),
            dict(confirmed_silent, status="unknown"),
            dict(confirmed_silent, evidence_count=True),
            dict(confirmed_silent, extra="ignored"),
        ):
            self.reject(lambda bad=bad: stream_truth_v1(bad))

    def test_partial_vs_completed_and_foreign_ref(self):
        for kind in ("video", "slideshow_video", "photo_carousel",
                     "image_post", "mixed_carousel"):
            m = manifest(kind)
            self.assertEqual(group_a_to_b_v1(result(m))["analysis_state"], "partial")
            self.assertEqual(group_a_to_b_v1(result(m, True))["analysis_state"], "completed")
            r = result(m)
            r["analysis_state"] = "completed"
            self.reject(lambda: group_a_to_b_v1(r), "inconsistent_analysis_state")
            r = result(m, True)
            r["streams"].pop(next(iter(r["streams"])))
            self.reject(lambda: group_a_to_b_v1(r), "incomplete_stream_matrix")
            r = result(m)
            r["video_a_analysis_id"], r["carousel_a_analysis_id"] = 200, 201
            self.reject(lambda: group_a_to_b_v1(r), "analysis_kind_mismatch")

    def test_cross_project_cross_video_and_preferred_media(self):
        q, m = pointer(), manifest()
        self.assertTrue(ensure_same_lineage(q, m, result(m)))
        for key, value in (("project_id", "b" * 32), ("video_id", 456),
                           ("qdp_input_sha256", "a" * 64),
                           ("qdp_run_id", str(uuid4()))):
            other = refresh_hash(dict(m, **{key: value}))
            with self.subTest(key=key):
                self.reject(lambda: ensure_same_lineage(q, other), "lineage_mismatch")
        q["preferred_media_asset_id"] = 999
        self.reject(lambda: ensure_same_lineage(q, m), "preferred_media_mismatch")

    def test_peer_versions_and_deterministic_canonical_encoding(self):
        self.assertEqual(peer_state("transcript/v1", None), "unavailable")
        self.assertEqual(peer_state("transcript/v1", "transcript/v2"), "incompatible")
        self.assertEqual(peer_state("transcript/v1", "transcript/v1"), "available")
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}),
                         canonical_json_bytes({"a": 1, "b": 2}))
        self.reject(lambda: canonical_sha256({"bad": float("nan")}),
                    "invalid_canonical_json")
        self.reject(lambda: canonical_sha256({"bad": object()}),
                    "invalid_canonical_json")

    def test_static_json_schemas_exist_and_restrict_extra_fields(self):
        for name in ("qdp-handoff-v1", "media-manifest-v1",
                     "stream-truth-v1", "group-a-to-b-v1"):
            doc = json.loads((SCHEMAS / (name + ".json")).read_text("utf-8"))
            with self.subTest(schema=name):
                self.assertEqual(doc["$schema"],
                                 "https://json-schema.org/draft/2020-12/schema")
                self.assertFalse(doc["additionalProperties"])
                self.assertTrue(doc["required"])

    @unittest.skipUnless(jsonschema is not None, "Optional jsonschema validator not installed")
    def test_jsonschema_accepts_and_rejects_wire_payloads(self):
        for name, good in (
            ("qdp-handoff-v1", pointer()),
            ("media-manifest-v1", manifest()),
            ("stream-truth-v1", status("complete")),
            ("group-a-to-b-v1", result(manifest())),
        ):
            schema = json.loads((SCHEMAS / (name + ".json")).read_text("utf-8"))
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(good, schema, cls=jsonschema.Draft202012Validator)
            malformed = deepcopy(good)
            malformed["unexpected"] = 5
            with self.subTest(name=name):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(malformed, schema,
                                        cls=jsonschema.Draft202012Validator)


if __name__ == "__main__":
    unittest.main()
