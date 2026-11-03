"""Offline regression for explicit retry of ONLY failed carousel rows.

72 completed + 21 paid-format errors + 6 media errors + 1 needs_review.
Never collects Bright Data, never re-sends successful/ambiguous LLM work.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence import carousel_studio as st
from intelligence.market_intelligence import carousel_studio_http as http

RUN="b"*32
PROFILE="https://www.tiktok.com/@example"

def url(native):
    return PROFILE+"/photo/"+native


class Handler:
    def __init__(self,body,confirmed=True):
        raw=json.dumps(body).encode("utf-8")
        self.headers={
            "X-ContentMachine":"1" if confirmed else "0",
            "Content-Type":"application/json",
            "Content-Length":str(len(raw)),
        }
        self.rfile=BytesIO(raw)
        self.calls=[]
    def json_response(self,code,body):
        self.calls.append((code,body))


class TestRetryFailedOnly(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        p=patch.object(st,"ROOT",self.root)
        p.start()
        self.addCleanup(p.stop)
        (self.root/RUN).mkdir()
        with st.db() as conn:
            conn.execute("""INSERT INTO studio_runs(
                id,source_kind,source_input,max_carousels,provider,model,
                state,phase,progress,total,completed,failed,created_at,updated_at,output_dir)
                VALUES(?,'profile',?,100,'rusgpt',?,'partial','Завершено',100,
                       100,72,27,?,?,?)""",(
                RUN,PROFILE,st.RUSGPT_MODEL,st.utc(),st.utc(),str(self.root/RUN)))
            for index in range(100):
                native=str(7000000000000000000+index)
                if index<72:
                    state,error="completed",None
                    analysis=json.dumps({"overall_story_summary":"Saved "+native})
                elif index<93:
                    state,error="failed","story_model_invalid_json"
                    analysis=None
                elif index<99:
                    state,error="failed","carousel_slide_count_unavailable"
                    analysis=None
                else:
                    state,error="needs_review","LLM payment uncertain"
                    analysis=None
                conn.execute("""INSERT INTO studio_targets
                    (run_id,native_id,ordinal,canonical_url) VALUES(?,?,?,?)""",
                    (RUN,native,index,url(native)))
                conn.execute("""INSERT INTO studio_items
                    (run_id,native_id,canonical_url,state,error,analysis_json)
                    VALUES(?,?,?,?,?,?)""",(
                    RUN,native,url(native),state,error,analysis))
                folder=self.root/RUN/"carousels"/native
                if state=="failed" and index<93:
                    folder.mkdir(parents=True)
                    (folder/"manifest.json").write_text(json.dumps({
                        "slides":[{"slide_index":1,"file_name":"001.jpg"}]
                    }),encoding="utf-8")
                    (folder/"gemini-request.json").write_text(json.dumps({
                        "provider":"rusgpt","model":st.RUSGPT_MODEL,
                        "prompt_sha256":st.story.PROMPT_SHA,
                        "slide_hashes":["a"*64],
                        "state":"started_no_automatic_retry"
                    }),encoding="utf-8")
                if state=="needs_review":
                    folder.mkdir(parents=True)
                    (folder/"gemini-request.json").write_text(json.dumps({
                        "provider":"rusgpt","model":st.RUSGPT_MODEL,
                        "state":"started_no_automatic_retry",
                    }),encoding="utf-8")

    def test_payment_consent_required_and_keeps_original_rows(self):
        before=st.get_run(RUN)
        self.assertEqual(before["retryable_failed"],27)
        self.assertEqual(before["retryable_paid_failed"],21)
        self.assertEqual(before["retryable_media_failed"],6)
        self.assertEqual(before["completed"],72)
        self.assertEqual(before["manual_review"],1)
        with self.assertRaisesRegex(ValueError,"Подтвердите повторную оплату"):
            st.retry_failed(RUN,confirm_paid_retries=False)
        after=st.get_run(RUN)
        self.assertEqual(after["completed"],72)
        self.assertEqual(after["failed"],27)
        self.assertEqual(after["state"],"partial")

    def test_only_27_failed_requeued_and_processes_without_brightdata(self):
        ambiguous=str(7000000000000000099)
        original_marker=(self.root/RUN/"carousels"/ambiguous/
                         "gemini-request.json").read_bytes()
        with patch.object(st,"_thread") as thread:
            result=st.retry_failed(RUN,confirm_paid_retries=True)
            thread.assert_called_once_with(RUN,resume=True)
        self.assertEqual(result["selected"],27)
        self.assertEqual(result["new_paid_llm_calls_possible"],27)
        self.assertEqual(result["repeat_paid_llm_calls_possible"],21)
        self.assertEqual(result["first_llm_after_media_recovery"],6)
        before=st.get_run(RUN)
        self.assertEqual(before["completed"],72)
        self.assertEqual(before["failed"],0)
        self.assertEqual(before["manual_review"],1)
        self.assertEqual(sum(x["state"]=="queued" for x in before["items"]),27)
        self.assertEqual(sum(x["state"]=="completed" for x in before["items"]),72)
        self.assertEqual((self.root/RUN/"carousels"/ambiguous/
                          "gemini-request.json").read_bytes(),original_marker)
        self.assertTrue((self.root/RUN/"carousels"/
                        str(7000000000000000072)/
                        "gemini-request-before-retry-001.json").exists())

        def fake_download(link,folder):
            return {"post_native_id":link.rsplit("/",1)[-1],
                    "slides":[{"slide_index":1,"file_name":"001.jpg"}]}
        def fake_analyze(folder,items,*,provider,model):
            return ({"overall_story_summary":"retried"},
                    {"prompt_tokens":100,"completion_tokens":100,
                     "estimated_cost_rub":0.036})
        with patch.object(st,"_trigger_profile",side_effect=AssertionError(
                "no profile collection")),patch.object(st,"_trigger_metrics",
                side_effect=AssertionError("no Bright Data detail")),patch.object(
                st,"download_carousel",side_effect=fake_download) as download,patch.object(
                st,"_jpeg_inputs",return_value=[{
                    "slide_index":1,"relative_image":"mock/image.jpg",
                    "input_jpeg_sha256":"a"*64}]),patch.object(
                st,"_analyze",side_effect=fake_analyze) as llm,patch.object(
                st,"_package"):
            st._process(RUN,resume=True)
        self.assertEqual(download.call_count,6)
        self.assertEqual(llm.call_count,27)
        self.assertTrue(all(call.kwargs["provider"]=="rusgpt" for call in llm.call_args_list))
        after=st.get_run(RUN)
        self.assertEqual(after["completed"],99)
        self.assertEqual(after["failed"],0)
        self.assertEqual(after["manual_review"],1)
        self.assertEqual(after["state"],"partial")
        self.assertEqual((self.root/RUN/"carousels"/ambiguous/
                          "gemini-request.json").read_bytes(),original_marker)

    def test_http_route_requires_explicit_consent(self):
        req=Handler({"confirm_paid_retries":False})
        with patch.object(http.studio,"retry_failed") as action:
            self.assertTrue(http.handle_post(req,"/api/studio/runs/"+RUN+"/retry-failed"))
            action.assert_called_once_with(RUN,confirm_paid_retries=False)
        no_header=Handler({"confirm_paid_retries":True},confirmed=False)
        with patch.object(http.studio,"retry_failed") as action:
            self.assertTrue(http.handle_post(
                no_header,"/api/studio/runs/"+RUN+"/retry-failed"))
            action.assert_not_called()
        self.assertEqual(no_header.calls[0][0],400)


if __name__=="__main__":
    unittest.main()
