"""Read-only, no-redownload verification of real carousel QA report path.

Fixtures are local JPEGs; this checks the REAL file hashing/PNG conversion
and free-only workflow without making any platform or OpenRouter calls.
"""
from __future__ import annotations
import contextlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from intelligence.market_intelligence.carousel_media import scan_images
from scripts import probe_market_carousel_one as probe

POST="https://www.tiktok.com/@luke.swan89/photo/7689988051296210209"
NATIVE="7689988051296210209"


class RealCarouselReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.media=self.root/"carousel-media"
        self.reports=self.root/"reports"
        self.folder=self.media/("tiktok-"+NATIVE)/("carousel-tiktok-"+NATIVE)
        self.folder.mkdir(parents=True)
        self.reports.mkdir()
        for index in range(1,9):
            im=Image.new("RGB",(96,128),(index*25,index*20,index*15))
            im.save(self.folder/(f"{index:03}.jpg"),format="JPEG")
        self.manifest=scan_images(self.folder,expected_url=POST)

    def args(self,sha,name="after.json"):
        return ["--url",POST,"--reuse-downloaded",
                "--expected-manifest-sha256",sha,
                "--output",str(self.reports/name)]

    def test_reuse_exact_eight_source_files_without_download_or_model(self):
        capture=StringIO()
        with patch.object(probe,"QA_PHOTOS",self.media),patch.object(
             probe,"QA_REPORTS",self.reports),patch.object(
             probe,"download_carousel",
             side_effect=AssertionError("a second download is forbidden")),patch(
             "intelligence.market_intelligence.carousel_media._ocr_backend",
             return_value=None),contextlib.redirect_stdout(capture):
            rc=probe.main(self.args(self.manifest["manifest_sha256"]))
        self.assertEqual(rc,0)
        printed=json.loads(capture.getvalue())
        self.assertEqual(printed["actual_slides"],8)
        self.assertEqual(printed["ocr_words_total"],0)
        self.assertEqual(len(printed["ocr_slides"]),8)
        self.assertTrue(all(s["reason"]=="ocr_backend_not_configured"
                            for s in printed["ocr_slides"]))
        report=json.loads((self.reports/"after.json").read_text("utf-8"))
        self.assertTrue(report["source_reused_without_download"])
        self.assertEqual(report["source_manifest_sha256"],
                         self.manifest["manifest_sha256"])
        self.assertEqual(report["paid_brightdata_triggers"],0)
        self.assertEqual(report["paid_visual_model_calls"],0)

    def test_refuses_changed_sha_and_does_not_write_misleading_report(self):
        with patch.object(probe,"QA_PHOTOS",self.media),patch.object(
             probe,"QA_REPORTS",self.reports),patch.object(
             probe,"download_carousel",
             side_effect=AssertionError("must not download")):
            with self.assertRaises(SystemExit) as caught:
                probe.main(self.args("a"*64))
        self.assertEqual(caught.exception.code,2)
        self.assertFalse((self.reports/"after.json").exists())


if __name__=="__main__":
    unittest.main()
