"""GA-MOD-06 independent bounded FFprobe/FFmpeg technical extraction.

Observations are low-level A facts, NOT video meaning, CTA, speech, OCR,
subtitles or audio events. FFmpeg sees only an authorized local file.
PTS is reset to stream-local zero before scene-cut detection, regardless of
container/video stream start offset. Legacy A1 public DTO stays unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from math import gcd
from pathlib import Path
from types import SimpleNamespace
import json
import re
import shutil
import subprocess

from ..contracts import (
    GroupAError, MAX_DURATION_SECONDS, MAX_SCENES,
    Scene, TechnicalMeasurement, parse_config, seconds,
)

PTS_RE = re.compile(r"\bpts_time:\s*(-?\d+(?:\.\d+)?)")
FRAME_RE = re.compile(r"(?m)^frame=(\d+)\s*$")
MAX_PROBE_CHARS = 2_000_000
MAX_FFMPEG_LOG_CHARS = 10_000_000
PROBE_TIMEOUT_SEC = 30
DECODE_TIMEOUT_SEC = 210
METHOD_VERSION = "group-a-ffmpeg-technical-v2"
SUPPORTED_CODECS = frozenset((
    "h264", "hevc", "mpeg4", "vp8", "vp9", "av1", "prores", "mjpeg",
))
# Exclude audio-only, subtitle-only and attached cover images explicitly.
MAX_DIMENSION = 16384


@dataclass(frozen=True, slots=True)
class ProbeInfo:
    duration: Decimal
    aspect_ratio: str
    stream_index: int
    codec_name: str | None
    time_base: str | None
    start_time: Decimal | None
    duration_origin: str


@dataclass(frozen=True, slots=True)
class TechnicalEvidence:
    """Information relevant to reproducibility, not unobserved A fields."""
    schema_version: str
    method_version: str
    measurement: TechnicalMeasurement
    probe: ProbeInfo
    scene_threshold: str
    min_scene_gap_sec: str
    decoded_frames: int | None
    ffprobe_version: str | None
    ffmpeg_version: str | None

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "method_version": self.method_version,
            "measurement": self.measurement.to_dict(),
            "probe": {
                "duration_origin": self.probe.duration_origin,
                "time_base": self.probe.time_base,
                "start_time_sec": None if self.probe.start_time is None else str(self.probe.start_time),
                "stream_index": self.probe.stream_index,
                "codec_name": self.probe.codec_name,
            },
            "scene_threshold": self.scene_threshold,
            "min_scene_gap_sec": self.min_scene_gap_sec,
            "decoded_frames": self.decoded_frames,
            "ffprobe_version": self.ffprobe_version,
            "ffmpeg_version": self.ffmpeg_version,
            "analysis_state": "partial",
            "content_semantics": "unknown",
        }


def _duration(value):
    if type(value) not in (str, int, float, Decimal) or type(value) is bool:
        return None
    try:
        raw=Decimal(str(value))
        if not raw.is_finite() or raw<=0 or raw>MAX_DURATION_SECONDS+Decimal("0.001"):
            return None
        value=seconds(value, "duration")
    except (GroupAError,ValueError,InvalidOperation):
        return None
    if not 0<value<=MAX_DURATION_SECONDS:
        return None
    return value


def _timebase(value):
    if type(value) is not str:
        return None
    if re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*",value) is None:
        raise GroupAError("invalid_probe","Invalid FFprobe video timebase")
    try:
        timebase=Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise GroupAError("invalid_probe","Invalid FFprobe video timebase") from exc
    if not 0<timebase<=Decimal("60"):
        raise GroupAError("invalid_probe","Invalid FFprobe video timebase")
    return f"{timebase.numerator}/{timebase.denominator}"


def _start_time(value):
    if value is None or value=="N/A":
        return None
    if type(value) not in (str,int,float,Decimal) or type(value) is bool:
        raise GroupAError("invalid_probe","Invalid FFprobe start time")
    try:
        dec=Decimal(str(value))
        if not dec.is_finite() or not Decimal("-86400")<=dec<=Decimal("86400"):
            raise ValueError()
        return dec.quantize(Decimal("0.001"))
    except (ValueError,InvalidOperation,OverflowError) as exc:
        raise GroupAError("invalid_probe","Invalid FFprobe start time") from exc


def probe_details(raw: str, *, strict_codec: bool = False) -> ProbeInfo:
    """Parse real FFprobe JSON preserving timeline metadata.

    Width/FPS/codec information is internal QA context, not public A1 facts.
    In strict mode refuse nondecodable/unknown codecs and missing timebase.
    """
    try:
        if type(raw) is not str or len(raw)>MAX_PROBE_CHARS:
            raise ValueError()
        payload=json.loads(raw,parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
        if type(payload) is not dict or type(payload.get("streams")) is not list:
            raise ValueError()
        streams=payload["streams"]
        videos=[
            (i,s) for i,s in enumerate(streams)
            if type(s) is dict and s.get("codec_type")=="video"
            and not (type(s.get("disposition")) is dict and
                     s["disposition"].get("attached_pic") in (True,1))
        ]
        if len(videos)!=1:
            raise ValueError("Expected exactly one primary video stream")
        enumerated,video=videos[0]
        stream_index=video.get("index",enumerated)
        if type(stream_index) is not int or stream_index<0 or stream_index>1024:
            raise ValueError("Invalid video stream index")
        width,height=video.get("width"),video.get("height")
        if (type(width) is not int or type(height) is not int or
            not 1<=width<=MAX_DIMENSION or not 1<=height<=MAX_DIMENSION):
            raise ValueError("Missing or invalid geometry")
        sar=video.get("sample_aspect_ratio") or "1:1"
        if type(sar) is not str or re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*",sar) is None:
            raise ValueError("Invalid sample aspect ratio")
        sn,sd=[int(v) for v in sar.split(":")]
        if sn>100000 or sd>100000:
            raise ValueError("Invalid sample aspect ratio")
        n,d=width*sn,height*sd
        common=gcd(n,d)
        ratio=f"{n//common}:{d//common}"
        source=None
        duration=None
        for origin,value in (("stream",video.get("duration")),
                             ("container",(payload.get("format") or {}).get("duration"))):
            duration=_duration(value)
            if duration is not None:
                source=origin
                break
        if duration is None:
            raise GroupAError("invalid_probe","FFprobe duration is unknown or outside bounds")
        codec=video.get("codec_name")
        if codec is not None and (type(codec) is not str or
                                  not re.fullmatch(r"[a-z0-9_]{1,40}",codec)):
            raise ValueError("Invalid video codec")
        time_base=_timebase(video.get("time_base"))
        start_time=_start_time(video.get("start_time"))
        if strict_codec and (codec not in SUPPORTED_CODECS or time_base is None):
            raise GroupAError("unsupported_codec",
                              "Video codec or stream timebase not supported")
        return ProbeInfo(duration,ratio,stream_index,codec,time_base,start_time,source)
    except GroupAError:
        raise
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as exc:
        raise GroupAError("invalid_probe",
                          "FFprobe did not return a valid single video stream",422) from exc


def inspect_probe(raw: str) -> tuple[Decimal,str]:
    """Legacy A1 tuple (duration, display aspect)."""
    data=probe_details(raw)
    return data.duration,data.aspect_ratio


def parse_cuts(log: str, duration: Decimal, min_gap: Decimal) -> tuple[Scene,...]:
    """Only valid, finite, local-video timestamps; deterministic debounce."""
    if (type(log) is not str or len(log)>MAX_FFMPEG_LOG_CHARS or
        not isinstance(duration,Decimal) or not 0<duration<=MAX_DURATION_SECONDS or
        not isinstance(min_gap,Decimal) or not 0<min_gap<=Decimal("2.000")):
        raise GroupAError("invalid_scene_log","Invalid FFmpeg log or timeline")
    candidates=[]
    for line in log.splitlines():
        if "showinfo" not in line:
            continue
        match=PTS_RE.search(line)
        if match is None:
            continue
        try:
            raw=Decimal(match.group(1))
            if not raw.is_finite():
                raise ValueError()
            cut=raw.quantize(Decimal("0.001"))
        except (ValueError,InvalidOperation,OverflowError) as exc:
            raise GroupAError("invalid_scene_log","Malformed frame PTS") from exc
        if Decimal("0")<cut<duration:
            candidates.append(cut)
    candidates.sort()
    boundaries=[Decimal("0")]
    for cut in candidates:
        if cut-boundaries[-1]>=min_gap and duration-cut>=min_gap:
            if len(boundaries)>=MAX_SCENES:
                raise GroupAError("too_many_scenes","Scene count exceeds 300")
            boundaries.append(cut)
    boundaries.append(duration)
    return tuple(Scene(i,a,b) for i,(a,b) in enumerate(
        zip(boundaries,boundaries[1:]),start=1))


def _binary(name: str, provided=None) -> str:
    binary=provided if provided is not None else shutil.which(name)
    if type(binary) is not str or not binary:
        raise GroupAError("tool_not_installed",name+" must be on PATH",503)
    path=Path(binary)
    if not path.is_file():
        raise GroupAError("tool_not_installed",name+" must be a regular file",503)
    return str(path.resolve())


def _execute(args, *, timeout, runner=subprocess.run):
    try:
        result=runner(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE,text=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise GroupAError("extractor_unavailable",
                          "Local tool failed or timed out",503) from exc
    if type(result.returncode) is not int or result.returncode!=0:
        raise GroupAError("extractor_failed","Local media tool exited unsuccessfully",422)
    if (type(result.stdout) is not str or type(result.stderr) is not str or
        len(result.stdout)>MAX_PROBE_CHARS or len(result.stderr)>MAX_FFMPEG_LOG_CHARS):
        raise GroupAError("extractor_output_limit","Extractor log exceeds limit",422)
    return result


def _versions(args, runner):
    """Version reporting must never fabricate an actual executable version."""
    try:
        result=_execute(args,timeout=10,runner=runner)
        line=result.stdout.splitlines()[0][:160] if result.stdout.splitlines() else None
        return line if line and re.match(r"^(?:ffmpeg|ffprobe) version\b",line) else None
    except (GroupAError,IndexError):
        return None


def _frames(stdout: str) -> int | None:
    matches=FRAME_RE.findall(stdout)
    if not matches:
        return None
    count=int(matches[-1])
    return count if 0<count<=20_000_000 else None


def analyze_technical(path: Path, config=None, *, runner=subprocess.run,
                      ffprobe=None, ffmpeg=None, strict=True,
                      include_versions=False) -> TechnicalEvidence:
    """Run local tools with exact stream mapping and bounded timeouts.

    strict=True checks a real codec/time_base and decoded-frame progress.
    legacy A1 measure() sets strict=False for old injectable fake runners.
    """
    if not isinstance(path,Path) or not path.is_file():
        raise GroupAError("media_unavailable","Expected an authorized local file",409)
    cfg=parse_config(config)
    probe_bin=_binary("ffprobe",ffprobe)
    ffmpeg_bin=_binary("ffmpeg",ffmpeg)
    file=str(path)
    probe=_execute([
        probe_bin,"-v","error","-protocol_whitelist","file,pipe",
        "-show_entries",
        "format=duration,start_time:stream=index,codec_type,codec_name,width,height,duration,sample_aspect_ratio,time_base,start_time,disposition",
        "-of","json",file,
    ],timeout=PROBE_TIMEOUT_SEC,runner=runner)
    details=probe_details(probe.stdout,strict_codec=strict)
    # Reset PTS before scene detection so nonzero source start_time cannot
    # shift cuts; showinfo observes the normalized, selected frame PTS.
    ffmpeg_args=[
        ffmpeg_bin,"-nostdin","-hide_banner","-v","info",
        "-protocol_whitelist","file,pipe","-i",file,
        "-map",f"0:{details.stream_index}","-an","-sn","-dn",
        "-vf",f"setpts=PTS-STARTPTS,select='gt(scene,{cfg['scene_threshold']})',showinfo",
        "-fps_mode","passthrough","-progress","pipe:1",
        "-f","null","-",
    ]
    result=_execute(ffmpeg_args,timeout=DECODE_TIMEOUT_SEC,runner=runner)
    frames=_frames(result.stdout)
    if strict and frames is None:
        raise GroupAError("decode_unverified","FFmpeg did not confirm decoded frames",422)
    scenes=parse_cuts(result.stderr,details.duration,Decimal(cfg["min_scene_gap_sec"]))
    measured=TechnicalMeasurement(duration_sec=details.duration,
                                  aspect_ratio=details.aspect_ratio,scenes=scenes)
    probe_version=None
    ffmpeg_version=None
    if include_versions:
        probe_version=_versions([probe_bin,"-version"],runner)
        ffmpeg_version=_versions([ffmpeg_bin,"-version"],runner)
    return TechnicalEvidence(
        schema_version="group-a-technical-evidence/v1",method_version=METHOD_VERSION,
        measurement=measured,probe=details,
        scene_threshold=cfg["scene_threshold"],
        min_scene_gap_sec=cfg["min_scene_gap_sec"],
        decoded_frames=frames,ffprobe_version=probe_version,
        ffmpeg_version=ffmpeg_version,
    )


def measure(path: Path, config: dict, *, runner=subprocess.run,
            ffprobe=None, ffmpeg=None) -> TechnicalMeasurement:
    """Old public A1 signature, unchanged result DTO and partial truth."""
    return analyze_technical(
        path,config,runner=runner,ffprobe=ffprobe,ffmpeg=ffmpeg,
        strict=False,include_versions=False,
    ).measurement
