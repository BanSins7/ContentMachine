"""Bounded, local-only FFprobe/FFmpeg extraction of technical Group A A1 signals.

No network access; never download, transcribe, OCR, classify CTA or invent media.
Source permission and QDP eligibility are checked by repository before AND after
calling this module. The local path must reside in an explicitly configured root.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import gcd
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

from .contracts import (
    GroupAError, MAX_DURATION_SECONDS, MAX_SCENES,
    Scene, TechnicalMeasurement, parse_config, seconds
)

VIDEO_EXTENSIONS = frozenset((".mp4",".mov",".mkv",".webm"))
PTS_RE = re.compile(r"\bpts_time:\s*(-?\d+(?:\.\d+)?)")
MAX_LOCAL_BYTES = 2 * 1024 * 1024 * 1024


def _allowed_roots(roots=None) -> tuple[Path,...]:
    if roots is None:
        raw=os.environ.get("CM_GROUP_A_MEDIA_ROOTS","")
        roots = raw.split(os.pathsep) if raw else []
    if not roots or any(type(root) is not str or not root.strip() for root in roots):
        raise GroupAError("media_roots_unconfigured",
                          "Configure CM_GROUP_A_MEDIA_ROOTS with authorized local folders",409)
    directories=[]
    for value in roots:
        path=Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise GroupAError("invalid_media_root","Authorized media root is not a directory",409)
        directories.append(path)
    return tuple(directories)


def authorized_file(raw_path: str, *, roots=None) -> Path:
    if type(raw_path) is not str or not raw_path.strip() or "\x00" in raw_path:
        raise GroupAError("invalid_media_path","Invalid media path",409)
    candidate=Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise GroupAError("invalid_media_path","Media path must be absolute",409)
    try:
        path=candidate.resolve(strict=True)
        approved=_allowed_roots(roots)
        if not any(path.is_relative_to(root) for root in approved):
            raise GroupAError("media_path_not_allowed","Media asset outside configured roots",403)
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise GroupAError("media_type_not_supported","Expected a local video file",422)
        size=path.stat().st_size
        if not 0<size<=MAX_LOCAL_BYTES:
            raise GroupAError("media_file_limit","Media file is empty or exceeds the 2 GiB limit",422)
        return path
    except (OSError,RuntimeError) as exc:
        raise GroupAError("media_unavailable","Cannot access authorized media",409) from exc


def _binary(name: str, provided=None) -> str:
    binary=provided if provided is not None else shutil.which(name)
    if type(binary) is not str or not binary:
        raise GroupAError("tool_not_installed",name + " must be available on PATH",503)
    path=Path(binary)
    if not path.is_file():
        raise GroupAError("tool_not_installed",name + " is not a regular executable",503)
    return str(path.resolve())


def _execute(args, *, timeout, runner=subprocess.run):
    try:
        result=runner(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE,text=True,timeout=timeout,
                      check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise GroupAError("extractor_unavailable","Local media tool failed or timed out",503) from exc
    if type(result.returncode) is not int or result.returncode!=0:
        raise GroupAError("extractor_failed","Local media extractor returned an error",422)
    if len(result.stdout)>2_000_000 or len(result.stderr)>10_000_000:
        raise GroupAError("extractor_output_limit","Extractor produced too much output",422)
    return result


def inspect_probe(raw: str) -> tuple[Decimal,str]:
    """Reject audio-only, invalid/nonfinite durations and distorted geometry."""
    try:
        parsed=json.loads(raw)
        if type(parsed) is not dict:
            raise ValueError()
        streams=parsed.get("streams")
        if type(streams) is not list:
            raise ValueError()
        videos=[s for s in streams if type(s) is dict and s.get("codec_type")=="video"]
        if len(videos)!=1:
            raise ValueError()
        video=videos[0]
        width,height=video.get("width"),video.get("height")
        if type(width) is not int or type(height) is not int or not 1<=width<=16384 or not 1<=height<=16384:
            raise ValueError()
        # Container duration is authoritative fallback when a video stream
        # reports "N/A" (common for MKV/WebM). Never invent missing duration.
        candidates=(video.get("duration"),(parsed.get("format") or {}).get("duration"))
        duration=None
        for candidate in candidates:
            try:
                duration=seconds(candidate,"duration")
                break
            except GroupAError:
                continue
        if duration is None:
            raise ValueError("No verified stream or container duration")
        if duration>MAX_DURATION_SECONDS:
            raise GroupAError("duration_limit","Max video duration is 600 seconds",422)
        sar=video.get("sample_aspect_ratio") or "1:1"
        if sar in ("N/A","0:1","0:0"):
            raise ValueError()
        parts=sar.split(":")
        if len(parts)!=2:
            raise ValueError()
        sar_n,sar_d=[int(x) for x in parts]
        if not 1<=sar_n<=100000 or not 1<=sar_d<=100000:
            raise ValueError()
        numer,denom=width*sar_n,height*sar_d
        d=gcd(numer,denom)
        return duration,f"{numer//d}:{denom//d}"
    except GroupAError:
        raise
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError) as exc:
        raise GroupAError("invalid_probe","FFprobe did not return valid video geometry/duration",422) from exc


def parse_cuts(log: str, duration: Decimal, min_gap: Decimal) -> tuple[Scene,...]:
    """FFmpeg 'showinfo' PTS are candidate cuts, not semantic/story scenes."""
    candidates=[]
    for line in log.splitlines():
        if "showinfo" not in line:
            continue
        match=PTS_RE.search(line)
        if match is None:
            continue
        cut=seconds(match.group(1),"scene_pts",allow_zero=True)
        if Decimal("0")<cut<duration:
            candidates.append(cut)
    candidates.sort()
    boundaries=[Decimal("0")]
    for cut in candidates:
        if cut-boundaries[-1]>=min_gap and duration-cut>=min_gap:
            boundaries.append(cut)
        if len(boundaries)>MAX_SCENES:
            raise GroupAError("too_many_scenes","Scene detector exceeded configured limits",422)
    boundaries.append(duration)
    return tuple(Scene(i+1,start,end)
                 for i,(start,end) in enumerate(zip(boundaries,boundaries[1:])))


def measure(path: Path, config: dict, *, runner=subprocess.run,
            ffprobe=None, ffmpeg=None) -> TechnicalMeasurement:
    cfg=parse_config({k:config[k] for k in ("scene_threshold","min_scene_gap_sec")})
    file=str(path)
    probe=_execute([_binary("ffprobe",ffprobe),"-v","error",
                    "-protocol_whitelist","file,pipe",
                    "-show_entries","format=duration:stream=codec_type,width,height,duration,sample_aspect_ratio",
                    "-of","json",file],timeout=30,runner=runner)
    duration,ratio=inspect_probe(probe.stdout)
    # FFmpeg is strictly local. This command decodes the video and detects
    # abrupt cuts, without storing/streaming decoded video or audio.
    command=[_binary("ffmpeg",ffmpeg),"-nostdin","-hide_banner","-v","info",
             "-protocol_whitelist","file,pipe","-i",file,
             "-map","0:v:0","-an","-sn","-dn",
             "-vf",f"select='gt(scene,{cfg['scene_threshold']})',showinfo",
             "-f","null","-"]
    result=_execute(command,timeout=210,runner=runner)
    scenes=parse_cuts(result.stderr,duration,Decimal(cfg["min_scene_gap_sec"]))
    return TechnicalMeasurement(duration_sec=duration,aspect_ratio=ratio,scenes=scenes)
