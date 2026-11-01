"""Offline tests for top100 by popularity, mixed photo/video, consent and SHA.

Never contact TikTok, OpenRouter or Bright Data.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.tiktok_top100 import (
    canonical_tiktok_profile,rank_profile_posts)
from intelligence.market_intelligence import tiktok_top100_register as reg
from scripts import prepare_market_tiktok_top100 as cli

ACCOUNT="@faceyogilab"
PROFILE="https://www.tiktok.com/@faceyogilab"
SID="sd_mvxdevhz202ah32q5t"


def posts(count=150,photos=True):
    return [{
        "url":f"https://www.tiktok.com/@faceyogilab/{'photo' if photos and i%3==0 else 'video'}/{7600000000000000000+i}",
        "author_username":"faceyogilab","play_count":1000+i*31,
        "digg_count":i*2,"comment_count":i,
        "type":"photo" if photos and i%3==0 else "video",
    } for i in range(count)]


class RankTop100(unittest.TestCase):
    def test_public_username_only(self):
        self.assertEqual(canonical_tiktok_profile(" @FaceYogaLab "),("faceyogilab",PROFILE))
        self.assertEqual(canonical_tiktok_profile(PROFILE),("faceyogilab",PROFILE))
        for bad in ("","../bad","@private/123","https://evil.com/@x","name?x=1"):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                canonical_tiktok_profile(bad)

    def test_true_views_descending_top100_mixed_photo_and_video(self):
        result=rank_profile_posts(ACCOUNT,posts(150))
        self.assertTrue(result["ready"])
        self.assertEqual(result["selected"],100)
        self.assertEqual(result["unique_ranked_posts"],150)
        self.assertEqual(result["posts"][0]["views"],1000+149*31)
        self.assertEqual(result["posts"][-1]["views"],1000+50*31)
        self.assertEqual([v["views"] for v in result["posts"]],
                         sorted([v["views"] for v in result["posts"]],reverse=True))
        self.assertIn("photo_carousel",{x["content_kind"] for x in result["posts"]})
        self.assertIn("video",{x["content_kind"] for x in result["posts"]})
        self.assertFalse(result["incomplete_profile_history_possible"] is False)

    def test_missing_views_and_wrong_authors_cannot_fill_top100(self):
        values=posts(98)
        values.extend([
            {"url":"https://www.tiktok.com/@other/video/7600000000000000998",
             "play_count":999999999},
            {"url":"https://www.tiktok.com/@faceyogilab/photo/7600000000000000999"},
        ])
        result=rank_profile_posts(ACCOUNT,values)
        self.assertEqual(result["selected"],98)
        self.assertFalse(result["ready"])
        self.assertEqual(result["rejected"]["different_author"],1)
        self.assertEqual(result["rejected"]["no_views"],1)

    def test_provider_photo_signal_corrects_video_url_variant(self):
        record={"url":"https://www.tiktok.com/@faceyogilab/video/7600000000000000999",
                "is_photo":True,"play_count":"402000"}
        result=rank_profile_posts(ACCOUNT,[record],limit=1)
        self.assertEqual(result["posts"][0]["content_kind"],"photo_carousel")
        self.assertIn("/photo/",result["posts"][0]["url"])

    def test_conflicting_duplicate_never_wins_ranking(self):
        items=posts(101)
        copied=dict(items[-1]);copied["play_count"]=100000000
        result=rank_profile_posts(ACCOUNT,items+[copied])
        self.assertEqual(result["selected"],100)
        self.assertNotIn(items[-1]["url"],[x["url"] for x in result["posts"]])
        self.assertEqual(result["rejected"]["duplicate"],1)


class Top100OneTrigger(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=cli._path(ACCOUNT)
        self.report=self.root/"tiktok-top100-faceyogilab-selection.json"
        patcher=patch.object(cli,"REPORTS",self.root)
        patcher.start();self.addCleanup(patcher.stop)

    def test_one_paid_trigger_writes_durable_preflight_marker(self):
        calls=[]
        def submit(method,path,token,*,query,payload):
            self.assertTrue(self.path.exists())
            self.assertEqual(cli._read(self.path)["state"],
                             "trigger_uncertain_no_auto_retry")
            self.assertEqual(payload,[{"url":PROFILE,"country":""}])
            self.assertEqual(query["dataset_id"],cli.PROFILE_DATASET)
            calls.append(1)
            return {"snapshot_id":SID}
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            response=cli.start(ACCOUNT,request=submit)
            with self.assertRaises(FileExistsError):
                cli.start(ACCOUNT,request=submit)
        self.assertEqual(calls,[1])
        self.assertEqual(response["snapshot_id"],SID)
        self.assertEqual(cli._read(self.path)["snapshot_id"],SID)

    def test_poll_then_collect_existing_snapshot_zero_new_post(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *args,**kwargs:{"snapshot_id":SID})
        done=cli.poll(ACCOUNT,progress=lambda snapshot:"ready")
        self.assertEqual(done["paid_requests"],0)
        report=cli.collect(ACCOUNT,fetch=lambda snapshot:posts(150))
        self.assertEqual(report["selected"],100)
        self.assertTrue(report["ready"])
        self.assertEqual(report["paid_requests"],0)
        self.assertTrue(self.report.exists())
        with self.assertRaisesRegex(ValueError,"top100_selection_already_saved"):
            cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        selected,hash_=reg.read_selection(ACCOUNT,root=self.root)
        self.assertEqual(selected["selected"],100)
        self.assertEqual(sha256(self.report.read_bytes()).hexdigest(),hash_)

    def test_selection_mutation_is_rejected_before_d1_write(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *a,**kw:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _: "ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        doc=json.loads(self.report.read_text())
        doc["posts"][0]["views"]=0
        self.report.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError,"top100_selection_sha256_mismatch"):
            reg.read_selection(ACCOUNT,root=self.root)

    def test_one_command_never_starts_paid_collection_without_opt_in(self):
        with patch.object(cli,"start",
                          side_effect=AssertionError("no provider should start")):
            with self.assertRaisesRegex(ValueError,"explicit_provider_cost_opt_in_required"):
                cli.run_all(ACCOUNT,"a"*32,allow_paid=False,paid_metrics=True)

    def test_one_command_reuses_completed_snapshot_without_another_paid_post(self):
        with patch.object(cli.bd,"readiness",return_value={"ready":True}),patch.object(
             cli.bd,"_token",return_value="stub"):
            cli.start(ACCOUNT,request=lambda *args,**kw:{"snapshot_id":SID})
        cli.poll(ACCOUNT,progress=lambda _: "ready")
        cli.collect(ACCOUNT,fetch=lambda _:posts(150))
        with patch.object(cli,"start",
                          side_effect=AssertionError("duplicate trigger")) as trigger,patch.object(
             cli,"launch",return_value={"state":"launched"}) as launched:
            report=cli.run_all(ACCOUNT,"a"*32,allow_paid=True,paid_metrics=True)
        trigger.assert_not_called()
        launched.assert_called_once_with("faceyogilab","a"*32,
                                         paid_metrics=True,interval=20)
        self.assertEqual(report["state"],"launched")

    def test_local_media_roots_include_actual_tiktok_mp4_archive(self):
        import os
        home=self.root/"fake_home"
        home.mkdir()
        with patch.object(cli,"ROOT",self.root),patch(
             "scripts.prepare_market_tiktok_top100.Path.home",
             return_value=home),patch(
             "scripts.prepare_market_tiktok_top100.config.setting",
             return_value=""),patch.dict(os.environ,{},clear=False):
            result=cli.configure_local_media_roots()
            roots=os.environ["CM_MARKET_MEDIA_ROOTS"].split(os.pathsep)
            self.assertIn(str((self.root/"marketing-qa"/"media").resolve()),roots)
            self.assertIn(str((home/"Downloads"/"TikTokArchive").resolve()),roots)
        self.assertEqual(result["roots_configured"],2)

    def test_registration_transfer_is_separate_deployment_opt_in(self):
        with patch("intelligence.market_intelligence.tiktok_top100_register.config.setting",
                   side_effect=lambda key,default="": "1" if key=="CM_MARKET_VISION_ENABLED" else ""):
            self.assertFalse(reg.transfer_allowed())
        with patch("intelligence.market_intelligence.tiktok_top100_register.config.setting",
                   return_value="1"):
            self.assertTrue(reg.transfer_allowed())


if __name__=="__main__":
    unittest.main()
