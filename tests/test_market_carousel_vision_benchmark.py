"""Offline reproducibility tests for 7 fixed carousels × 4 vision models."""
from __future__ import annotations

from collections import Counter,defaultdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from intelligence.market_intelligence.carousel_media import _image
from intelligence.market_intelligence.contracts import digest
from scripts import benchmark_market_carousel_vision as b


def fixture(tmp):
    root=Path(tmp)/"media"
    root.mkdir()
    output=Path(tmp)/"reports"
    selected=[]
    vids=[]
    rows={}
    names=[n for n,_ in b.FIXED]+[str(7800000000000000000+i) for i in range(93)]
    for i,native in enumerate(names):
        url=f"https://www.tiktok.com/{b.USERNAME}/photo/{native}"
        post={"native_id":native,"url":url,"views":(100-i)*1000,
              "content_kind":"photo_carousel"}
        selected.append(post)
        vids.append({**post,"video_id":i+1,"state":"analysis_partial"})
        if i>=7:continue
        folder=root/"mi-carousels"/b.PROJECT/("carousel-tiktok-"+native)
        folder.mkdir(parents=True)
        manifest_slides=[]
        for num in range(1,3):
            im=Image.new("RGB",(180,320),
                         (30+i*3,40+num*4,70))
            f=folder/(f"{num:03}.png")
            im.save(f)
            png,w,h=_image(f)
            binary=f.read_bytes()
            sha=sha256(binary).hexdigest()
            pngsha=sha256(png).hexdigest()
            manifest_slides.append({
                "slide_index":num,"file_name":f.name,
                "source_image_sha256":sha,"byte_size":len(binary),
                "image_width":w,"image_height":h,
                "image_png_sha256":pngsha,
                "ref":"s"+str(num)+"-"+pngsha,
            })
        manifest={
            "schema_version":"mi-photo-carousel-media/v1",
            "content_kind":"photo_carousel",
            "platform":"tiktok","post_native_id":native,
            "canonical_post_url":url,
            "post_url_sha256":sha256(url.encode()).hexdigest(),
            "complete_ordered_slides":True,
            "slides":manifest_slides,
            "video_duration_sec":None,"video_audio":None,
            "retention":None,"slide_view_durations":None,
        }
        manifest["manifest_sha256"]=digest(manifest)
        rows[native]=(manifest,str(folder))
    active={
        "project_id":b.PROJECT,"batch_id":2,"username":"luke.swan89",
        "selection_sha256":"f"*64,"videos":vids}
    selection={"profile_snapshot_id":"sd_mvxfrkpt61ds6ual","posts":selected}
    def lookup(post,video):
        return rows[post["native_id"]]
    return root,output,active,selection,lookup


def fake_reply(request_bytes):
    request=json.loads(request_bytes)
    assert request["messages"][1]["content"][1]["type"]=="image_url"
    assert request["response_format"]=={"type":"json_object"}
    result={
        "scene_description":"Человек в кадре, цветной фон",
        "people":[{"description":"person","pose":"front",
                   "expression":"neutral","position":"center"}],
        "background":"white","camera_composition":"central close up",
        "visual_style":"photo","text_blocks":[{"verbatim":"Example",
            "position":"top_center","legibility":"clear"}],
        "text_unreadable":False,
        "hook":{"present":True,"kind":"curiosity","verbatim_text":"Example",
                "visual_mechanism":"face","explanation":"question"},
        "cta":{"present":False,"kind":None,"verbatim_text":None,
               "target_app":None,"explanation":None},
        "product":{"visible":False,"name_exact":None,"appearance":None,
                   "screen_or_mockup":False},
        "slide_role_guess":"hook",
        "recreate":{"preserve_visual_structure":["composition"],
                    "replaceable_variables":["avatar","product"]},
        "uncertainties":[],
    }
    return {"id":"test_no_api_call","choices":[{
        "message":{"content":json.dumps(result,ensure_ascii=False)},
        "finish_reason":"stop"}],
        "usage":{"prompt_tokens":120,"completion_tokens":340,"cost":0.001}}


