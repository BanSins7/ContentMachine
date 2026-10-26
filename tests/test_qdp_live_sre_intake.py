"""FIX-01 fail-closed SRE intake tests; no external API / live provider."""
import unittest
import uuid

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.intake import (
    require_sre_live_schema, read_live_sre_run, get_live_result_rows,
    read_eligible_sre_run_list,
)


PID = "a" * 32
RUN = str(uuid.uuid4())
CATEGORY = str(uuid.uuid4())
CONTENT_SHA = "a" * 64


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
    def fetchone(self):
        return self.rows if isinstance(self.rows, dict) else None
    def fetchall(self):
        return self.rows if isinstance(self.rows, list) else []


class _DB:
    def __init__(self, live_column=True, run=None, results=None):
        self.live_column = live_column
        self.run = run
        self.results = results or []
        self.calls = []
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "FROM pg_attribute" in sql:
            return _Cursor({"available": self.live_column})
        if "SELECT r.id,r.project_id,r.taxonomy_version_id" in sql:
            return _Cursor(self.run)
        if "FROM sre_taxonomy_categories" in sql:
            return _Cursor([{"category_key": CATEGORY, "review_status": "approved"}])
        if "FROM sre_classification_results result" in sql:
            assert "sr.execution_mode='live'" in sql
            assert "sr.status='completed'" in sql
            return _Cursor(self.results)
        if "SELECT created_at,id FROM sre_classification_runs" in sql:
            return _Cursor(None)
        if "SELECT r.id,r.taxonomy_version_id,r.created_at" in sql:
            assert "r.execution_mode='live'" in sql
            return _Cursor([])
        raise AssertionError("Unexpected SQL: " + sql)


def _run(**changes):
    row = {
        "id": uuid.UUID(RUN), "project_id": PID,
        "taxonomy_version_id": 1, "status": "completed",
        "execution_mode": "live", "taxonomy_status": "approved",
        "resolved_models": {"classifier": "anthropic/claude-opus-5.5"},
        "dataset_snapshot": {
            "taxonomy_sha256": "b" * 64,
            "items": [{"video_id": 11, "content_version_id": 5,
                       "content_sha256": CONTENT_SHA,
                       "hypothesis_keys": [CATEGORY]}],
        },
    }
    row.update(changes)
    return row


def _result(**changes):
    row = {
        "result_id": 42, "video_id": 11, "category_key": CATEGORY,
        "result_taxonomy_version_id": 1, "content_version_id": 5,
        "model_decision": "match", "effective_decision": "match",
        "category_review_status": "approved", "project_video_linked": True,
        "content_readable": True,
        "result_content_sha256": CONTENT_SHA,
        "content_snapshot": {"title": "fitness"},
        "reason": "Model evidence", "evidence": [{"source": "title", "quote": "fitness"}],
        "stage_trace": [{"provider": "openrouter", "model_id": "anthropic/claude-opus-5.5"}],
    }
    row.update(changes)
    return row


