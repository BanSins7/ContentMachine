"""Independent MI technical contracts (adapted source from A1). Unmeasured features remain unknown.

Only technical measurements from a locally authorized video are accepted here.
Semantic roles, CTA classification, subtitles, ASR, OCR and audio-classification
must never be fabricated from FFprobe/scene-cut results.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import re

METHOD = "mi-local-video-technical-v1"
GROUP = "MI"
JOB_TYPE = "market_intelligence.analyze_video"
MAX_DURATION_SECONDS = Decimal("600.000")
MAX_SCENES = 300
VIDEO_KINDS = frozenset(("video", "slideshow_video"))
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


class GroupAError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code, self.http_status = code, http_status


def canonical_hash(obj) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return sha256(text.encode("utf-8")).hexdigest()


def positive_int(value, field: str):
    if type(value) is not int or value <= 0:
        raise GroupAError("invalid_id", "Invalid " + field)
    return value


def parse_config(raw=None) -> dict:
    if raw is None:
        raw = {}
    if type(raw) is not dict or set(raw)-{"scene_threshold","min_scene_gap_sec"}:
        raise GroupAError("invalid_config", "Unexpected Group A configuration fields")
    values = {}
    for name,default,low,high in (
        ("scene_threshold","0.35",Decimal("0.10"),Decimal("0.90")),
        ("min_scene_gap_sec","0.25",Decimal("0.10"),Decimal("2.00")),
    ):
        value=raw.get(name,default)
        if type(value) is bool or type(value) not in (int,float,str,Decimal):
            raise GroupAError("invalid_config", name + " must be a finite number")
        try:
            converted=Decimal(str(value))
        except InvalidOperation as exc:
            raise GroupAError("invalid_config", name + " must be numeric") from exc
        if not converted.is_finite() or not low<=converted<=high:
            raise GroupAError("invalid_config", name + " is outside approved bounds")
        values[name]=str(converted.quantize(Decimal("0.001"),rounding=ROUND_HALF_UP))
    return {"schema_version":"market-intelligence-config/v1",**values}


def seconds(raw, field, *, allow_zero=False) -> Decimal:
    try:
        if raw is None or type(raw) is bool:
            raise ValueError()
        value=Decimal(str(raw)).quantize(Decimal("0.001"),rounding=ROUND_HALF_UP)
    except (ValueError,TypeError,InvalidOperation) as exc:
        raise GroupAError("invalid_observation", "Invalid " + field) from exc
    if not value.is_finite() or value < 0 or (value==0 and not allow_zero):
        raise GroupAError("invalid_observation", "Nonpositive " + field)
    return value


@dataclass(frozen=True, slots=True)
class Scene:
    index: int
    start_sec: Decimal
    end_sec: Decimal


@dataclass(frozen=True, slots=True)
class TechnicalMeasurement:
    duration_sec: Decimal
    aspect_ratio: str
    scenes: tuple[Scene,...]
    method_version: str = METHOD

    def __post_init__(self):
        if not 0<self.duration_sec<=MAX_DURATION_SECONDS:
            raise GroupAError("invalid_observation", "Invalid measured video duration")
        if re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*",self.aspect_ratio) is None:
            raise GroupAError("invalid_observation", "Invalid display aspect ratio")
        if not 1<=len(self.scenes)<=MAX_SCENES:
            raise GroupAError("invalid_observation", "Invalid measured scene count")
        expected=Decimal("0")
        for i,scene in enumerate(self.scenes,1):
            if scene.index!=i or scene.start_sec!=expected:
                raise GroupAError("invalid_observation", "Gapped or overlapping scenes")
            if scene.end_sec<=scene.start_sec or scene.end_sec>self.duration_sec:
                raise GroupAError("invalid_observation", "Scene outside video bounds")
            expected=scene.end_sec
        if expected!=self.duration_sec:
            raise GroupAError("invalid_observation", "Final scene must cover video duration")

    def to_dict(self):
        return {"schema_version":"market-intelligence-measurement/v1",
                "method_version":self.method_version,
                "duration_sec":str(self.duration_sec),
                "aspect_ratio":self.aspect_ratio,
                "scenes":[{"scene_index":s.index,
                           "start_sec":str(s.start_sec),
                           "end_sec":str(s.end_sec)} for s in self.scenes],
                "scene_status":"complete",
                "speech_status":"not_run",
                "screen_text_status":"not_run",
                "audio_status":"not_run",
                "has_subtitles":"unknown",
                "spoken_words":None,
                "cta_start_sec":None}

