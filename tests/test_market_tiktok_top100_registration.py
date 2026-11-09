"""Offline proof: 100 real-view ranked selections are registered atomically
for local research, including carousel source types, without forged licenses,
cross-post grants or provider charges. No external networking.
"""
from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.tiktok_top100 import rank_profile_posts
from intelligence.market_intelligence.tiktok_top100_register import register_selection
from intelligence.market_intelligence.extended_c import enrich_exact_c


URL="https://www.tiktok.com/@faceyogilab"
HANDLE="faceyogilab"
PROJECT="a"*32


def sample(root):
    records=[{
        "url":f"{URL}/{'photo' if i%4==0 else 'video'}/{7600000000000000000+i}",
        "play_count":i*1000+3,
        "type":"photo" if i%4==0 else "video"
    } for i in range(100)]
    selection=rank_profile_posts(HANDLE,records)
    selection["profile_snapshot_id"]="sd_a1b2c3d4e5f6g7h8i9j0"
    selection["profile_dataset_id"]="gd_lj71gn6l68bz7y9hc"
    root.mkdir(parents=True,exist_ok=True)
    path=root/f"tiktok-top100-{HANDLE}-selection.json"
    path.write_text(json.dumps(selection),encoding="utf-8")
    state={"schema_version":"marketing-tiktok-profile-snapshot-job/v1",
           "username":HANDLE,"snapshot_id":selection["profile_snapshot_id"],
           "state":"completed","selection_sha256":sha256(path.read_bytes()).hexdigest()}
    (root/f"tiktok-top100-{HANDLE}-state.json").write_text(json.dumps(state),
                                                          encoding="utf-8")
    return selection


class R:
    def __init__(self,value=None):self.value=value
    def fetchone(self):return self.value


class Conn:
    def __init__(self,existing=False):
        self.existing=existing
        self.rights=[]
        self.video_ids=[]
        self.existing_sql=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):return nullcontext()
    def cursor(self,**kw):
        db=self
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def execute(self,sql,args):
                db.existing_sql.append(sql)
                self.args=args
                return self
            def fetchone(self):
                if not db.existing:return None
                return {"id":"11111111-1111-4111-8111-111111111111",
                        "active_version_id":"22222222-2222-4222-8222-222222222222",
                        "canonical_url":f"{URL}/{'photo' if self.args[1][-2:] else 'video'}/{self.args[1]}",
                        "source_type":"user_video_reference","link_kind":"video"}
        return Cursor()
    def execute(self,sql,args):
        if "INSERT INTO creators(" in sql:
            return R((19,))
        if "UPDATE project_sources" in sql:
            self.rights.append((sql,args))
            return R()
        if "INSERT INTO videos(" in sql:
            self.video_ids.append(args[0])
            return R((len(self.video_ids),))
        raise AssertionError("Unexpected SQL "+sql[:100])


class RegisterPublicHundred(unittest.TestCase):
    def test_register_exact100_as_one_public_research_authorization(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            selection=sample(root)
            conn=Conn()
            def inserted(conn_obj,project_id,*,source_type,url):
                return ({
                    "id":"11111111-1111-4111-8111-111111111111",
                    "active_version_id":"22222222-2222-4222-8222-222222222222",
                    "canonical_url":url,"link_kind":"video",
                    "source_type":"user_video_reference"},None)
            with patch("intelligence.market_intelligence.tiktok_top100_register._project"),patch(
                 "intelligence.market_intelligence.tiktok_top100_register.d1.connect",
                 return_value=conn),patch(
                 "intelligence.market_intelligence.tiktok_top100_register._insert",
                 side_effect=inserted),patch(
                 "intelligence.market_intelligence.tiktok_top100_register.transfer_allowed",
                 return_value=True):
                result=register_selection(PROJECT,HANDLE,root=root)
            self.assertEqual(result["registered_exact_public_posts"],100)
            self.assertEqual(len(set(result["video_ids"])),100)
            self.assertEqual(result["photo_carousels"],25)
            self.assertEqual(result["regular_videos"],75)
            self.assertEqual(len(conn.rights),100)
            self.assertEqual(conn.video_ids[0],selection["posts"][0]["native_id"])
            for sql,args in conn.rights:
                self.assertIn("can_fetch=TRUE,can_store=TRUE,can_analyze=TRUE",sql)
                self.assertTrue(args[0])
                policy=json.loads(args[1])
                self.assertFalse(policy["license_from_creator_asserted"])
                self.assertFalse(policy["private_or_restricted_access_permitted"])
                self.assertEqual(policy["selection_sha256"],
                                 sha256((root/f"tiktok-top100-{HANDLE}-selection.json").read_bytes()).hexdigest())
            self.assertEqual(result["billable_requests"],0)

    def test_brightdata_assumed_clock_exposes_c4_diagnostic_not_c0_or_c7(self):
        rows=[{"video_id":7,"platform":"tiktok","content_kind":"photo_carousel",
               "metric_source_id":"mi.brightdata","metric_definition":"platform_post_views",
               "metric_version":"brightdata_on_demand_v1","published_at":None}]
        newest={"id":23,"source_id":"mi.brightdata",
                "source_metric_definition":"platform_post_views",
                "source_metric_version":"brightdata_on_demand_v1",
                "observed_at":"2026-11-01T09:00:00+00:00",
                "data_quality_status":"ok","precision":"exact",
                "observation_time_assumed":True,
                "views":424000,"likes":25400,"comments":53,"shares":1359}
        output=enrich_exact_c(rows,{},raw_histories={7:[newest]})
        receipt=output[0]["group_c"]
        self.assertIsNone(receipt["c4"])
        self.assertEqual(receipt["c0"],"unavailable")
        self.assertEqual(receipt["c4_public_counter_diagnostic"][
                         "engagement_rate_fraction"],"0.063235849057")
        self.assertFalse(receipt["c4_public_counter_diagnostic"]["eligible_for_c0_or_c7"])
        self.assertIsNone(receipt["c7_breakout_preview"])


if __name__=="__main__":
    unittest.main()
