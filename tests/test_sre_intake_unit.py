"""S3 pure metadata/right/retention contracts; never access a live source."""
import unittest
from datetime import datetime, timedelta, timezone

from intelligence.relevance.content_builder import (
    build_metadata_envelope, intake_fingerprint, metadata_payload,
)
from intelligence.relevance.source_policy import expiry_for_intake, verified_rights
from intelligence.relevance.v1_contracts import SREContractError, fingerprint

PID = "a" * 32
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def source():
    return {"id": "fixture_manual", "status": "enabled",
            "can_store_metadata": True, "can_analyze_media": False,
            "retention_days": 30}


def policy():
    return {"source_id": "fixture_manual", "id": 1,
            "is_active": True, "reviewed_at": NOW,
            "permissions": {"can_store_metadata": True,
                            "can_semantic_classify": True,
                            "can_external_llm": False},
            "retention_policy": {"retention_days": 10}}


class S3PureContracts(unittest.TestCase):
    def test_current_verified_rights_never_imply_external_permission(self):
        rights = verified_rights(source(), policy())
        self.assertEqual(rights, {"can_store_metadata": True,
            "can_semantic_classify": True, "can_external_llm": False,
            "can_analyze_media": False})
        with self.assertRaises(SREContractError):
            verified_rights(source(), policy(), external_llm=True)
        p = policy()
        p["permissions"]["can_external_llm"] = True
        self.assertTrue(verified_rights(source(), p, external_llm=True)["can_external_llm"])

    def test_denial_matrix_defaults_to_false(self):
        mutations = [
            lambda s,p: s.update(status="disabled"),
            lambda s,p: s.update(can_store_metadata=False),
            lambda s,p: p.update(is_active=False),
            lambda s,p: p.update(reviewed_at=None),
            lambda s,p: p.update(source_id="other"),
            lambda s,p: p["permissions"].pop("can_semantic_classify"),
            lambda s,p: p["permissions"].update(can_semantic_classify="true"),
            lambda s,p: p["permissions"].update(can_store_metadata=False),
            lambda s,p: p.update(permissions={}),
            lambda s,p: p.update(permissions=None),
        ]
        for i, change in enumerate(mutations):
            with self.subTest(i=i), self.assertRaises(SREContractError):
                s,p=source(),policy()
                change(s,p)
                verified_rights(s,p)

    def test_m08_live_policy_rechecks_retention_json_types(self):
        # Reviewed policy can be edited later; a malformed retention value
        # must never act as infinite retention on a saved content revision.
        for value in ("forever", "14", True, 0, -1, 36501, 1.25):
            with self.subTest(value=value), self.assertRaises(SREContractError) as err:
                p=policy()
                p["retention_policy"]["retention_days"]=value
                verified_rights(source(),p)
            self.assertEqual(err.exception.code,"rights_denied")
        for bad in (None, "bad", [], 14):
            with self.subTest(bad=bad), self.assertRaises(SREContractError):
                p=policy();p["retention_policy"]=bad
                verified_rights(source(),p)
        p=policy();p["retention_policy"]={}
        self.assertTrue(verified_rights(source(),p)["can_semantic_classify"])

    def test_retention_uses_shortest_cap(self):
        obs = {"expires_at": NOW + timedelta(days=3), "deleted_at": None,
               "is_current": True}
        self.assertEqual(expiry_for_intake(source(),policy(),obs,now=NOW),
                         obs["expires_at"])
        self.assertEqual(expiry_for_intake(source(),policy(),None,now=NOW),
                         NOW+timedelta(days=10))

    def test_revocation_expiry_and_invalid_retention(self):
        for obs in (
            {"expires_at": None,"deleted_at": NOW,"is_current": False},
            {"expires_at": NOW,"deleted_at": None,"is_current": True},
        ):
            with self.assertRaises(SREContractError):
                expiry_for_intake(source(),policy(),obs,now=NOW)
        for value in (0,-1,1.2,True,"14",36501):
            s=source()
            s["retention_days"]=value
            with self.assertRaises(SREContractError):
                expiry_for_intake(s,policy(),None,now=NOW)

    def test_never_fall_back_to_catalog_metadata(self):
        self.assertEqual(metadata_payload(None), {
            "title": None, "description": None, "hashtags": []})
        obs = {"title": "Allowed title", "description": "Description"}
        a = metadata_payload(obs)
        self.assertEqual(a["title"], "Allowed title")
        self.assertEqual(a["hashtags"], [])

    def test_metadata_envelope_exact_and_fingerprint_idempotent(self):
        args = dict(project=PID,video=5,source="fixture_manual",policy_id=1,
            content_version=7,rights=verified_rights(source(),policy()),
            observation={"title": "Title", "description": None},
            discovery_id=33,search_query_id=None,source_request_id=None,
            expiry=NOW+timedelta(days=10))
        snap=build_metadata_envelope(**args)
        self.assertEqual(snap["content_sha256"],fingerprint({
            k:snap["data"][k] for k in ("title","description","hashtags")}))
        self.assertEqual(snap["completeness"],"metadata_only")
        self.assertEqual(snap["media_asset_id"],None)
        key=intake_fingerprint(project=PID,video=5,discovery_id=33,
            source="fixture_manual",policy_id=1,observation_id=10,
            content_sha256=snap["content_sha256"])
        self.assertEqual(key,intake_fingerprint(project=PID,video=5,discovery_id=33,
            source="fixture_manual",policy_id=1,observation_id=10,
            content_sha256=snap["content_sha256"]))
        self.assertNotEqual(key,intake_fingerprint(project=PID,video=5,discovery_id=34,
            source="fixture_manual",policy_id=1,observation_id=10,
            content_sha256=snap["content_sha256"]))


if __name__ == "__main__":
    unittest.main()
