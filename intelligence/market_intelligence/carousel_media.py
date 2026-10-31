"""Standalone verified photo-carousel media A/B evidence for Marketing.

No QDP / no synthetic MP4 / no claimed video duration, ASR or retention.
Acquisition uses gallery-dl (image downloader) on an exact canonical post URL.
The source identity and project rights are rechecked by carousel_jobs.

Image safety: a private project root, strict 24-slide limit, 20MB per slide,
bounded decompression, original-image SHA, digest of ordered manifest, no
symlinks or arbitrary input image URLs. Gallery-dl output is untrusted.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import re
import subprocess
import sys

from PIL import Image, ImageOps

from intelligence.discovery.normalize import canonical_video_url
from .contracts import digest
from ._copied.group_b.blocks.b1_hook import MECHANISMS
from ._copied.group_b.blocks.b2_format import CAROUSEL_FORMATS
from ._copied.group_b.blocks.b3_story import ROLES
from ._copied.group_b.blocks.b4_visual import FEATURES
from ._copied.group_b.blocks.b7_cta import CTA_TYPES
from .replica_media import _ocr_backend

MAX_SLIDES=24
MIN_SLIDES=2
MAX_SLIDE_BYTES=20_000_000
MAX_PIXEL_AREA=30_000_000
MAX_CANVAS=1536
MAX_OUTPUT_PNG=3_000_000
SUPPORTED={".jpg",".jpeg",".png",".webp"}
SCHEMA="mi-photo-carousel-media/v1"
GROUP_A="mi-carousel-group-a/v1"
GROUP_B="mi-carousel-group-b-b1-b8/v1"


class CarouselError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def carousel_candidate(url):
    try:
        p,n,canonical=canonical_video_url(url)
    except ValueError:
        return False
    from urllib.parse import urlsplit
    path=urlsplit(canonical).path
    return (p=="tiktok" and "/photo/" in path) or (
        p=="instagram" and path.startswith("/p/"))


def _sha_file(path):
    if path.is_symlink() or not path.is_file():
        raise CarouselError("slide_missing_or_unsafe")
    total=0
    h=sha256()
    with path.open("rb") as stream:
        for buf in iter(lambda:stream.read(1024*1024),b""):
            total+=len(buf)
            if total>MAX_SLIDE_BYTES:
                raise CarouselError("slide_exceeds_byte_limit")
            h.update(buf)
    if total<100:
        raise CarouselError("slide_empty_or_invalid")
    return h.hexdigest(),total


def _image(item):
    # Do not trust user or gallery-dl file extensions as proof of media type.
    try:
        with Image.open(item) as im:
            im.load()
            if im.format not in ("PNG","JPEG","WEBP"):
                raise CarouselError("unsupported_slide_format")
            if im.width<=0 or im.height<=0 or im.width*im.height>MAX_PIXEL_AREA:
                raise CarouselError("image_dimensions_outside_bounds")
            image=ImageOps.exif_transpose(im)
            width,height=image.size
            image=image.convert("RGB")
            image.thumbnail((MAX_CANVAS,MAX_CANVAS),Image.Resampling.LANCZOS)
            out=BytesIO()
            image.save(out,format="PNG",optimize=True)
            binary=out.getvalue()
            if not binary or len(binary)>MAX_OUTPUT_PNG:
                raise CarouselError("rendered_slide_oversized")
            return binary,width,height
    except (Image.DecompressionBombError,Image.DecompressionBombWarning,
            OSError,ValueError) as exc:
        if isinstance(exc,CarouselError):
            raise
        raise CarouselError("slide_decode_failed") from None


def scan_images(directory:Path,*,expected_url:str)->dict:
    """Build ordered, hash-verified source-slide bundle from acquired images."""
    directory=directory.resolve(strict=True)
    if not directory.is_dir() or directory.is_symlink():
        raise CarouselError("carousel_directory_invalid")
    from urllib.parse import urlsplit
    platform,native,canonical=canonical_video_url(expected_url)
    if not carousel_candidate(canonical):
        raise CarouselError("not_carousel_candidate")
    # A public /p/ may be a video; without >=2 real image files it is NOT a
    # verified carousel. No fabricated frames or empty "success" manifests.
    items=list(directory.iterdir())
    videos=[p for p in items if p.suffix.lower() in (".mp4",".mov",".webm",".gif")]
    paths=sorted(
        [p for p in items if p.is_file() and p.suffix.lower() in SUPPORTED],
        key=lambda p:p.name.casefold())
    if videos and paths:
        # Mixed Instagram post: cannot claim a complete image-only review.
        raise CarouselError("mixed_carousel_not_yet_supported")
    if videos and not paths:
        # Instagram /p/ can be a conventional video, so the caller may
        # fall back to the existing verified MP4 adapter.
        raise CarouselError("post_is_video_not_photo_carousel")
    if not MIN_SLIDES<=len(paths)<=MAX_SLIDES:
        raise CarouselError("carousel_slide_count_unavailable")
    # gallery-dl uses "{num:03}.{extension}" for stable ordinal. Reject
    # arbitrary files or discontinuous ordering instead of guessing slide 1.
    slide_numbers=[]
    for p in paths:
        match=re.fullmatch(r"([0-9]{3})\.(?:jpe?g|png|webp)",p.name,re.I)
        if p.is_symlink() or not match:
            raise CarouselError("slide_order_unverified")
        slide_numbers.append(int(match.group(1)))
    if slide_numbers!=list(range(1,len(paths)+1)):
        raise CarouselError("slide_order_unverified")
    slides=[]
    for number,p in enumerate(paths,1):
        content_hash,byte_size=_sha_file(p)
        png,width,height=_image(p)
        png_hash=sha256(png).hexdigest()
        slides.append({
            "slide_index":number,"file_name":p.name,
            "source_image_sha256":content_hash,"byte_size":byte_size,
            "image_width":width,"image_height":height,
            "image_png_sha256":png_hash,
            "ref":"s"+str(number)+"-"+png_hash,
        })
    manifest={
        "schema_version":SCHEMA,
        "content_kind":"photo_carousel",
        "platform":platform,"post_native_id":native,
        "canonical_post_url":canonical,
        "post_url_sha256":sha256(canonical.encode()).hexdigest(),
        "complete_ordered_slides":True,
        "slides":slides,
        "video_duration_sec":None,"video_audio":None,
        "retention":None,"slide_view_durations":None,
    }
    return {**manifest,"manifest_sha256":digest(manifest)}


def download_carousel(url:str,root:Path,*,runner=subprocess.run)->dict:
    """Collect 2-24 source slides using gallery-dl; no cookies or account bypass.

    Caller passes a project-bound path under CM_MARKET_MEDIA_ROOTS and is
    responsible for ensuring explicit source fetch/store rights.
    """
    if not carousel_candidate(url):
        raise CarouselError("unsupported_carousel_url")
    if root.is_symlink() or not root.is_dir():
        raise CarouselError("carousel_storage_root_unavailable")
    from urllib.parse import urlsplit
    platform,post_id,canonical=canonical_video_url(url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,80}",post_id):
        raise CarouselError("invalid_post_native_id")
    # The directory is deterministic for idempotency and contains only
    # images belonging to this exact post.
    target=root/("carousel-"+platform+"-"+post_id)
    if target.is_symlink():
        raise CarouselError("unsafe_carousel_target")
    if target.exists():
        # Never overwrite a partially existing directory and misclassify its
        # contents as an already-complete acquisition.
        raise CarouselError("carousel_target_exists_review_before_retry")
    target.mkdir(mode=0o700)
    cmd=[sys.executable,"-m","gallery_dl",
         "--config-ignore","--no-input","--directory",str(target),
         # Include one extra position to detect and reject a carousel
         # longer than our capacity; never label first 24 slides COMPLETE.
         "--range","1-"+str(MAX_SLIDES+1),
         "--filename","{num:03}.{extension}",
         canonical]
    try:
        finished=runner(cmd,capture_output=True,timeout=300,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise CarouselError("carousel_provider_unavailable") from None
    if finished.returncode!=0:
        # Do not leak provider stdout (URLs/cookies/PII) into project reports.
        raise CarouselError("carousel_gallery_dl_failed")
    result=scan_images(target,expected_url=canonical)
    if result["platform"]!=platform or result["post_native_id"]!=post_id:
        raise CarouselError("carousel_source_identity_mismatch")
    return result


def _slide_payload(folder:Path,slide:dict):
    path=folder/slide["file_name"]
    if path.is_symlink() or path.parent.resolve()!=folder.resolve():
        raise CarouselError("slide_path_untrusted")
    actual,_=_sha_file(path)
    if actual!=slide["source_image_sha256"]:
        raise CarouselError("carousel_source_changed")
    png,width,height=_image(path)
    if sha256(png).hexdigest()!=slide["image_png_sha256"]:
        raise CarouselError("carousel_render_changed")
    if width!=slide["image_width"] or height!=slide["image_height"]:
        raise CarouselError("carousel_dimensions_changed")
    return png


def _ocr(png,backend):
    if backend is None:
        return {"state":"unavailable","reason":"ocr_backend_not_configured",
                "words":[]}
    from ._copied.group_a.extractors.ocr import OCRConfig
    try:
        raw=backend.process(png,OCRConfig(languages=("eng","rus"),
            timeout_sec=15,batch_timeout_sec=300,
            missing_language="fallback_eng"))
        return {"state":raw.get("state","unavailable"),
                "reason":raw.get("error_code"),
                "words":[w.get("text") for w in raw.get("words",[])
                         if isinstance(w,dict) and type(w.get("text")) is str][:150]}
    except (ValueError,OSError):
        return {"state":"unavailable","reason":"ocr_engine_failed","words":[]}


def analyze_carousel(manifest:dict,folder:Path,*,allow_llm=False,
                     inference=None,backend=None)->dict:
    """Each of the actual original slides is inspected once in ordinal order.

    No video time coordinate is ever invented. Inferred B labels come only
    from a validated evidence-grounded provider response.
    """
    if (type(manifest) is not dict or manifest.get("schema_version")!=SCHEMA
        or manifest.get("complete_ordered_slides") is not True
        or len(manifest.get("slides") or [])<MIN_SLIDES
        or digest({k:v for k,v in manifest.items() if k!="manifest_sha256"})
           !=manifest.get("manifest_sha256")):
        raise CarouselError("carousel_manifest_not_verified")
    slides=manifest["slides"]
    if len(slides)>MAX_SLIDES or [s.get("slide_index") for s in slides]!=list(
        range(1,len(slides)+1)):
        raise CarouselError("carousel_order_unverified")
    folder=folder.resolve(strict=True)
    ocr_engine=backend if backend is not None else _ocr_backend()
    observed=[]
    blobs={}
    for slide in slides:
        png=_slide_payload(folder,slide)
        blobs[slide["ref"]]=png
        observed.append({
            "slide_index":slide["slide_index"],
            "ref":slide["ref"],
            "image_png_sha256":slide["image_png_sha256"],
            "image_width":slide["image_width"],
            "image_height":slide["image_height"],
            "ocr":_ocr(png,ocr_engine),
        })
    group_a={
        "schema_version":GROUP_A,
        "content_kind":"photo_carousel",
        "source_manifest_sha256":manifest["manifest_sha256"],
        "slide_count":len(slides),
        "slide_observations":observed,
        "image_decode":"complete",
        "ocr_state":("observed" if all(v["ocr"]["state"]!="unavailable"
                                     for v in observed) else "partial_or_unavailable"),
        "video_technical":"not_applicable",
        "audio_asr":"unavailable_no_verified_carousel_soundtrack",
        "video_frame_sampling":"not_applicable_all_slides_examined",
        "slide_dwell_time":"unavailable",
        "text_tracking":"static_slide_order_only_not_video_timestamps",
        "original_full_video_a_certified":False,
    }
    group_a["report_sha256"]=digest(group_a)
    model={"state":"unavailable","reason":"model_not_authorized_or_unconfigured"}
    if allow_llm:
        if inference is not None:
            model=inference(observed,blobs)
        else:
            from .carousel_vision import classify_carousel
            model=classify_carousel(observed,blobs)
    group_b=build_carousel_b(observed,model,manifest["manifest_sha256"],
                              group_a["report_sha256"])
    return {"content_kind":"photo_carousel","group_a":group_a,
            "group_b":group_b,"visual_model":model.get("state","unavailable"),
            "original_full_abc_certified":False}


def build_carousel_b(slides:list[dict],inferred:dict,manifest_sha:str,a_sha:str)->dict:
    """Eight B stages with image-only applicability and exact slide refs."""
    if type(inferred) is not dict:
        raise CarouselError("carousel_inference_invalid")
    refs={s["ref"] for s in slides}
    trusted=inferred.get("state")=="model_observed_partial"
    selected=inferred.get("evidence_slide_refs",[]) if trusted else []
    if (type(selected) is not list or len(set(selected))!=len(selected)
        or any(type(r) is not str or r not in refs for r in selected)):
        raise CarouselError("carousel_model_forged_slide_refs")
    state="model_observed_partial" if trusted else "unavailable"
    p=inferred if trusted else {}
    slide1=slides[0]["ref"]
    # First slide is the carousel hook. No duration/first-five-seconds claim.
    hook_refs=[r for r in selected if r==slide1]
    # Exact text requires same-slide OCR evidence. A vision model's string
    # alone is a proposal and cannot be marked a verbatim observation.
    def verbatim(text,observations):
        if type(text) is not str or not text.strip():
            return None
        want=" ".join(text.split()).casefold()
        for slide in observations:
            observed=" ".join(str(x) for x in slide.get("ocr",{}).get("words",[]))
            if want in " ".join(observed.split()).casefold():
                return text
        return None
    hook_text=verbatim(p.get("hook_text"),slides[:1]) if hook_refs else None
    cta_text=verbatim(p.get("cta_text"),slides) if trusted else None
    block=lambda st,e=None,why=None,**kw:dict(
        state=st,evidence_slide_refs=e or [],reason=why,
        original_b_receipt_verified=False,**kw)
    taxonomy={"state":"unavailable","reason":"no_grounded_topic_labels"}
    if trusted and selected and p.get("category_label") and p.get("topic_label"):
        from .group_b_taxonomy import resolve_topic
        taxonomy=resolve_topic({
            "category_label":p["category_label"],
            "topic_label":p["topic_label"],
            "subtopic_label":p.get("subtopic_label"),
        },selected)
    blocks={
        "B1":block(state if hook_refs else "unavailable",hook_refs,
                   None if hook_refs else "first_slide_hook_not_observed",
                   hook_mechanisms=[x for x in p.get("hook_mechanisms",[])
                                    if x in MECHANISMS] if hook_refs else [],
                   hook_text=hook_text,
                   hook_text_verbatim_ocr_verified=hook_text is not None,
                   hook_visual=p.get("hook_visual") if hook_refs else None,
                   hook_slide_index=1),
        "B2":block(state if p.get("carousel_format") in CAROUSEL_FORMATS else "unavailable",
                   selected,carousel_format=p.get("carousel_format")
                       if p.get("carousel_format") in CAROUSEL_FORMATS else None),
        "B3":block(state,selected,story_roles=[r for r in p.get("story_roles",[])
                                      if r in ROLES] if trusted else [],
                   reading_axis="slide_index_not_seconds"),
        "B4":block(state,selected,visual_features={
            x:("present" if p.get("visual_features",{}).get(x)=="present"
               else "unknown") for x in FEATURES}),
        "B5":block("not_applicable",why="no_video_editing_or_shot_timeline",
                   slide_transitions=len(slides)-1,
                   slideshow_video_not_assumed=True),
        "B6":block("static_ocr_observed" if any(s["ocr"]["words"] for s in slides)
                   else "unavailable",
                   [s["ref"] for s in slides if s["ocr"]["words"]],
                   slide_ocr=[{"slide_index":s["slide_index"],
                               "words":s["ocr"]["words"][:60]}
                               for s in slides],
                   video_subtitles_not_applicable=True),
        "B7":block(state if p.get("cta_type") in CTA_TYPES else "unavailable",
                   selected,
                   cta_type=p.get("cta_type") if p.get("cta_type") in CTA_TYPES else None,
                   cta_text=cta_text,
                   cta_text_verbatim_ocr_verified=cta_text is not None),
        "B8":block(taxonomy.get("state","unavailable"),
                   selected if taxonomy.get("state")=="catalog_matched" else [],
                   taxonomy_proposal=taxonomy,
                   topic_hint=p.get("topic_hint") if trusted else None,
                   category_id=taxonomy.get("category_id"),
                   topic_id=taxonomy.get("topic_id"),
                   subtopic_id=taxonomy.get("subtopic_id"),
                   approved_topic_match=taxonomy.get("mi_approved_topic_match") is True),
    }
    report={
        "schema_version":GROUP_B,
        "source_manifest_sha256":manifest_sha,
        "group_a_report_sha256":a_sha,
        "content_kind":"photo_carousel",
        "blocks":blocks,
        "slide_count":len(slides),
        "whole_video_B_certified":False,
        "no_fabricated_timestamps":True,
    }
    return {**report,"report_sha256":digest(report)}