class SreLiveIntakeTests(unittest.TestCase):
    def _failure(self, db, code, fn=None):
        fn = fn or (lambda: read_live_sre_run(db, PID, RUN))
        with self.assertRaises(QDPError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_missing_live_mode_is_explicit_503(self):
        db = _DB(live_column=False, run=_run())
        exc = self._failure(db, "sre_live_schema_unavailable")
        self.assertEqual(exc.status, 503)
        self.assertEqual(len(db.calls), 1)

    def test_completed_offline_and_legacy_rejected(self):
        for mode in ("offline_fixture", "legacy"):
            with self.subTest(mode=mode):
                self._failure(_DB(run=_run(execution_mode=mode)), "sre_not_live")

    def test_not_completed_rejected(self):
        for status in ("queued", "running", "partial", "failed", "cancelled"):
            with self.subTest(status=status):
                self._failure(_DB(run=_run(status=status)), "sre_not_completed")

    def test_unapproved_taxonomy_rejected(self):
        self._failure(_DB(run=_run(taxonomy_status="draft")), "sre_not_approved")

    def test_wrong_project_and_missing_run_rejected(self):
        db = _DB(run=None)
        exc = self._failure(db, "sre_not_found")
        self.assertEqual(exc.status, 404)
        self.assertEqual(db.calls[-1][1], (PID, RUN))
        self._failure(_DB(run=_run(project_id="b" * 32)), "sre_run_mismatch",
                      lambda: get_live_result_rows(
                          _DB(run=_run(project_id="b" * 32)), PID, RUN))

    def test_fake_or_empty_model_rejected(self):
        for models in ({}, {"classifier": "test/fake-opus"},
                       {"classifier": ["offline_fixture"]}):
            with self.subTest(models=models):
                self._failure(_DB(run=_run(resolved_models=models)),
                              "sre_model_unverified" if not models else "sre_synthetic_model")

    def test_live_valid_run_and_result_rows(self):
        db = _DB(run=_run(), results=[_result()])
        run = read_live_sre_run(db, PID, RUN)
        results = get_live_result_rows(db, PID, RUN, verified_run=run)
        from intelligence.qualified_data.intake import verify_live_coverage
        self.assertEqual(verify_live_coverage(db,run,results)["expected_pairs"],1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["effective_decision"], "match")
        self.assertEqual(sum("FROM sre_classification_results result" in q
                             for q, _ in db.calls), 1)

    def test_no_silent_unapproved_or_foreign_taxonomy_rows(self):
        for changes, code in (
            ({"category_review_status": "proposed"}, "sre_unapproved_category"),
            ({"result_taxonomy_version_id": 123}, "sre_taxonomy_mismatch"),
            ({"project_video_linked": False}, "sre_unlinked_video"),
        ):
            with self.subTest(changes=changes):
                self._failure(_DB(run=_run(), results=[_result(**changes)]),
                              code, lambda changes=changes:
                              get_live_result_rows(
                                  _DB(run=_run(), results=[_result(**changes)]),
                                  PID, RUN))

    def test_duplicate_result_keys_rejected(self):
        row = _result()
        self._failure(_DB(run=_run(), results=[row, dict(row, result_id=43)]),
                      "sre_duplicate_result",
                      lambda: get_live_result_rows(
                          _DB(run=_run(), results=[row, dict(row, result_id=43)]),
                          PID, RUN))

    def test_empty_manifest_remains_rejected_until_fix02(self):
        self._failure(_DB(run=_run(), results=[]), "sre_empty",
                      lambda: get_live_result_rows(_DB(run=_run()), PID, RUN))

    def test_get_eligible_list_is_read_only_and_strict(self):
        db = _DB()
        self.assertEqual(read_eligible_sre_run_list(db, PID), {
            "runs": [], "next_cursor": None})
        self.assertTrue(all(not sql.strip().lower().startswith(("insert", "update", "delete"))
                            for sql, _ in db.calls))
        self._failure(db, "invalid_limit",
                      lambda: read_eligible_sre_run_list(db, PID, limit=0))

    def test_picker_skips_unverified_pages_without_hiding_later_runs(self):
        from datetime import datetime, timezone, timedelta
        dates=[datetime(2026,10,26,9,0,tzinfo=timezone.utc)-timedelta(seconds=i)
               for i in range(53)]
        fake=[{"id":uuid.uuid4(),"taxonomy_version_id":1,"created_at":dt,
               "finished_at":dt,"resolved_models":{"classifier":"offline_fixture"}}
              for dt in dates[:50]]
        good=[{"id":uuid.uuid4(),"taxonomy_version_id":1,"created_at":dt,
               "finished_at":dt,"resolved_models":{"classifier":"opus-5.5"}}
              for dt in dates[50:]]
        all_rows=fake+good

        class PickerDB(_DB):
            def execute(self, sql, params=None):
                if "SELECT r.id,r.taxonomy_version_id,r.created_at" in sql:
                    n=params[-1]
                    anchor=params[1:4]
                    rows=all_rows
                    if anchor[0] is not None:
                        rows=[x for x in rows
                              if (x["created_at"],x["id"]) < (anchor[0],anchor[2])]
                    return _Cursor(rows[:n])
                return super().execute(sql,params)

        db=PickerDB()
        page=read_eligible_sre_run_list(db,PID,limit=2)
        self.assertEqual([x["id"] for x in page["runs"]],[good[0]["id"],good[1]["id"]])
        self.assertIsNotNone(page["next_cursor"])

    def test_invalid_run_id_rejected_without_sql(self):
        db = _DB()
        self._failure(db, "invalid_id",
                      lambda: read_live_sre_run(db, PID, "xyz"))
        self.assertFalse(db.calls)


if __name__ == "__main__":
    unittest.main()
