"""Offline: one-call whole-carousel semantics without invented per-slide hooks."""
import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from scripts import analyze_marketing_carousel_story as story
from scripts import benchmark_market_carousel_vision as benchmark


def fixture(folder):
    native=story.DEFAULT_NATIVE_ID
    slides=[]
    prior_slides={}
    for num in (1,2):
        im=Image.new("RGB",(128,224), (num*30,60,70))
        buf=BytesIO()
        im.save(buf,format="JPEG")
        data=buf.getvalue()
        relative=f"references/{native}/{num:02}.jpg"
        output=folder/relative
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(data)
        digest=sha256(data).hexdigest()
        slides.append({"slide_index":num,"input_jpeg_relative":relative,
                       "input_jpeg_sha256":digest})
        prior_slides[f"{native}:{num}"]={
            "state":"completed","input_jpeg_sha256":digest,
            "analysis":{"scene_description":"person looking at phone",
                        "text_blocks":[{"verbatim":"A test caption",
                                        "position":"top_center",
                                        "legibility":"clear"}],
                        "hook":{"present":True,"kind":"BAD EARLIER GUESS"},
                        "cta":{"present":True,"kind":"BAD EARLIER GUESS"},
                        "slide_role_guess":"hook",
                        "visual_style":"snapshot","people":[],
                        "background":"room","camera_composition":"center"}
        }
    source={
        "schema_version":benchmark.SCHEMA,
        "project_id":story.PROJECT if hasattr(story,"PROJECT") else benchmark.PROJECT,
        "batch_id":2,"selection_sha256":"b"*64,
        "prompt_sha256":"c"*64,
        "fixed_native_ids":[n for n,_ in benchmark.FIXED],
        "carousels":[{
            "native_id":native,"video_id":23,
            "url":f"https://www.tiktok.com/@luke.swan89/photo/{native}",
            "manifest_sha256":"d"*64,"slides":slides
        }],
    }
    prior={"model":story.MODEL,"source_selection_sha256":source["selection_sha256"],
           "prompt_sha256":source["prompt_sha256"],"slides":prior_slides}
    (folder/"sources.json").write_text(json.dumps(source),encoding="utf-8")
    (folder/"model-gemini-3-flash.json").write_text(json.dumps(prior),encoding="utf-8")
    return native


def valid_response():
    return {
        "overall_story_summary":"Сначала демонстрируется проблема, затем решение.",
        "story_pattern":"problem -> tip",
        "global_hook":{"present":True,"starts_on_slide":1,
            "supported_by_slides":[1],"exact_visible_text":["A test caption"],
            "visual_mechanism":"...","why_it_creates_interest":"...",
            "how_and_where_payoff_happens":"..."},
        "narrative_phases":[
            {"start_slide":1,"end_slide":1,"phase":"opening_hook",
             "purpose":"...","what_changes_for_viewer":"..."},
            {"start_slide":2,"end_slide":2,"phase":"tips",
             "purpose":"...","what_changes_for_viewer":"..."}],
        "slide_roles":[
            {"slide_index":i,"primary_role":"hook" if i==1 else "tip",
             "secondary_roles":[],"visible_text_support":["A test caption"],
             "visual_evidence":"text at top",
             "narrative_function":"...","connection_to_previous":None,
             "sets_up_next":None,"product_mentioned_or_shown":False,
             "viewer_directed_cta":False,
             "interpretation_confidence":"high"}
            for i in (1,2)],
        "product_integration":{"present":False,"slide_indices":[],
            "app_name_visible":None,"role_in_story":"none",
            "how_naturally_inserted":None,"what_the_images_actually_show":None,
            "marketing_claims_are_unverified":True},
        "global_cta":{"present":False,"slide_indices":[],
            "classification":"none","exact_visible_text":[],
            "requested_action":None,"why_it_is_or_is_not_cta":"not shown",
            "not_to_confuse_with":[]},
        "alternative_interpretations":[],
        "reproduction_blueprint":{"preserve":["sequence"],
            "replace":["avatar"],"must_verify":["claims"]}
    }


class CarouselStoryContract(unittest.TestCase):
    def test_prepare_neutral_evidence_exact_input_hashes(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)
            native=fixture(folder)
            p,meta=story.prepare(directory=folder)
            self.assertEqual(meta["slide_count"],2)
            self.assertEqual(meta["model_requests"],0)
            self.assertEqual([s["slide_index"] for s in meta["slides"]],[1,2])
            self.assertNotIn("BAD EARLIER GUESS",
                             json.dumps(meta,ensure_ascii=False))
            first=folder/("story-"+native+"-gemini3flash-input.json")
            self.assertTrue(first.is_file())
            self.assertEqual(story.prepare(directory=folder)[1],meta)
            image=folder/f"references/{native}/01.jpg"
            image.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError,"hash_changed"):
                story.prepare(directory=folder)

    def test_one_multimodal_call_eight_like_semantic_roles_and_idempotent(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)
            native=fixture(folder)
            received=[]
            def fake(raw):
                request=json.loads(raw)
                received.append(request)
                self.assertEqual(request["model"],story.MODEL)
                parts=request["messages"][1]["content"]
                self.assertEqual([x["text"] for x in parts
                                  if x["type"]=="text" and x["text"].startswith("SLIDE")],
                                 ["SLIDE 1 OF 2","SLIDE 2 OF 2"])
                self.assertEqual(sum(x["type"]=="image_url" for x in parts),2)
                self.assertEqual(request["response_format"],{"type":"json_object"})
                return {"id":"fake","choices":[{"message":{
                    "content":json.dumps(valid_response())},"finish_reason":"stop"}],
                    "usage":{"cost":0.0001,"prompt_tokens":500,
                             "completion_tokens":100}}
            result=story.run(directory=folder,client=fake)
            self.assertEqual(result["state"],"completed")
            self.assertEqual(len(received),1)
            actual=json.loads(Path(result["result_path"]).read_text("utf-8"))
            self.assertEqual([x["primary_role"] for x in actual["analysis"]["slide_roles"]],
                             ["hook","tip"])
            second=story.run(directory=folder,client=fake)
            self.assertEqual(second["state"],"already_completed_no_extra_cost")
            self.assertEqual(len(received),1)

    def test_reject_invalid_roles_and_phase_gaps(self):
        response=valid_response()
        response["slide_roles"][1]["primary_role"]="hook_always"
        with self.assertRaisesRegex(ValueError,"invalid_slide_role"):
            story._valid_response(response,2)
        response=valid_response()
        response["narrative_phases"][1]["start_slide"]=1
        with self.assertRaisesRegex(ValueError,"not_contiguous"):
            story._valid_response(response,2)

    def test_failed_response_does_not_automatically_repeat_billed_call(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)
            fixture(folder)
            calls=[]
            def bad(raw):
                calls.append(1)
                return {"choices":[{"message":{"content":"NOT VALID JSON"}}]}
            with self.assertRaisesRegex(ValueError,"story_request_failed_review_error_json"):
                story.run(directory=folder,client=bad)
            with self.assertRaisesRegex(ValueError,"explicit_retry_needed"):
                story.run(directory=folder,client=bad)
            self.assertEqual(len(calls),1)
            with self.assertRaisesRegex(ValueError,"story_request_failed_review_error_json"):
                story.run(directory=folder,client=bad,retry_failed=True)
            self.assertEqual(len(calls),2)


if __name__=="__main__":
    unittest.main()
