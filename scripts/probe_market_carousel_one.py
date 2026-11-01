"""One real public photo-carousel smoke probe, isolated from production D1.

Run with a user-specified public photo-post URL after confirming download rights:
python -m scripts.probe_market_carousel_one --url "https://.../photo/..." \
    --output "marketing-qa/reports/carousel-one.json"

Default: download once (or reuse already downloaded slides), decode all source
images, and run local A and OCR-backed B6 with NO external AI or Bright Data.
--vision-readiness checks model config without network/charges.
--with-vision is a separate explicitly enabled, potentially billable model
call that transmits the eight images to OpenRouter; it is NOT a project rights
certificate. Production batch mode requires independently verified post rights.
Neither option collects Group C or claims certified full B1–B8.
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
    parser.add_argument("--vision-readiness",action="store_true",
        help="Check model, provider transfer and key settings locally; no network or billing")
    parser.add_argument("--with-vision",action="store_true",
        help="Explicit operator opt-in: transmit these images and make one potentially paid OpenRouter model request")
    args=parser.parse_args(argv)
    if args.with_vision and args.vision_readiness:
        parser.error("--with-vision cannot be combined with --vision-readiness")
    if args.vision_readiness:
        from intelligence import config
        from intelligence.market_intelligence.replica_vision import _enabled
        from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
        readiness={
            "model_name":config.setting("CM_MARKET_VISION_MODEL","").strip() or None,
            "vision_enabled":config.setting("CM_MARKET_VISION_ENABLED","")=="1",
            "transfer_permitted":config.setting("CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED","")=="1",
            "credential_present":bool(get_market_openrouter_key()),
            "provider_model_ready":bool(_enabled()),
            "network_calls":0,"billable_requests":0,
            "user_source_rights_not_checked_in_standalone_qa":True,
        }
        print(json.dumps(readiness,ensure_ascii=False,indent=2))
        return 0
    if args.with_vision and not args.reuse_downloaded:
        parser.error("--with-vision requires --reuse-downloaded to prevent accidental re-download")
    if args.with_vision:
        from intelligence.market_intelligence.replica_vision import _enabled
        if not _enabled():
            parser.error("model_not_ready_run_--vision-readiness_first")

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
        # --with-vision is the explicit operator-approved paid transfer
        # switch. Normal reuse/QA never sends images to OpenRouter.
        evidence=analyze_carousel(manifest,folder,allow_llm=args.with_vision)
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
            "visual_model_requested":args.with_vision,
            "visual_model_state":evidence["visual_model"],
            "paid_visual_model_calls_maximum":1 if args.with_vision else 0,
            "paid_visual_model_calls":0 if not args.with_vision else None,
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
            "visual_model_state":evidence["visual_model"],
            "visual_model_requested":args.with_vision,
            "group_b_stages":{k:v["state"] for k,v in evidence["group_b"]["blocks"].items()},
            "group_c":"not_in_this_probe",
            "report_file":str(output),
            "billable_api_requests":0 if not args.with_vision else None,
            "visual_model_max_paid_requests":1 if args.with_vision else 0,
        },ensure_ascii=False,indent=2))
        return 0
    except (CarouselError,OSError,ValueError) as exc:
        code=getattr(exc,"code","carousel_probe_io_error")
        parser.exit(2,"BLOCKED: "+code+"\n")


if __name__=="__main__":
    sys.exit(main())
