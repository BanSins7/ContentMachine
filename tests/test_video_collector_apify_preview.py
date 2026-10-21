import unittest
from intelligence.collector.apify_preview import inspect_tiktok_item,inspect_items

BASE={"id":"7234567890123456789",
      "webVideoUrl":"https://www.tiktok.com/@test/video/7234567890123456789",
      "text":"A morning facial routine",
      "createTimeISO":"2026-09-13T09:15:00.000Z",
      "playCount":5000,"diggCount":123,"videoMeta":{"downloadAddr":"https://example.com/video.mp4"}}

class ApifyPreviewTests(unittest.TestCase):
 def test_inspection_never_exposes_media_or_metrics(self):
  result=inspect_tiktok_item(BASE)
  self.assertIn(result["state"],("valid","partial"))
  self.assertEqual(result["canonical_url"],BASE["webVideoUrl"])
  self.assertFalse(result["metrics_imported"])
  self.assertFalse(result["media_imported"])
  self.assertNotIn("playCount",result)
  self.assertNotIn("videoMeta",result)
  self.assertNotIn("downloadAddr",repr(result))
 def test_reject_ssrf_bad_ids_errors_and_slides(self):
  cases=[
   dict(BASE,webVideoUrl="https://www.tiktok.com.evil.com/@test/video/7234567890123456789"),
   dict(BASE,id="1234567890123456789"),
   dict(BASE,error="Not found"),
   dict(BASE,isSlideshow=True),
   dict(BASE,webVideoUrl=None),
   dict(BASE,text=3),
   dict(BASE,createTimeISO=42)
  ]
  for case in cases:
   with self.subTest(case=case),self.assertEqual(inspect_tiktok_item(case)["state"],"rejected")
 def test_duplicate_batch_and_limits(self):
  q=inspect_items([BASE,BASE],max_items=2)
  self.assertEqual(q["accepted"],1)
  self.assertEqual(q["duplicate_rows"],[1])
  self.assertEqual(q["persisted"],0)
  self.assertEqual(q["paid_calls"],0)
  with self.assertRaises(ValueError):inspect_items([BASE,BASE],max_items=1)
 def test_partial_without_optional_fields(self):
  data={"webVideoUrl":BASE["webVideoUrl"],"id":BASE["id"]}
  r=inspect_tiktok_item(data)
  self.assertEqual(r["state"],"partial")
  self.assertIsNone(r["title"])

if __name__=="__main__":unittest.main()
