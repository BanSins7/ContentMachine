"""One real public photo-carousel smoke probe, isolated from production D1.

Run with a user-specified public photo-post URL after confirming download rights:
python -m scripts.probe_market_carousel_one --url "https://.../photo/..." \
    --output "marketing-qa/reports/carousel-one.json"

This performs real public image downloads through gallery-dl, decodes every
slide and runs Marketing's native A/B image path without any LLM or Bright Data.
It does not claim to be a project-authorized production batch, nor C0-C7.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from intelligence.market_intelligence.carousel_media import (
    carousel_candidate,download_carousel,scan_images,analyze_carousel,CarouselError)
from intelligence.discovery.normalize import canonical_video_url

ROOT=Path(__file__).resolve().parents[1]
QA_ROOT=ROOT/"marketing-qa"
QA_PHOTOS=QA_ROOT/"carousel-media"
QA_REPORTS=QA_ROOT/"reports"


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",required=True,
        help="Exact public TikTok /photo/ or Instagram /p/ post URL")
    parser.add_argument("--output",type=Path,required=True,
        help="New JSON QA output under marketing-qa/reports")
    parser.add_argument("--reuse-downloaded",action="store_true",
        help="Reinspect existing source slides; no download or network call")
    parser.add_argument("--expected-manifest-sha256",
        help="For reused slides: verified SHA-256 from the original download report")
    args=parser.parse_args(argv)
    if args.reuse_downloaded and not args.expected_manifest_sha256:
        parser.error("--reuse-downloaded requires original --expected-manifest-sha256")
    try:
        if not carousel_candidate(args.url):
            raise CarouselError("not_supported_photo_url")
        output=args.output.resolve()
        QA_REPORTS.mkdir(parents=True,exist_ok=True)
        if not output.is_relative_to(QA_REPORTS.resolve()):
            raise CarouselError("output_must_be_inside_marketing_qa_reports")
        if output.exists():
            raise CarouselError("output_already_exists_no_overwrite")
        QA_PHOTOS.mkdir(parents=True,exist_ok=True)
        platform,native,canonical=canonical_video_url(args.url)
        work=QA_PHOTOS/(platform+"-"+native)
        work.mkdir(parents=True,exist_ok=True)
        folder=work/("carousel-"+platform+"-"+native)
        if args.reuse_downloaded:
            # Read the actual unchanged local images without re-downloading.
            manifest=scan_images(folder,expected_url=canonical)
            if manifest["manifest_sha256"]!=args.expected_manifest_sha256:
                raise CarouselError("carousel_source_manifest_changed")
        else:
            # download_carousel creates an immutable exact-post folder.
            manifest=download_carousel(canonical,work)
        evidence=analyze_carousel(manifest,folder,allow_llm=False)
        report={
            "schema_version":"marketing-real-carousel-one-qa/v1",
            "post_url_sha256":sha256(canonical.encode()).hexdigest(),
            "platform":platform,"native_id":native,
            "source_manifest_sha256":manifest["manifest_sha256"],
            "downloaded_actual_images":len(manifest["slides"]),
            "all_slide_hashes_verified":True,
            "slide_order_verified":True,
            "group_a":evidence["group_a"],
            "group_b":evidence["group_b"],
            "source_reused_without_download":args.reuse_downloaded,
            "group_c":"unavailable_in_standalone_probe_requires_real_d1_metric_snapshots",
            "paid_brightdata_triggers":0,
            "paid_visual_model_calls":0,
            "video_duration_fabricated":False,
            "not_production_rights_certification":True,
        }
        # Never replace a prior report.
        with output.open("x",encoding="utf-8") as file:
            json.dump(report,file,ensure_ascii=False,indent=2)
            file.write("\n")
        print(json.dumps({
            "state":"real_carousel_a_b_inspected",
            "platform":platform,
            "native_id":native,
            "actual_slides":len(manifest["slides"]),
            "source_manifest_sha256":manifest["manifest_sha256"],
            "group_a_ocr_state":evidence["group_a"]["ocr_state"],
            "ocr_slides":evidence["group_a"]["ocr_slide_states"],
            "ocr_words_total":sum(len(slide["ocr"]["words"])
                                   for slide in evidence["group_a"]["slide_observations"]),
            "group_b_stages":{k:v["state"] for k,v in evidence["group_b"]["blocks"].items()},
            "group_c":"not_in_this_probe",
            "report_file":str(output),
            "billable_api_requests":0,
        },ensure_ascii=False,indent=2))
        return 0
    except (CarouselError,OSError,ValueError) as exc:
        code=getattr(exc,"code","carousel_probe_io_error")
        parser.exit(2,"BLOCKED: "+code+"\n")


if __name__=="__main__":
    sys.exit(main())
