"""No-network, adversarial FIX-11-B GitHub CI evidence tests."""
from __future__ import annotations
from copy import deepcopy
from unittest.mock import patch
import unittest

from intelligence.taxonomy_builder import release_ci_evidence as ci

HEAD="a"*40
MAIN="b"*40


def fixture():
    pr={"number":32,"state":"open","merged":False,
        "head":{"sha":HEAD},"base":{"ref":"main"},"draft":True}
    runs=[]
    jobs={}
    for number,name in enumerate(ci.REQUIRED_WORKFLOWS,1):
        run={"name":name,"id":number,"run_attempt":1,"head_sha":HEAD,
             "event":"pull_request","status":"completed","conclusion":"success",
             "created_at":"2026-10-27T10:00:00Z"}
        runs.append(run)
        jobs[number]=[{"name":name+" / job","run_id":number,
                       "status":"completed","conclusion":"success"}]
    return pr,runs,jobs


class RTBReleaseCIEvidence(unittest.TestCase):

    def test_all_four_jobs_success_is_ci_only_not_release_ready(self):
        pr,runs,jobs=fixture()
        out=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=MAIN,behind=0)
        self.assertTrue(out["observed_workflow_success"],out["issues"])
        self.assertEqual(len(out["workflows"]),4)
        self.assertFalse(out["release_ready"])
        self.assertTrue(out["manual_release_approval_required"])
        self.assertFalse(out["rtb_sp_v2_t28_t29_verified"])

    def test_running_queued_or_skipped_workflow_fails_closed(self):
        for status,conclusion in (("queued",None),
                                  ("in_progress",None),
                                  ("completed","cancelled"),
                                  ("completed","skipped"),
                                  ("completed","failure")):
            with self.subTest(status=status,conclusion=conclusion):
                pr,runs,jobs=fixture()
                runs[0].update({"status":status,"conclusion":conclusion})
                result=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,
                                   main_sha=MAIN,behind=0)
                self.assertFalse(result["observed_workflow_success"])
                self.assertTrue(any(x.startswith("workflow_not_success:")
                                    for x in result["issues"]))

    def test_green_old_run_cannot_hide_new_failed_attempt(self):
        pr,runs,jobs=fixture()
        rerun=deepcopy(runs[0])
        rerun.update({"id":999,"run_attempt":2,
                      "created_at":"2026-10-27T10:01:00Z",
                      "status":"completed","conclusion":"failure"})
        runs.append(rerun)
        jobs[999]=[{"name":"retry","run_id":999,"status":"completed",
                    "conclusion":"failure"}]
        out=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=MAIN,behind=0)
        self.assertFalse(out["observed_workflow_success"])
        self.assertEqual(out["workflows"][ci.REQUIRED_WORKFLOWS[0]]["id"],999)

    def test_wrong_head_foreign_event_and_empty_jobs_rejected(self):
        pr,runs,jobs=fixture()
        runs[0]["head_sha"]="c"*40
        runs[1]["event"]="push"
        jobs[runs[2]["id"]]=[]
        out=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=MAIN,behind=0)
        self.assertFalse(out["observed_workflow_success"])
        self.assertIn("missing_required_workflow:"+ci.REQUIRED_WORKFLOWS[0],
                      out["issues"])
        self.assertIn("missing_required_workflow:"+ci.REQUIRED_WORKFLOWS[1],
                      out["issues"])
        self.assertIn("workflow_jobs_missing:"+ci.REQUIRED_WORKFLOWS[2],
                      out["issues"])

    def test_main_behind_or_changed_job_identity_blocks(self):
        pr,runs,jobs=fixture()
        out=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=MAIN,behind=1)
        self.assertIn("feature_behind_latest_main",out["issues"])
        jobs[4][0]["run_id"]=1111
        out=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=MAIN,behind=0)
        self.assertTrue(any("workflow_job_not_success:" in x for x in out["issues"]))
        pr["base"]["ref"]="not-main"
        out=ci.evaluate(pr=pr,runs=runs,jobs_by_id=jobs,main_sha=MAIN,behind=0)
        self.assertIn("pr_not_targeting_main",out["issues"])

    def test_read_only_github_api_uses_exact_pr_sha_and_real_jobs(self):
        pr,runs,jobs=fixture()
        called=[]
        def fake_api(path,token):
            called.append(path)
            if path.endswith("/pulls/32"):return pr
            if path.endswith("/git/ref/heads/main"):
                return {"object":{"sha":MAIN}}
            if "/actions/runs?" in path:
                self.assertIn("head_sha="+HEAD,path)
                return {"total_count":len(runs),"workflow_runs":runs}
            if "/actions/runs/" in path:
                ident=int(path.split("/actions/runs/")[1].split("/")[0])
                return {"total_count":1,"jobs":jobs[ident]}
            if "/compare/" in path:
                self.assertIn(HEAD+"..."+MAIN,path)
                return {"ahead_by":0}
            raise AssertionError("Unexpected GitHub route "+path)
        with patch.object(ci,"_api",side_effect=fake_api):
            result=ci.fetch_live(ci.DEFAULT_REPOSITORY,32,"test-only")
        self.assertTrue(result["github_api_live_verified"])
        self.assertTrue(result["observed_workflow_success"],result["issues"])
        self.assertFalse(result["release_ready"])
        self.assertEqual(len(called),8)

    def test_unknown_or_paginated_github_evidence_cannot_pass(self):
        pr,runs,jobs=fixture()
        def fake_api(path,token):
            if path.endswith("/pulls/32"):return pr
            if path.endswith("/git/ref/heads/main"):
                return {"object":{"sha":MAIN}}
            if "/actions/runs?" in path:
                return {"total_count":101,"workflow_runs":runs}
            raise AssertionError(path)
        with patch.object(ci,"_api",side_effect=fake_api):
            with self.assertRaisesRegex(RuntimeError,"requires_pagination"):
                ci.fetch_live(ci.DEFAULT_REPOSITORY,32,"")


if __name__=="__main__":
    unittest.main()