class FixedBenchmark(unittest.TestCase):
    def test_exact_fixed_ids_four_real_model_slugs(self):
        self.assertEqual(len(b.FIXED),7)
        self.assertEqual(len({x[0] for x in b.FIXED}),7)
        self.assertEqual(len(b.MODELS),4)
        self.assertEqual(b.MODELS[0][1],"google/gemini-3.1-flash-lite")
        self.assertEqual(b.MODELS[1][1],"google/gemini-3-flash-preview")
        self.assertEqual(b.MODELS[2][1],"deepseek/deepseek-v4.1-flash")
        self.assertEqual(b.MODELS[3][1],"qwen/qwen3-vl-30b-a3b-thinking")

    def test_preparation_is_sha_verified_and_creates_readable_gallery(self):
        with TemporaryDirectory() as tmp:
            root,out,active,selection,lookup=fixture(tmp)
            path,manifest=b.prepare(output_root=out,root=root,
                active=active,selection=selection,db_lookup=lookup)
            self.assertEqual(manifest["carousel_count"],7)
            self.assertEqual(manifest["slide_count"],14)
            self.assertEqual(manifest["planned_api_requests"],56)
            self.assertEqual(len(manifest["carousels"]),7)
            self.assertTrue((path/"source_paths.txt").exists())
            self.assertTrue((path/"references.html").exists())
            txt=(path/"source_paths.txt").read_text("utf-8")
            self.assertEqual(txt.count("Slide 1:"),7)
            self.assertEqual(txt.count("Slide 2:"),7)
            for c in manifest["carousels"]:
                for s in c["slides"]:
                    self.assertTrue(Path(s["original_path"]).exists())
                    self.assertEqual(sha256((path/s["input_jpeg_relative"]).read_bytes()).hexdigest(),
                                     s["input_jpeg_sha256"])
            again=b.prepare(output_root=out,root=root,
                active=active,selection=selection,db_lookup=lookup)
            self.assertEqual(again[1],manifest)
            # Changed original image is rejected, not silently re-used.
            f=Path(manifest["carousels"][0]["slides"][0]["original_path"])
            Image.new("RGB",(180,320),(2,2,2)).save(f)
            with self.assertRaises(ValueError):
                b.prepare(output_root=out,root=root,
                          active=active,selection=selection,db_lookup=lookup)

    def test_all_four_models_get_identical_pixels_and_resume_without_pay(self):
        with TemporaryDirectory() as tmp:
            root,out,active,selection,lookup=fixture(tmp)
            path,manifest=b.prepare(output_root=out,root=root,
                active=active,selection=selection,db_lookup=lookup)
            lock=threading.Lock()
            seen=[]
            def requester(raw):
                request=json.loads(raw)
                data=request["messages"][1]["content"][1]["image_url"]["url"]
                with lock:
                    seen.append((request["model"],sha256(data.encode()).hexdigest()))
                return fake_reply(raw)
            with patch.object(b,"prepare",return_value=(path,manifest)),patch.object(
                 b,"get_market_openrouter_key",side_effect=AssertionError("must not read keys")):
                outcome=b.run(output_root=path,max_usd=10,workers=2,client=requester)
                self.assertEqual(len(seen),56)
                self.assertEqual(len({x[0] for x in seen}),4)
                hashed=Counter(x[1] for x in seen)
                self.assertTrue(all(num==4 for num in hashed.values()))
                self.assertEqual(len(hashed),14)
                self.assertEqual(outcome["known_cost_usd"],0.056)
                self.assertEqual(len(outcome["models"]),4)
                for row in outcome["models"]:
                    self.assertEqual(row["completed_slides"],14)
                    record=json.loads(Path(row["output_path"]).read_text("utf-8"))
                    self.assertEqual(len(record["carousels"]),7)
                    self.assertEqual(len(record["slides"]),14)
                    self.assertEqual(record["carousels"][0]["slides"][0]["result"]["state"],
                                     "completed")
                    self.assertEqual(record["status"],"completed")
                second=b.run(output_root=path,max_usd=10,workers=2,client=requester)
                self.assertEqual(len(seen),56)
                self.assertEqual(second["model_runs_started"],0)

    def test_failed_model_json_never_retries_implicitly(self):
        with TemporaryDirectory() as tmp:
            root,out,active,selection,lookup=fixture(tmp)
            path,manifest=b.prepare(output_root=out,root=root,
                active=active,selection=selection,db_lookup=lookup)
            seen=[]
            def requester(raw):
                seen.append(1)
                return {"choices":[{"message":{"content":"not json"}}]}
            with patch.object(b,"prepare",return_value=(path,manifest)):
                first=b.run(max_usd=10,workers=3,client=requester)
                self.assertEqual(len(seen),56)
                self.assertTrue(all(x["failed_slides"]==14 for x in first["models"]))
                again=b.run(max_usd=10,workers=3,client=requester)
                self.assertEqual(len(seen),56)
                self.assertEqual(again["model_runs_started"],0)
                third=b.run(max_usd=10,workers=3,client=requester,retry_failed=True)
                self.assertEqual(len(seen),112)
                self.assertEqual(third["model_runs_started"],56)


if __name__=="__main__":
    unittest.main()
