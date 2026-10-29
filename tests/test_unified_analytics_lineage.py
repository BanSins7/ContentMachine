"""UA-MOD-05: strict project/source/video/policy and exact A/B/C lineage tests.

No live QDP membership is fabricated: CanonicalLineage.qualified is ALWAYS
False. Main-D1-only SQL parse is tested on disposable PG17 when available.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from intelligence.unified_analytics import (
    AnalyticsBoundaryError, InputScope, UnifiedAnalyticsInputPointer,
)
from intelligence.unified_analytics.lineage import (
    LINEAGE_SCHEMA, A_CAROUSEL_SQL, A_VIDEO_SQL, B_ANALYSIS_SQL, C_METRIC_SQL,
    D1_IDENTITY_SQL, CanonicalLineagePins, resolve_canonical_lineage,
)

P = "a" * 32
QDP = "00112233-4455-6677-8899-aabbccddeeff"
RUN = "11223344-5566-4777-8899-aabbccddeeff"
AS_OF = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
SOURCE = "licensed_source"
SHA = "b" * 64

SCOPE = InputScope(P, 31, SOURCE, 4, "video", QDP, SHA)
PINS = CanonicalLineagePins(
    scope=SCOPE, metadata_id=101, metric_snapshot_id=202,
    sre_run_id=RUN, sre_taxonomy_version_id=303,
    sre_content_version_id=404, group_a_analysis_id=505,
    group_b_analysis_id=606, calculated_metric_id=707,
    b_taxonomy_version="taxonomy/1", sre_decision_sha256="c" * 64,
)


def rows(pins=PINS, as_of=AS_OF):
    s = pins.scope
    video = s.content_kind in ("video", "slideshow_video")
    base = dict(
        project_id=s.project_id, video_id=s.video_id, content_kind=s.content_kind,
        member_video_id=s.video_id, source_id=s.source_id, source_status="enabled",
        policy_id=s.source_policy_version_id, policy_source_id=s.source_id,
        policy_is_active=True, metadata_id=pins.metadata_id,
        metadata_policy_id=s.source_policy_version_id,
        metadata_is_current=True, metadata_deleted_at=None,
        metadata_expires_at=as_of + timedelta(days=2),
        snapshot_id=pins.metric_snapshot_id, snapshot_video_id=s.video_id,
        snapshot_source_id=s.source_id, snapshot_policy_id=s.source_policy_version_id,
        metric_definition="video_views/v1", metric_version="1",
        active_taxonomy_id=pins.sre_taxonomy_version_id,
        taxonomy_id=pins.sre_taxonomy_version_id,
        taxonomy_project_id=s.project_id, taxonomy_status="approved",
        taxonomy_checksum="d" * 64, sre_run_id=pins.sre_run_id,
        sre_run_project_id=s.project_id, run_taxonomy_id=pins.sre_taxonomy_version_id,
        content_version_id=pins.sre_content_version_id,
        content_project_id=s.project_id, content_video_id=s.video_id,
        content_revision=2,
    )
    a = dict(
        id=pins.group_a_analysis_id, video_id=s.video_id,
        source_id=s.source_id, source_policy_version_id=s.source_policy_version_id,
        analysis_version="a2", state="completed",
        content_kind=None if video else s.content_kind,
    )
    b = dict(
        id=pins.group_b_analysis_id, video_id=s.video_id,
        source_id=s.source_id, source_policy_version_id=s.source_policy_version_id,
        content_type="video" if video else "carousel",
        video_a_analysis_id=pins.group_a_analysis_id if video else None,
        carousel_a_analysis_id=None if video else pins.group_a_analysis_id,
        analysis_version="b3", taxonomy_version=pins.b_taxonomy_version,
        state="completed", needs_review=False,
    )
    c = dict(
        id=pins.calculated_metric_id, post_id=s.video_id,
        snapshot_id=pins.metric_snapshot_id, calculation_config_id=818,
        score_status="complete", score_creator_component=None,
        source_id=s.source_id, source_policy_version_id=s.source_policy_version_id,
        source_metric_definition="video_views/v1", source_metric_version="1",
    )
    return {"d1": base, "a": a, "b": b, "c": c}


class _Cursor:
    def __init__(self, result):
        self.result = result

    def fetchone(self):
        return self.result


class FakeD1Connection:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []
        self.fail = None

    def execute(self, query, parameters):
        self.calls.append((query, parameters))
        if self.fail is not None:
            raise RuntimeError("password=secret")
        if query == D1_IDENTITY_SQL:
            return _Cursor(self.evidence["d1"])
        if query in (A_VIDEO_SQL, A_CAROUSEL_SQL):
            return _Cursor(self.evidence["a"])
        if query == B_ANALYSIS_SQL:
            return _Cursor(self.evidence["b"])
        if query == C_METRIC_SQL:
            return _Cursor(self.evidence["c"])
        raise AssertionError("Unexpected query — no dynamic or missing table allowed")


class LineagePinsTests(unittest.TestCase):
    def test_explicit_pointer_bridge_and_no_silent_defaults(self):
        pointer = UnifiedAnalyticsInputPointer(
            project_id=P, video_id=SCOPE.video_id, source_id=SOURCE,
            source_policy_version_id=4, content_kind="video", qdp_run_id=QDP,
            preparation_sha256=SHA, group_a_analysis_id=505,
            group_b_analysis_id=606, calculated_metric_id=707,
            sre_decision_sha256="c"*64,
        )
        pins = CanonicalLineagePins.from_pointer(
            pointer, metadata_id=101, metric_snapshot_id=202,
            sre_run_id=RUN, sre_taxonomy_version_id=303,
            sre_content_version_id=404, b_taxonomy_version="taxonomy/1",
        )
        self.assertEqual(pins, PINS)
        self.assertEqual(len(pins.identity_sha256), 64)
        for kw in (
            {"group_b_analysis_id": 0}, {"metadata_id": True},
            {"sre_taxonomy_version_id": False}, {"sre_run_id": RUN.upper()},
            {"sre_decision_sha256": "C"*64}, {"b_taxonomy_version": ""},
            {"schema_version": "lineage/v2"},
        ):
            with self.subTest(kw=kw), self.assertRaises(AnalyticsBoundaryError):
                replace(pins, **kw)
        with self.assertRaises(AnalyticsBoundaryError):
            CanonicalLineagePins.from_pointer({}, metadata_id=101,
                metric_snapshot_id=202,sre_run_id=RUN,
                sre_taxonomy_version_id=303,sre_content_version_id=404,
                b_taxonomy_version="taxonomy/1")

    def test_identity_fingerprint_changes_with_any_pin(self):
        for field, val in (
            ("metadata_id", 102), ("metric_snapshot_id", 203),
            ("sre_taxonomy_version_id", 304),
            ("sre_content_version_id", 405),
            ("group_a_analysis_id", 506),
            ("group_b_analysis_id", 607),
            ("calculated_metric_id", 708),
            ("b_taxonomy_version", "taxonomy/2"),
            ("sre_decision_sha256", "e"*64),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(PINS.identity_sha256,
                                    replace(PINS, **{field: val}).identity_sha256)
        for kw in ({"project_id": "f"*32}, {"video_id": 32},
                   {"source_id": "other"}, {"source_policy_version_id": 9},
                   {"qdp_run_id": RUN}, {"qdp_input_sha256": "f"*64},
                   {"content_kind": "photo_carousel"}):
            with self.subTest(scope=kw):
                updated = replace(PINS, scope=replace(SCOPE, **kw))
                self.assertNotEqual(updated.identity_sha256, PINS.identity_sha256)


class CanonicalD1ReaderTests(unittest.TestCase):
    def read(self, pins=PINS, evidence=None, *, as_of=AS_OF):
        conn = FakeD1Connection(rows(pins, as_of) if evidence is None else evidence)
        return resolve_canonical_lineage(conn, pins, as_of=as_of), conn

    def test_video_positive_is_linked_but_never_qdp_qualified(self):
        result, conn = self.read()
        self.assertEqual(result.schema_version, LINEAGE_SCHEMA)
        self.assertEqual(result.metric_snapshot_id, 202)
        self.assertEqual(result.calculated_metric_id, 707)
        self.assertEqual(result.a_analysis_version, "a2")
        self.assertEqual(result.sre_content_revision, 2)
        self.assertEqual(result.qdp_verification, "not_checked")
        self.assertFalse(result.qualified)
        self.assertEqual(len(result.lineage_sha256), 64)
        self.assertEqual([q for q,p in conn.calls],
                         [D1_IDENTITY_SQL, A_VIDEO_SQL, B_ANALYSIS_SQL, C_METRIC_SQL])
        self.assertEqual(conn.calls[0][1][-1], P)
        self.assertEqual(conn.calls[1][1], (505,))
        self.assertEqual(conn.calls[2][1], (606,))
        self.assertEqual(conn.calls[3][1], (707,))
        self.assertEqual(result.lineage_sha256, self.read()[0].lineage_sha256)

    def test_carousels_exact_table_and_parent_identity(self):
        for kind in ("photo_carousel", "mixed_carousel"):
            pins = replace(PINS, scope=replace(SCOPE, content_kind=kind))
            result, conn = self.read(pins)
            self.assertFalse(result.qualified)
            self.assertEqual(conn.calls[1][0], A_CAROUSEL_SQL)
            bad = rows(pins)
            bad["a"]["content_kind"] = "photo_carousel" if kind == "mixed_carousel" else "mixed_carousel"
            with self.assertRaises(AnalyticsBoundaryError) as err:
                self.read(pins, bad)
            self.assertEqual(err.exception.code, "a_carousel_kind_mismatch")
        with self.assertRaises(AnalyticsBoundaryError):
            replace(SCOPE, content_kind="image_post")

    def test_cross_project_source_policy_d1_and_metadata_denied(self):
        cases = [
            ("d1", "project_id", "f"*32, "video_project_identity_mismatch"),
            ("d1", "member_video_id", None, "not_in_project"),
            ("d1", "video_id", 32, "video_project_identity_mismatch"),
            ("d1", "content_kind", "photo_carousel", "content_kind_mismatch"),
            ("d1", "source_id", "other", "source_policy_lineage_mismatch"),
            ("d1", "policy_id", 77, "source_policy_lineage_mismatch"),
            ("d1", "policy_source_id", "other", "source_policy_lineage_mismatch"),
            ("d1", "policy_is_active", False, "source_policy_inactive"),
            ("d1", "source_status", "revoked", "source_policy_inactive"),
            ("d1", "metadata_id", 888, "source_metadata_lineage_mismatch"),
            ("d1", "metadata_policy_id", 777, "source_metadata_lineage_mismatch"),
            ("d1", "metadata_deleted_at", AS_OF, "source_metadata_lineage_mismatch"),
            ("d1", "metadata_is_current", None, "source_metadata_lineage_mismatch"),
            ("d1", "metadata_expires_at", AS_OF, "source_metadata_expired"),
            ("d1", "snapshot_video_id", 666, "metric_lineage_mismatch"),
            ("d1", "snapshot_source_id", "other", "metric_lineage_mismatch"),
            ("d1", "snapshot_policy_id", 2, "metric_lineage_mismatch"),
        ]
        for table, key, val, expected in cases:
            with self.subTest(key=key):
                data = rows()
                data[table][key] = val
                with self.assertRaises(AnalyticsBoundaryError) as err:
                    self.read(evidence=data)
                self.assertEqual(err.exception.code, expected)

    def test_wrong_sre_project_taxonomy_run_and_content_pins_denied(self):
        cases = [
            ("active_taxonomy_id", 999, "sre_taxonomy_not_current"),
            ("taxonomy_status", "draft", "sre_taxonomy_not_current"),
            ("taxonomy_project_id", "f"*32, "sre_taxonomy_not_current"),
            ("taxonomy_checksum", None, "sre_taxonomy_fingerprint_missing"),
            ("sre_run_project_id", "f"*32, "sre_run_lineage_mismatch"),
            ("run_taxonomy_id", 999, "sre_run_lineage_mismatch"),
            ("content_version_id", 666, "sre_content_version_mismatch"),
            ("content_video_id", 666, "sre_content_version_mismatch"),
            ("content_project_id", "f"*32, "sre_content_version_mismatch"),
            ("content_revision", None, "sre_content_version_mismatch"),
        ]
        for key,val,code in cases:
            with self.subTest(key=key):
                r = rows()
                r["d1"][key] = val
                with self.assertRaises(AnalyticsBoundaryError) as e:
                    self.read(evidence=r)
                self.assertEqual(e.exception.code, code)

    def test_a_and_b_cross_video_policy_version_parent_taxonomy(self):
        cases = [
            ("a", "id", 700, "a_lineage_mismatch"),
            ("a", "video_id", 333, "a_lineage_mismatch"),
            ("a", "source_id", "other", "a_lineage_mismatch"),
            ("a", "source_policy_version_id", 999, "a_lineage_mismatch"),
            ("a", "analysis_version", "", "a_version_unknown"),
            ("b", "id", 800, "b_lineage_mismatch"),
            ("b", "video_id", 333, "b_lineage_mismatch"),
            ("b", "source_id", "other", "b_lineage_mismatch"),
            ("b", "source_policy_version_id", 999, "b_lineage_mismatch"),
            ("b", "content_type", "carousel", "a_b_lineage_mismatch"),
            ("b", "video_a_analysis_id", 999, "a_b_lineage_mismatch"),
            ("b", "carousel_a_analysis_id", 505, "a_b_lineage_mismatch"),
            ("b", "analysis_version", "", "b_version_unknown"),
            ("b", "taxonomy_version", "taxonomy/other", "b_taxonomy_version_mismatch"),
        ]
        for table,key,val,code in cases:
            with self.subTest(table=table,key=key):
                data = rows()
                data[table][key] = val
                with self.assertRaises(AnalyticsBoundaryError) as e:
                    self.read(evidence=data)
                self.assertEqual(e.exception.code, code)

    def test_c_exact_snapshot_and_metric_version_without_recomputing_score(self):
        cases = [
            ("id", 888), ("post_id", 888), ("snapshot_id", 888),
            ("source_id", "other"), ("source_policy_version_id", 888),
            ("source_metric_definition", "reach"),
            ("source_metric_version", "2"),
        ]
        for key,value in cases:
            with self.subTest(key=key):
                r = rows()
                r["c"][key] = value
                with self.assertRaises(AnalyticsBoundaryError) as e:
                    self.read(evidence=r)
                self.assertEqual(e.exception.code, "c_metric_lineage_mismatch")
        r = rows()
        r["c"]["calculation_config_id"] = None
        with self.assertRaises(AnalyticsBoundaryError) as e:
            self.read(evidence=r)
        self.assertEqual(e.exception.code, "c_config_reference_missing")
        import intelligence.unified_analytics as uae
        self.assertFalse(hasattr(uae, "compute_c_metrics"))

    def test_missing_records_and_db_failures_fail_closed(self):
        for table, code in (
            ("d1", "project_not_found"), ("a", "a_lineage_mismatch"),
            ("b", "b_lineage_mismatch"), ("c", "c_metric_lineage_mismatch"),
        ):
            data = rows()
            data[table] = None
            with self.subTest(table=table), self.assertRaises(AnalyticsBoundaryError) as e:
                self.read(evidence=data)
            self.assertEqual(e.exception.code, code)
        conn = FakeD1Connection(rows())
        conn.fail = True
        with self.assertRaises(AnalyticsBoundaryError) as e:
            resolve_canonical_lineage(conn,PINS,as_of=AS_OF)
        self.assertEqual(e.exception.code, "lineage_read_unavailable")
        self.assertNotIn("password",str(e.exception))
        with self.assertRaises(AnalyticsBoundaryError):
            resolve_canonical_lineage(None,PINS,as_of=AS_OF)
        with self.assertRaises(AnalyticsBoundaryError):
            resolve_canonical_lineage(conn,{},as_of=AS_OF)

    def test_required_utc_and_dict_rows(self):
        conn = FakeD1Connection(rows())
        for invalid in (
            datetime(2026,10,26), AS_OF.astimezone(timezone(timedelta(hours=2))),
        ):
            with self.subTest(as_of=invalid), self.assertRaises(AnalyticsBoundaryError):
                resolve_canonical_lineage(conn,PINS,as_of=invalid)
        data = rows()
        data["d1"] = (P,31)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            self.read(evidence=data)
        self.assertEqual(e.exception.code,"invalid_db_row")

    def test_lineage_output_hash_is_pinned_to_db_versions(self):
        before=self.read()[0]
        r=rows()
        r["a"]["analysis_version"]="a3"
        later=self.read(evidence=r)[0]
        self.assertNotEqual(before.lineage_sha256,later.lineage_sha256)
        self.assertNotEqual(before.a_analysis_version,later.a_analysis_version)
        r=rows()
        r["d1"]["metric_version"]="2"
        r["c"]["source_metric_version"]="2"
        self.assertNotEqual(before.lineage_sha256,self.read(evidence=r)[0].lineage_sha256)


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),
                     "Disposable PG17 required for main-schema SQL compatibility")
class CanonicalLineagePG17Tests(unittest.TestCase):
    def test_all_static_read_sql_parses_in_real_pg17(self):
        if os.environ.get("CM_TEST_PG_DSN") == os.environ.get("CM_DB_DSN"):
            self.fail("Refuse accidental production database")
        import psycopg
        from intelligence import migrate
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            version=int(conn.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=version<180000, "PG17 required")
            migrate.apply_migrations(conn)
            for stmt,args in (
                (D1_IDENTITY_SQL,(31,SOURCE,4,101,202,303,RUN,404,P)),
                (A_VIDEO_SQL,(505,)),
                (A_CAROUSEL_SQL,(505,)),
                (B_ANALYSIS_SQL,(606,)),
                (C_METRIC_SQL,(707,)),
            ):
                with self.subTest(query=stmt.strip().splitlines()[0]):
                    conn.execute("EXPLAIN "+stmt,args).fetchall()


if __name__ == "__main__":
    unittest.main()
