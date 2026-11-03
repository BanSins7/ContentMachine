"""GA-MOD-12 local OCR: strict observed-frame words and pixel geometry.

Tesseract is the CPU backend; engines are explicit injected OCRBackend
interfaces. No OCR result means UNKNOWN, never verified absence of text in
video. Frames are bound to GA07 immutable PNG/manifest SHA before use.
No provider calls, frames URL, SQL writer, background queue or model download.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import re
import unicodedata
from typing import Protocol, runtime_checkable

from ..contracts_v1 import canonical_sha256
from .frame_store import _validate as validate_frame_batch, PROJECT_RE, UUID_RE
from .frames import FRAME_SCHEMA

OCR_SCHEMA="ga-frame-ocr/v1"
OCR_METHOD="group-a-local-frame-ocr-v1"
MAX_WORDS_PER_FRAME=2000
MAX_WORD_TEXT=350
MAX_FRAME_BUDGET=48
SHA_RE=re.compile(r"^[a-f0-9]{64}$")
SUPPORTED_LANGS=frozenset(("eng","rus"))
OCR_STATES=frozenset(("text_observed","no_text_detected","partial_language",
                     "unavailable","failed","skipped_budget"))
SUCCESS_STATES=frozenset(("text_observed","no_text_detected","partial_language"))


class OCRError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("Group A frame OCR rejected: "+code)


@dataclass(frozen=True,slots=True)
class OCRConfig:
    languages: tuple[str,...]=("eng","rus")
    frame_budget: int=12
    timeout_sec: int=15
    batch_timeout_sec: int=150
    scale: int=2
    psm: int=6
    rotations: tuple[int,...]=(0,)
    preprocess: str="autocontrast"
    missing_language: str="fail"  # or explicit 'fallback_eng'
    cpu_threads: int=1
    device: str="cpu"

    def __post_init__(self):
        if (type(self.languages) is not tuple or not 1<=len(self.languages)<=2
            or any(type(x) is not str or x not in SUPPORTED_LANGS for x in self.languages)
            or len(set(self.languages))!=len(self.languages)
            or type(self.frame_budget) is not int or not 1<=self.frame_budget<=MAX_FRAME_BUDGET
            or type(self.timeout_sec) is not int or not 1<=self.timeout_sec<=30
            or type(self.batch_timeout_sec) is not int or not 15<=self.batch_timeout_sec<=900
            or type(self.scale) is not int or self.scale not in (1,2)
            or type(self.psm) is not int or self.psm not in (3,6,11,12)
            or type(self.rotations) is not tuple or not 1<=len(self.rotations)<=4
            or any(type(x) is not int or x not in (0,90,180,270) for x in self.rotations)
            or len(set(self.rotations))!=len(self.rotations)
            or self.preprocess not in ("none","autocontrast","sharpen")
            or self.missing_language not in ("fail","fallback_eng")
            or type(self.cpu_threads) is not int or not 1<=self.cpu_threads<=4
            or self.device!="cpu"):
            raise OCRError("invalid_ocr_config")

    def wire(self):
        return {
            "languages":list(self.languages),"frame_budget":self.frame_budget,
            "timeout_sec":self.timeout_sec,"batch_timeout_sec":self.batch_timeout_sec,
            "scale":self.scale,"psm":self.psm,"rotations":list(self.rotations),
            "preprocess":self.preprocess,
            "missing_language":self.missing_language,
            "cpu_threads":self.cpu_threads,"device":self.device,
        }


@runtime_checkable
class OCRBackend(Protocol):
    @property
    def identity(self) -> dict: ...
    def process(self,png:bytes,config:OCRConfig) -> dict: ...


def _identity(backend):
    try:
        source=backend.identity
    except (AttributeError,TypeError) as exc:
        raise OCRError("ocr_backend_missing_identity") from exc
    if (type(source) is not dict or set(source)!={"engine","engine_version","model_sha256","device"}
        or source["engine"] not in ("tesseract","paddleocr")
        or type(source["engine_version"]) is not str or
        not re.fullmatch(r"[a-zA-Z0-9._+-]{1,60}",source["engine_version"])
        or type(source["model_sha256"]) is not str
        or not SHA_RE.fullmatch(source["model_sha256"])
        or source["device"] not in ("cpu","cuda")):
        raise OCRError("ocr_unverified_backend_identity")
    if not callable(getattr(backend,"process",None)):
        raise OCRError("ocr_backend_missing_process")
    return dict(source)


def _number(value,*,low=0,high=100):
    if type(value) not in (int,str,float,Decimal) or type(value) is bool:
        raise OCRError("ocr_confidence_invalid")
    try:
        x=Decimal(str(value))
        if not x.is_finite() or not Decimal(low)<=x<=Decimal(high):
            raise ValueError()
    except (ValueError,InvalidOperation,OverflowError) as exc:
        raise OCRError("ocr_confidence_invalid") from exc
    return format(x.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP),".2f")


def _text(value):
    if type(value) is not str:
        raise OCRError("ocr_text_invalid")
    value=unicodedata.normalize("NFC",value.strip())
    if (not value or len(value)>MAX_WORD_TEXT
        or any(ord(c)<32 for c in value)):
        raise OCRError("ocr_text_invalid")
    return value


def _words(rows, width,height):
    if type(rows) is not list or len(rows)>MAX_WORDS_PER_FRAME:
        raise OCRError("ocr_words_invalid")
    output=[]
    seen=set()
    for row in rows:
        if type(row) is not dict or set(row)!={"text","confidence_pct","bbox","line_ref"}:
            raise OCRError("ocr_word_invalid")
        box=row["bbox"]
        if (type(box) is not dict or set(box)!={"x","y","width","height"}
            or any(type(box[k]) is not int for k in ("x","y","width","height"))):
            raise OCRError("ocr_box_invalid")
        x,y,w,h=(box[k] for k in ("x","y","width","height"))
        if not (0<=x<width and 0<=y<height and 0<w<=width-x and 0<h<=height-y):
            raise OCRError("ocr_box_outside_frame")
        ref=row["line_ref"]
        if type(ref) is not str or not re.fullmatch(r"l[0-9]{1,6}",ref):
            raise OCRError("ocr_line_ref_invalid")
        text=_text(row["text"])
        conf=_number(row["confidence_pct"])
        key=(ref,x,y,w,h,text)
        if key in seen:
            continue
        seen.add(key)
        output.append({"text":text,"confidence_pct":conf,
                       "bbox":{"x":x,"y":y,"width":w,"height":h},
                       "line_ref":ref})
    # Reading order is provided by the engine and is not a guarantee of
    # scene-level temporal sequence; future GA13 does temporal tracking.
    return output


def _checked_frame(frame, raw, backend_identity, cfg):
    if (type(raw) is not dict
        or set(raw)!={"state","words","languages_used","engine_rotation","error_code"}
        or raw["state"] not in ("text_observed","no_text_detected","partial_language")
        or type(raw["languages_used"]) is not list
        or not raw["languages_used"]
        or any(type(x) is not str or x not in cfg.languages
               for x in raw["languages_used"])
        or len(set(raw["languages_used"]))!=len(raw["languages_used"])
        or type(raw["engine_rotation"]) is not int or raw["engine_rotation"] not in cfg.rotations
        or raw["error_code"] is not None):
        raise OCRError("ocr_backend_output_invalid")
    if raw["state"]!="partial_language" and set(raw["languages_used"])!=set(cfg.languages):
        raise OCRError("ocr_unreported_language_gap")
    if raw["state"]=="partial_language" and set(raw["languages_used"])==set(cfg.languages):
        raise OCRError("ocr_false_partial_language")
    words=_words(raw["words"],frame["width"],frame["height"])
    if raw["state"]=="text_observed" and not words:
        raise OCRError("ocr_text_evidence_missing")
    if raw["state"]=="no_text_detected" and words:
        raise OCRError("ocr_false_empty_claim")
    if not words and raw["state"]=="partial_language":
        text_status="unknown"
    elif words:
        text_status="text_observed"
    else:
        text_status="no_text_detected_on_this_frame_only"
    mean=None
    if words:
        avg=sum((Decimal(x["confidence_pct"]) for x in words),Decimal(0))/len(words)
        mean=_number(avg)
    return {"state":raw["state"],"text_status":text_status,
            "reason_code":None,
            "languages_used":raw["languages_used"],
            "engine_rotation":raw["engine_rotation"],
            "words":words,"word_count":len(words),
            "mean_engine_confidence_pct":mean}


def _frame_wire(frame,observed):
    return {
        "frame_ref":frame["frame_ref"],
        "frame_sha256":frame["png_sha256"],
        "sample_index":frame["sample_index"],
        "observed_sec":frame["observed_sec"],
        "width":frame["width"],"height":frame["height"],
        **observed,
    }


def _empty_frame(frame,state,reason):
    return _frame_wire(frame,{
        "state":state,"text_status":"unknown","reason_code":reason,
        "languages_used":[],"engine_rotation":None,"words":[],
        "word_count":None,"mean_engine_confidence_pct":None,
    })


def _validate_frame_index(manifest,blobs):
    # Storage's authoritative frame-manifest validator checks SHA, image
    # dimensions and every PNG perceptual digest before any OCR/cache work.
    validate_frame_batch(manifest,blobs)
    if manifest.get("schema_version")!=FRAME_SCHEMA:
        raise OCRError("frame_contract_incompatible")
    return tuple(manifest["frames"])


class OCRCache:
    """Ephemeral scoped memo. Current project rights checked on *every* hit.

    Model digest, engine version, preprocessing/config and frame SHA are part
    of the cache identity. Never claim that source-media digest grants rights.
    Cache is not a cross-tenant permanent OCR catalog or a model cache.
    """
    def __init__(self,max_items=256):
        if type(max_items) is not int or not 1<=max_items<=2048:
            raise ValueError("invalid_ocr_cache_size")
        self.max_items=max_items
        self._memo={}

    @staticmethod
    def key(project_id,run_id,manifest,frame,identity,config):
        return canonical_sha256({
            "project_id":project_id,"run_id":run_id,
            "source_sha256":manifest["source_media_sha256"],
            "manifest_sha256":manifest["manifest_sha256"],
            "frame_sha256":frame["png_sha256"],
            "frame_ref":frame["frame_ref"],
            "ocr_model":identity,"config":config.wire(),
        })

    def get(self,key,*,verify_current,context):
        if not callable(verify_current) or verify_current(context) is not True:
            self._memo.pop(key,None)
            raise OCRError("ocr_cache_rights_unverified")
        value=self._memo.get(key)
        return None if value is None else json.loads(json.dumps(value))

    def put(self,key,result):
        if type(result) is not dict or result.get("state") not in SUCCESS_STATES:
            raise OCRError("ocr_cache_payload_invalid")
        if len(self._memo)>=self.max_items:
            self._memo.pop(next(iter(self._memo)))
        self._memo[key]=json.loads(json.dumps(result))


def analyze_frame_ocr(frame_manifest, png_blobs, *, backend=None, config=None,
                      project_id, run_id, cache=None, verify_current=None):
    """Use GA07 byte-verified PNGs; never infer text in unsampled intervals.

    Project & run namespace are explicit. A live authorized host MUST validate
    both against current QDP/project rights and current input manifest before
    invoking this explicit offline utility. verify_current is REQUIRED if a
    memo is used, rechecked for every cached hit.
    """
    cfg=OCRConfig() if config is None else config
    if not isinstance(cfg,OCRConfig):
        raise OCRError("invalid_ocr_config")
    if (type(project_id) is not str or not PROJECT_RE.fullmatch(project_id)
        or type(run_id) is not str or not UUID_RE.fullmatch(run_id)):
        raise OCRError("invalid_ocr_scope")
    frames=_validate_frame_index(frame_manifest,png_blobs)
    if cache is not None and not isinstance(cache,OCRCache):
        raise OCRError("invalid_ocr_cache")
    if backend is None:
        identity=None
    else:
        identity=_identity(backend)
        if identity["device"]!=cfg.device:
            raise OCRError("ocr_device_mismatch")
    import time
    started=time.monotonic()
    results=[]
    counters={"attempted":0,"observed":0,"failed":0,
              "unavailable":0,"skipped_budget":0,
              "partial_language":0,"text_observed":0,
              "no_text_detected":0,"cached":0}
    for index,frame in enumerate(frames):
        if index>=cfg.frame_budget:
            results.append(_empty_frame(frame,"skipped_budget","ocr_frame_budget_exhausted"))
            counters["skipped_budget"]+=1
            continue
        counters["attempted"]+=1
        if identity is None:
            results.append(_empty_frame(frame,"unavailable","ocr_backend_not_connected"))
            counters["unavailable"]+=1
            continue
        if time.monotonic()-started>cfg.batch_timeout_sec:
            results.append(_empty_frame(frame,"unavailable","ocr_batch_time_exceeded"))
            counters["unavailable"]+=1
            continue
        filename=f"f{frame['sample_index']:03d}-{frame['png_sha256']}.png"
        content=png_blobs[filename]
        key=None
        context={"project_id":project_id,"run_id":run_id,
                 "source_media_sha256":frame_manifest["source_media_sha256"],
                 "frame_manifest_sha256":frame_manifest["manifest_sha256"],
                 "frame_sha256":frame["png_sha256"]}
        if cache is not None:
            key=cache.key(project_id,run_id,frame_manifest,frame,identity,cfg)
            cached=cache.get(key,verify_current=verify_current,context=context)
            if cached is not None:
                results.append(_frame_wire(frame,cached))
                counters["cached"]+=1
                counters["observed"]+=1
                counters[cached["state"]]+=1
                continue
        try:
            raw=backend.process(content,cfg)
            normalized=_checked_frame(frame,raw,identity,cfg)
            if cache is not None:
                cache.put(key,normalized)
            counters["observed"]+=1
            counters[normalized["state"]]+=1
            results.append(_frame_wire(frame,normalized))
        except OCRError as exc:
            results.append(_empty_frame(frame,
                "unavailable" if exc.code in (
                    "ocr_engine_unavailable","ocr_language_unavailable",
                    "ocr_resource_unavailable","ocr_model_mismatch"
                ) else "failed",exc.code))
            counters[results[-1]["state"]]+=1
        except Exception:
            # Do not include private filesystem paths or raw recognized text
            # in exceptions; a failed frame MUST stay unknown.
            results.append(_empty_frame(frame,"failed","ocr_backend_failed"))
            counters["failed"]+=1
    if counters["attempted"] != (
        counters["observed"]+counters["failed"]+counters["unavailable"]):
        raise OCRError("ocr_accounting_mismatch")
    core={
        "schema_version":OCR_SCHEMA,"method_version":OCR_METHOD,
        "source_media_sha256":frame_manifest["source_media_sha256"],
        "frame_manifest_sha256":frame_manifest["manifest_sha256"],
        "project_id":project_id,"run_id":run_id,
        "state":"partial" if counters["observed"] else "unavailable",
        "temporal_coverage":"unknown",
        "screen_text_absence":"unknown",
        "unsampled_intervals_present":True,
        "model":identity,"config":cfg.wire(),
        "counts":counters,"frames":results,
    }
    return {**core,"observation_sha256":canonical_sha256(core)}
