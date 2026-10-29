"""QDP FIX-15 irreversible revocation, signed fanout and refresh validation."""
import unittest
import uuid
from intelligence.qualified_data.contracts import QDPError,sha256
from intelligence.qualified_data.invalidation import invalidate_run,explicit_refresh,SCHEMA
from intelligence.qualified_data.revocation_delivery import verify_invalidation_event
from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer


class InvalidationsPure(unittest.TestCase):
    def test_bad_origin_and_reason_rejected_before_database(self):
        class NoDB:
            def __call__(self):
                raise AssertionError("Untrusted scope must not reach DB")
        kwargs={"project":"a"*32,"run_id":str(uuid.uuid4()),"provenance_sha256":"b"*64,
                "origin":"operator","reason_code":"operator_revoked","connect":NoDB()}
        for change in ({"origin":"browser"},{"reason_code":"any_text"},
                       {"analysis_group":"A"},{"group":"D"},{"video_id":True},
                       {"provenance_sha256":"0"},{"provenance_sha256":"A"*64}):
            with self.subTest(change=change),self.assertRaises((QDPError,TypeError)):
                invalidate_run(**dict(kwargs,**change))

    def test_invalidation_version_and_refresh_requires_canonical_project(self):
        self.assertEqual(SCHEMA,"qdp-invalidation/v1")
        with self.assertRaises(QDPError):
            explicit_refresh("not-project",str(uuid.uuid4()),"new-key-123",
                             connect=lambda:None)

    def test_reject_tampered_compensation(self):
        event_id=str(uuid.uuid4())
        run=str(uuid.uuid4())
        event={"schema_version":"qdp-event/v1","event_type":"preparation_invalidated",
               "project_id":"a"*32,"run_id":run,"video_id":11,
               "analysis_group":"A","input_sha256":"b"*64,
               "invalidation_generation":1,"origin":"human_review",
               "reason_code":"human_veto","provenance_sha256":"c"*64}
        row=dict(event_id=uuid.UUID(event_id),run_id=uuid.UUID(run),
                 project_id="a"*32,video_id=11,analysis_group="A",
                 event_type="preparation_invalidated",payload=event,
                 payload_sha256=sha256(event))
        good=verify_invalidation_event(row,"group_a")
        self.assertIsInstance(good,QDPHandoffPointer)
        self.assertEqual(good.event_id,event_id)
        for changed in ({"event_type":"preparation_ready"},
                        {"provenance_sha256":"not-a-hash"},
                        {"invalidation_generation":0},
                        {"analysis_group":"B"},
                        {"project_id":"f"*32},
                        {"input_sha256":"wrong"}):
            with self.subTest(patch=changed),self.assertRaises(QDPError):
                tampered=dict(row,payload=dict(event,**changed))
                verify_invalidation_event(tampered,"group_a")

    def test_cross_group_compensation_is_never_accepted(self):
        run=str(uuid.uuid4())
        event={"schema_version":"qdp-event/v1","event_type":"preparation_invalidated",
               "project_id":"a"*32,"run_id":run,"video_id":1,
               "analysis_group":"A","input_sha256":"b"*64,
               "invalidation_generation":4,"origin":"operator",
               "reason_code":"operator_revoked","provenance_sha256":"c"*64}
        row=dict(event_id=uuid.uuid4(),run_id=uuid.UUID(run),project_id="a"*32,
                 video_id=1,analysis_group="A",
                 event_type="preparation_invalidated",payload=event,
                 payload_sha256=sha256(event))
        with self.assertRaises(QDPError):
            verify_invalidation_event(row,"group_c")


if __name__=="__main__":
    unittest.main()
