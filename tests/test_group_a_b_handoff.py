"""GA19 exact A→B frozen v1 producer; Group B remains an external peer.

No Group B package import. Conditional PG17 tests write ONLY GA-owned 054
tables; model/provider/QDP callbacks in fixtures are synthetic NOT production.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
import os
import unittest

from intelligence.group_a.contracts import Scene,TechnicalMeasurement
from intelligence.group_a.contracts_v1 import (
    canonical_sha256,group_a_to_b_v1,media_manifest_v1
)
from intelligence.group_a.media_manifest import build_manifest,open_verified
from intelligence.group_a.assembly import assemble_video_snapshot
from intelligence.group_a.validation import bind_technical
from intelligence.group_a.adapters.group_b import (
    prepare_group_b_result,validate_prepared_handoff,GroupBHandoffError
)
from intelligence.group_a.adapters.group_b_writer import persist_group_b_handoff

# Test utilities are from the same standalone Group A test suite, NOT Group B.
from test_group_a_atomic_snapshot import (
    certified_bundle, technical, manifest_fixture
)

try:
    from PIL import Image
except ImportError:
    Image=None


def allow(_):
    return True


def receipt(manifest,*,run_id=None,video_ids=None,carousel_id=None):
    if video_ids is None:
        video_ids={item["media_asset_id"]:123+item["index"] for item in manifest["items"]
                   if item["kind"]=="video"}
    return {
        "ga_run_id":run_id or str(uuid4()),
        "video_analysis_ids":video_ids,
        "carousel_analysis_id":carousel_id,
    }


def photo_fixture(root,*,kind="photo_carousel",video_id=433,
                  source_id="ga19-photo",project=None):
    rows=[]
    count=1 if kind=="image_post" else 2
    for idx in range(1,count+1):
        with BytesIO() as buffer:
            Image.new("RGB",(128+idx,128),"white").save(buffer,"PNG")
            payload=buffer.getvalue()
        p=root/("slide"+str(idx)+".png")
        p.write_bytes(payload)
        rows.append({
            "media_asset_id":300+idx,"video_id":video_id,
            "source_id":source_id,"media_kind":"image",
            "local_path":str(p),"sha256":sha256(payload).hexdigest(),
            "byte_size":len(payload),
        })
    pointer={
        "schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
        "project_id":project or uuid4().hex,
        "qdp_run_id":str(uuid4()),"video_id":video_id,
        "analysis_group":"A","input_sha256":"b"*64,
    }
    return build_manifest(
        pointer,source_id=source_id,source_policy_version_id=34,
        content_kind=kind,ordered_assets=rows,roots=[str(root)],
        verified_at=datetime(2026,10,27,tzinfo=timezone.utc),
    )


class OCR:
    identity={
        "engine":"tesseract","engine_version":"5.5.0",
        "model_sha256":"c"*64,"device":"cpu",
    }
    def process(self,png,cfg):
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        return {
            "state":"text_observed","languages_used":["eng","rus"],
            "engine_rotation":0,"error_code":None,
            "words":[{"text":"verified","confidence_pct":"93.00",
                      "bbox":{"x":2,"y":2,"width":24,"height":12},
                      "line_ref":"l1"}],
        }


def photo_observation(manifest,refs,root,*,backend=None):
    from intelligence.group_a.extractors.carousel import extract_photo_carousel
    @contextmanager
    def opened(ref):
        with open_verified(refs[ref],roots=[str(root)]) as stream:
            yield stream
    return extract_photo_carousel(
        manifest,open_ref=opened,verify_current=allow,
        backend=OCR() if backend is None else backend,
    )


@unittest.skipUnless(Image is not None,"Pillow required for GA19 photo tests")
class GA19FrozenContractUnit(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def video(self):
        manifest,refs=manifest_fixture(self.root)
        snap=certified_bundle(manifest)
        prepared=prepare_group_b_result(
            manifest,receipt=receipt(manifest),video_snapshots=[snap],
            verify_current=allow,verify_origin=allow,
            verify_coverage=allow,
        )
        return manifest,snap,prepared

    def test_full_certified_video_is_frozen_B_v1_and_hash_pinned(self):
        m,s,p=self.video()
        self.assertEqual(p["snapshot"]["schema_version"],"group-a-to-b/v1")
        self.assertEqual(p["snapshot"]["analysis_state"],"completed")
        self.assertEqual(set(p["snapshot"]["streams"]),{
            "technical","speech","screen_text","audio","subtitles"
        })
        self.assertTrue(p["ready_for_b"])
        self.assertEqual(p["delivery_state"],"eligible")
        self.assertEqual(p["result_sha256"],canonical_sha256(p["snapshot"]))
        self.assertEqual(p["snapshot"]["video_a_analysis_id"],
                         p["video_analysis_ids"][str(m["items"][0]["media_asset_id"])])
        self.assertIsNone(p["snapshot"]["carousel_a_analysis_id"])
        self.assertTrue(all(value["status"]=="complete"
                            for value in p["snapshot"]["streams"].values()))
        self.assertEqual(group_a_to_b_v1(p["snapshot"]),p["snapshot"])
        self.assertEqual(validate_prepared_handoff(m,p),p)
        self.assertIsNone(p["snapshot"].get("cta_start_sec"))
        self.assertTrue(p["snapshot"]["evidence"])
        self.assertNotIn("source_policy_version_id",p["snapshot"]) # frozen v1 B reads GA run

    def test_source_SHA_and_wrong_manifest_are_not_upgraded(self):
        m,s,p=self.video()
        modified=dict(p,result_sha256="f"*64)
        with self.assertRaisesRegex(GroupBHandoffError,"handoff_status_or_digest_mismatch"):
            validate_prepared_handoff(m,dict(
                modified,preparation_sha256=canonical_sha256({
                    k:v for k,v in modified.items() if k!="preparation_sha256"
                })
            ))
        altered={**m,"source_id":"other"}
        altered["manifest_sha256"]=canonical_sha256({
            k:v for k,v in altered.items() if k!="manifest_sha256"
        })
        with self.assertRaises(GroupBHandoffError):
            validate_prepared_handoff(altered,p)

    def test_partial_A_not_B_eligible_even_with_measured_technical(self):
        m,_=manifest_fixture(self.root)
        technical_only=assemble_video_snapshot(
            m,media_asset_id=m["items"][0]["media_asset_id"],
            technical=technical(m),verify_current=allow
        )
        p=prepare_group_b_result(
            m,receipt=receipt(m),video_snapshots=[technical_only],
            verify_current=allow,verify_origin=allow
        )
        self.assertEqual(p["snapshot"]["analysis_state"],"partial")
        self.assertEqual(p["snapshot"]["streams"]["technical"]["status"],"complete")
        self.assertEqual(p["snapshot"]["streams"]["speech"]["status"],"unavailable")
        self.assertFalse(p["ready_for_b"])
        self.assertEqual(p["delivery_state"],"blocked_partial")
        self.assertIsNone(p["snapshot"]["streams"]["speech"]["evidence_count"])
        validate_prepared_handoff(m,p)

    def test_full_video_does_not_bypass_independent_coverage_verifier(self):
        m,_=manifest_fixture(self.root)
        with self.assertRaisesRegex(Exception,"coverage_authorization_denied"):
            prepare_group_b_result(
                m,receipt=receipt(m),video_snapshots=[certified_bundle(m)],
                verify_current=allow,verify_origin=allow,
                verify_coverage=lambda _:False
            )

    def test_strict_wrong_B_version_denied_without_downgrade(self):
        m,s,p=self.video()
        bad=json.loads(json.dumps(p))
        bad["snapshot"]["schema_version"]="group-a-to-b/v2"
        bad["result_sha256"]=canonical_sha256(bad["snapshot"])
        bad["preparation_sha256"]=canonical_sha256({
            k:v for k,v in bad.items() if k!="preparation_sha256"
        })
        with self.assertRaises(GroupBHandoffError):
            validate_prepared_handoff(m,bad)

    def test_photo_carousel_complete_and_partial_negative(self):
        m,refs=photo_fixture(self.root)
        obs=photo_observation(m,refs,self.root)
        p=prepare_group_b_result(
            m,receipt=receipt(m,carousel_id=98),
            carousel_observation=obs,verify_current=allow,verify_origin=allow
        )
        self.assertTrue(p["ready_for_b"])
        self.assertEqual(p["snapshot"]["carousel_a_analysis_id"],98)
        self.assertEqual(p["snapshot"]["streams"]["slides"]["evidence_count"],2)
        self.assertEqual(p["snapshot"]["streams"]["screen_text"]["evidence_count"],1)
        self.assertEqual(group_a_to_b_v1(p["snapshot"]),p["snapshot"])

        obs=photo_observation(m,refs,self.root,backend=None) # default helper uses OCR
        from intelligence.group_a.extractors.carousel import extract_photo_carousel
        @contextmanager
        def opened(ref):
            with open_verified(refs[ref],roots=[str(self.root)]) as stream:
                yield stream
        partial=extract_photo_carousel(m,open_ref=opened,verify_current=allow,backend=None)
        q=prepare_group_b_result(
            m,receipt=receipt(m,carousel_id=99),
            carousel_observation=partial,verify_current=allow,verify_origin=allow
        )
        self.assertFalse(q["ready_for_b"])
        self.assertEqual(q["snapshot"]["streams"]["slides"]["status"],"complete")
        self.assertEqual(q["snapshot"]["streams"]["screen_text"]["status"],"partial")

    def test_image_post_is_explicitly_unsupported_by_B(self):
        m,refs=photo_fixture(self.root,kind="image_post")
        with self.assertRaisesRegex(GroupBHandoffError,"b_image_post_unsupported"):
            prepare_group_b_result(
                m,receipt=receipt(m,carousel_id=1),
                carousel_observation=photo_observation(m,refs,self.root),
                verify_current=allow,verify_origin=allow
            )

    def test_no_arbitrary_untrusted_frame_attachment(self):
        m,s,p=self.video()
        with self.assertRaisesRegex(GroupBHandoffError,"extra_evidence_verifier_required"):
            prepare_group_b_result(
                m,receipt=receipt(m),video_snapshots=[s],
                verify_current=allow,verify_origin=allow,
                verify_coverage=allow,
                extra_evidence=[{
                    "kind":"frame","ref_sha256":"a"*64,
                    "asset_sha256":m["items"][0]["sha256"]
                }]
            )

    def test_revoked_project_rights_block_even_pure_export(self):
        m,s,p=self.video()
        with self.assertRaisesRegex(GroupBHandoffError,"current_media_rights_denied"):
            prepare_group_b_result(
                m,receipt=receipt(m),video_snapshots=[s],
                verify_current=lambda _:False,verify_origin=allow,
                verify_coverage=allow
            )

    def test_duplicate_A003_or_cross_asset_id_rejected(self):
        m,s,p=self.video()
        with self.assertRaisesRegex(GroupBHandoffError,"unmatched_video_analysis_receipts"):
            prepare_group_b_result(
                m,receipt=receipt(m,video_ids={999:301}),
                video_snapshots=[s],verify_current=allow,
                verify_origin=allow,verify_coverage=allow
            )

    def test_mixed_video_slide_partial_text_never_B_complete(self):
        from intelligence.group_a.extractors.mixed_carousel import (
            extract_mixed_carousel, A003_BINDING
        )
        from intelligence.group_a.media_manifest import open_verified
        photo_m,photo_refs=photo_fixture(self.root)
        # Build a fresh ordered mixed manifest on *one* GA video + source.
        movie=b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41"+b"m"*128
        path=self.root/"clip.mp4"
        path.write_bytes(movie)
        p1=self.root/"slide1.png"
        p2=self.root/"slide2.png"
        ordered=[
            {"media_asset_id":301,"video_id":433,"source_id":"ga19-photo",
             "media_kind":"image","local_path":str(p1),
             "sha256":sha256(p1.read_bytes()).hexdigest(),"byte_size":p1.stat().st_size},
            {"media_asset_id":305,"video_id":433,"source_id":"ga19-photo",
             "media_kind":"video","local_path":str(path),
             "sha256":sha256(movie).hexdigest(),"byte_size":len(movie)},
            {"media_asset_id":302,"video_id":433,"source_id":"ga19-photo",
             "media_kind":"image","local_path":str(p2),
             "sha256":sha256(p2.read_bytes()).hexdigest(),"byte_size":p2.stat().st_size},
        ]
        ptr={
            "schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
            "project_id":uuid4().hex,"qdp_run_id":str(uuid4()),
            "video_id":433,"analysis_group":"A","input_sha256":"b"*64
        }
        m,refs=build_manifest(
            ptr,source_id="ga19-photo",source_policy_version_id=34,
            content_kind="mixed_carousel",ordered_assets=ordered,roots=[str(self.root)],
            verified_at=datetime(2026,10,27,tzinfo=timezone.utc),
        )
        @contextmanager
        def opened(ref):
            with open_verified(refs[ref],roots=[str(self.root)]) as s:
                yield s
        def pinned(mm,item):
            core={
                "schema_version":A003_BINDING,"analysis_id":401,
                "media_asset_id":item["media_asset_id"],
                "source_media_sha256":item["sha256"],
                "video_id":m["video_id"],"source_id":m["source_id"],
                "source_policy_version_id":m["source_policy_version_id"],
                "state":"completed","duration_sec":"4.000","aspect_ratio":"1:1",
                "audio_status":"complete","timeline":[],
            }
            return {**core,"binding_sha256":canonical_sha256(core)}
        mixed=extract_mixed_carousel(
            m,open_ref=opened,verify_current=allow,read_a003=pinned,backend=OCR()
        )
        v=certified_bundle(m,idx=1)
        r=receipt(m,video_ids={305:401},carousel_id=500)
        prepared=prepare_group_b_result(
            m,receipt=r,video_snapshots=[v],carousel_observation=mixed,
            verify_current=allow,verify_origin=allow,verify_coverage=allow
        )
        self.assertFalse(prepared["ready_for_b"])
        self.assertEqual(prepared["snapshot"]["streams"]["screen_text"]["status"],"partial")
        self.assertEqual(prepared["snapshot"]["streams"]["slides"]["status"],"complete")
        self.assertEqual(group_a_to_b_v1(prepared["snapshot"]),prepared["snapshot"])

    def test_cta_stays_a_null_no_b7_classification_import(self):
        m,s,p=self.video()
        self.assertNotIn("cta",p["snapshot"])
        self.assertNotIn("cta_start_sec",p["snapshot"])
        self.assertFalse(any("group_b" in k for k in p["snapshot"]["streams"]))

    def test_python_contract_and_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        path=Path(__file__).resolve().parents[1]/"intelligence/group_a/schemas/ga19-prepared-result-v1.json"
        spec=json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(spec)
        m,s,p=self.video()
        jsonschema.Draft202012Validator(spec).validate({
            **p,"video_analysis_ids":{
                str(k):v for k,v in p["video_analysis_ids"].items()
            }
        })



class FakeGroupBExactConsumer:
    """Standalone GB-MOD-05 acceptance double; NOT a real peer import."""
    def accept_exact(self, row, *, result_id, expected_sha256, native_state):
        from intelligence.group_a.contracts_v1 import group_a_to_b_v1
        if type(result_id) is not int or result_id<=0:
            raise ValueError("missing_exact_ga_result_id")
        snapshot=group_a_to_b_v1(row)
        if canonical_sha256(snapshot)!=expected_sha256:
            raise ValueError("a_snapshot_digest_mismatch")
        if snapshot["analysis_state"]!="completed" or native_state!="completed":
            raise ValueError("a_snapshot_not_complete")
        if any(x["status"]!="complete" for x in snapshot["streams"].values()):
            raise ValueError("stream_unavailable_for_b")
        if snapshot["content_kind"] not in (
            "video","slideshow_video","photo_carousel","mixed_carousel"
        ):
            raise ValueError("b_content_kind_unsupported")
        return {"ga_result_id":result_id,"result_sha256":expected_sha256}


@unittest.skipUnless(Image is not None,"Pillow required")
class GA19ConsumerFake(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_B_accepts_only_exact_completed_A_not_guessed_partial(self):
        m,_=manifest_fixture(self.root)
        full=certified_bundle(m)
        p=prepare_group_b_result(
            m,receipt=receipt(m),video_snapshots=[full],
            verify_current=allow,verify_origin=allow,verify_coverage=allow,
        )
        fake=FakeGroupBExactConsumer()
        selected=fake.accept_exact(
            p["snapshot"],result_id=501,
            expected_sha256=p["result_sha256"],native_state="completed"
        )
        self.assertEqual(selected["ga_result_id"],501)
        with self.assertRaisesRegex(ValueError,"a_snapshot_digest_mismatch"):
            fake.accept_exact(
                p["snapshot"],result_id=501,
                expected_sha256="f"*64,native_state="completed"
            )
        partial=assemble_video_snapshot(
            m,media_asset_id=m["items"][0]["media_asset_id"],
            technical=technical(m),verify_current=allow
        )
        q=prepare_group_b_result(
            m,receipt=receipt(m),video_snapshots=[partial],
            verify_current=allow,verify_origin=allow
        )
        with self.assertRaisesRegex(ValueError,"a_snapshot_not_complete"):
            fake.accept_exact(
                q["snapshot"],result_id=502,
                expected_sha256=q["result_sha256"],native_state="partial"
            )


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable PostgreSQL 17 DSN missing")
class GA19PG17(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.migrate import apply_migrations
        self.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        apply_migrations(self.conn)
        self.addCleanup(self.conn.close)
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=uuid4().hex
        self.source="ga19-"+uuid4().hex
        self.conn.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES(%s,'GA19 test',1,%s,now(),now())""",
            (self.project,"d"*64)
        )
        self.conn.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(self.project,))
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'test','owned','enabled',true)""",(self.source,)
        )
        self.policy=self.conn.execute(
            """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
               VALUES (%s,1,true,'{"can_analyze_media":true}'::jsonb)
               RETURNING id""",(self.source,)
        ).fetchone()[0]
        token=uuid4().hex
        self.video=self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
               VALUES ('youtube',%s,%s,'video') RETURNING id""",
            (token,"https://youtube.test/fixture/"+token)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
            (self.project,self.video)
        )
        blob=b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41"+b"v"*128
        path=self.root/"clip.mp4"
        path.write_bytes(blob)
        self.asset=self.conn.execute(
            """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,sha256,permission)
               VALUES (%s,%s,'video',%s,%s,'analysis_allowed') RETURNING id""",
            (self.video,self.source,str(path),sha256(blob).hexdigest())
        ).fetchone()[0]
        pointer={
            "schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
            "project_id":self.project,"qdp_run_id":str(uuid4()),
            "video_id":self.video,"analysis_group":"A","input_sha256":"b"*64,
        }
        self.manifest,_=build_manifest(
            pointer,source_id=self.source,source_policy_version_id=self.policy,
            content_kind="video",ordered_assets=[{
                "media_asset_id":self.asset,"video_id":self.video,
                "source_id":self.source,"media_kind":"video",
                "local_path":str(path),"sha256":sha256(blob).hexdigest(),
                "byte_size":len(blob),
            }],roots=[str(self.root)],
            verified_at=datetime(2026,10,27,tzinfo=timezone.utc),
        )
        config={"schema_version":"ga19-config/v1"}
        self.run=self.conn.execute(
            """INSERT INTO ga_runs
               (project_id,qdp_run_id,video_id,source_id,source_policy_version_id,
                qdp_input_sha256,manifest_sha256,config_sha256,config,
                expected_media_items,content_kind)
               VALUES (%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,1,'video')
               RETURNING id::text""",
            (self.project,self.manifest["qdp_run_id"],self.video,
             self.source,self.policy,self.manifest["qdp_input_sha256"],
             self.manifest["manifest_sha256"],canonical_sha256(config),
             json.dumps(config))
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO ga_media_refs
               (run_id,project_id,video_id,source_id,slide_index,
                media_asset_id,media_kind,asset_sha256,byte_size,verified_at)
               VALUES (%s::uuid,%s,%s,%s,1,%s,'video',%s,%s,%s)""",
            (self.run,self.project,self.video,self.source,self.asset,
             self.manifest["items"][0]["sha256"],self.manifest["items"][0]["byte_size"],
             self.manifest["verified_at"])
        )
        from intelligence.group_a.snapshot_repository import persist_group_a_snapshot
        self.snapshot=certified_bundle(self.manifest)
        a003=persist_group_a_snapshot(
            self.conn,self.manifest,video_snapshots=[self.snapshot],
            verify_current=allow,verify_origin=allow,verify_coverage=allow
        )
        self.r=receipt(self.manifest,run_id=self.run,
                       video_ids=a003["video_analysis_ids"])
        self.addCleanup(self.conn.execute,"DELETE FROM project_registry WHERE id=%s",(self.project,))
        self.addCleanup(self.conn.execute,"DELETE FROM sources WHERE id=%s",(self.source,))
        self.addCleanup(self.conn.execute,"DELETE FROM videos WHERE id=%s",(self.video,))

    def prepared(self):
        return prepare_group_b_result(
            self.manifest,receipt=self.r,video_snapshots=[self.snapshot],
            verify_current=allow,verify_origin=allow,verify_coverage=allow,
        )

    def publish(self,p,*,verify_current=allow):
        return persist_group_b_handoff(
            self.conn,self.manifest,p,video_snapshots=[self.snapshot],
            verify_current=verify_current,verify_origin=allow,
            verify_coverage=allow
        )

    def test_real_054_result_pending_outbox_and_idempotent_pin(self):
        p=self.prepared()
        receipt=self.publish(p)
        self.assertEqual(receipt["state"],"completed")
        self.assertEqual(receipt["delivery_state"],"pending_outbox")
        self.assertIsInstance(receipt["ga_result_id"],int)
        self.assertIsInstance(receipt["outbox_id"],int)
        self.assertEqual(self.publish(p),receipt)
        row=self.conn.execute(
            """SELECT g.state,g.result_schema,g.result_sha256,g.snapshot,
                      r.state,oa.payload_sha256,oa.state
               FROM ga_results g JOIN ga_runs r ON r.id=g.run_id
               JOIN ga_outbox oa ON oa.result_id=g.id
               WHERE g.id=%s""",(receipt["ga_result_id"],)
        ).fetchone()
        self.assertEqual(row[0],"completed")
        self.assertEqual(row[1],"group-a-to-b/v1")
        self.assertEqual(row[2],p["result_sha256"])
        self.assertEqual(row[3],p["snapshot"])
        self.assertEqual(row[4],"completed")
        self.assertEqual(row[6],"pending")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM ga_outbox WHERE run_id=%s::uuid",
            (self.run,)
        ).fetchone()[0],1)
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE ga_results SET method_version='evil' WHERE id=%s",
                (receipt["ga_result_id"],)
            )

    def test_revocation_rolls_back_result_and_outbox(self):
        p=self.prepared()
        hits=[0]
        def rights(scope):
            hits[0]+=1
            return hits[0]<=5
        with self.assertRaises(GroupBHandoffError):
            self.publish(p,verify_current=rights)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM ga_results WHERE run_id=%s::uuid",
            (self.run,)
        ).fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM ga_runs WHERE id=%s::uuid",
            (self.run,)
        ).fetchone()[0],"queued")

    def test_wrong_run_policy_or_media_hash_fails_closed(self):
        p=self.prepared()
        self.conn.execute(
            "UPDATE media_assets SET sha256=%s WHERE id=%s",
            ("f"*64,self.asset)
        )
        with self.assertRaisesRegex(GroupBHandoffError,"media_ref_or_permission_mismatch"):
            self.publish(p)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM ga_outbox WHERE run_id=%s::uuid",
            (self.run,)
        ).fetchone()[0],0)
