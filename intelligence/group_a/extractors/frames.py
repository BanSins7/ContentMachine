"""GA-MOD-07: sparse scene-aware + periodic, byte-verified video frames.

Pure deterministic planner plus explicit offline FFmpeg image sampling.
No content semantics, provider calls, full-video visual/OCR coverage claims,
or writes to a SQL database. A frame's observed PTS comes from FFmpeg's
showinfo filter, validated against the probed source video timebase.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import subprocess
import sys

from ..contracts import GroupAError, Scene, MAX_DURATION_SECONDS
from ..contracts_v1 import canonical_sha256
from ..media_manifest import VerifiedFile, open_verified
from .technical import ProbeInfo, _binary, _execute

FRAME_SCHEMA = "group-a-frame-manifest/v1"
FRAME_METHOD = "group-a-frame-sampling-v1"
MAX_BUDGET = 48
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_EDGE = 720
FRAME_TIMEOUT = 60
FRAME_RE = re.compile(r"\bpts:\s*(-?\d+)\s+pts_time:\s*(-?\d+(?:\.\d+)?)")


class FrameError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Group A frame sampling failed: " + code)


@dataclass(frozen=True, slots=True)
class SampleConfig:
    budget: int = 12
    max_edge: int = 512
    early_focus: int = 3
    duplicate_hamming: int = 5
    duplicate_color_l1: int = 24

    def __post_init__(self):
        values = (self.budget, self.max_edge, self.early_focus,
                  self.duplicate_hamming, self.duplicate_color_l1)
        if any(type(x) is not int for x in values):
            raise FrameError("invalid_config")
        if not (1 <= self.budget <= MAX_BUDGET and
                64 <= self.max_edge <= MAX_EDGE and
                0 <= self.early_focus <= min(6, self.budget) and
                0 <= self.duplicate_hamming <= 16 and
                0 <= self.duplicate_color_l1 <= 100):
            raise FrameError("invalid_config")


def _valid_time(value, field="timestamp"):
    if type(value) not in (int, str, Decimal) or type(value) is bool:
        raise FrameError("invalid_" + field)
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise FrameError("invalid_" + field) from exc
    if not v.is_finite():
        raise FrameError("invalid_" + field)
    return v


def _ms(value):
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def plan_timestamps(duration, *, scenes=(), config=None):
    """Return deterministic strictly increasing, bounded video-local offsets.

    Priority: first video frame, early focus, spread across technical scene
    midpoints, then farthest uncovered periodic slots. A scene here is ONLY
    technical A1; no semantic narrative significance is inferred.
    """
    cfg = SampleConfig() if config is None else config
    if not isinstance(cfg, SampleConfig):
        raise FrameError("invalid_config")
    duration = _valid_time(duration, "duration")
    if not Decimal("0") < duration <= MAX_DURATION_SECONDS:
        raise FrameError("invalid_duration")
    if type(scenes) not in (tuple, list):
        raise FrameError("invalid_scenes")
    end = _ms(duration)
    if end <= 0:
        raise FrameError("invalid_duration")
    # Sample targets are offsets, not invented observed source timestamps.
    # Avoid requesting exact EOF, which cannot produce a frame.
    final = max(Decimal("0"), end - Decimal("0.100"))
    candidates = {Decimal("0")}
    for idx in range(cfg.budget * 5):
        t = _ms(duration * Decimal(2 * idx + 1) / Decimal(cfg.budget * 10))
        candidates.add(min(max(Decimal("0"), t), final))
    mids = []
    for scene in scenes:
        if not isinstance(scene, Scene):
            raise FrameError("invalid_scenes")
        if not Decimal("0") <= scene.start_sec < scene.end_sec <= duration:
            raise FrameError("invalid_scenes")
        mid = min(_ms((scene.start_sec + scene.end_sec) / 2), final)
        mids.append(mid)
        candidates.add(mid)
    for sec in ("0.250", "0.750", "1.500", "3.000", "5.000"):
        d = Decimal(sec)
        if d <= final:
            candidates.add(d)
    ordered = sorted(candidates)
    selected = [Decimal("0")]
    def insert(value):
        if value not in selected and len(selected) < cfg.budget:
            selected.append(value)
    # Early shots capture hooks; always limited by the overall budget.
    early = [t for t in (Decimal("0.250"), Decimal("0.750"),
                        Decimal("1.500"), Decimal("3.000"),
                        Decimal("5.000")) if t in candidates]
    for t in early[:max(0, cfg.early_focus - 1)]:
        insert(t)
    # Spread technical-scene midpoints rather than taking first N scenes.
    while mids and len(selected) < cfg.budget:
        pool = sorted(set(mids) - set(selected))
        if not pool:
            break
        best = max(pool, key=lambda x: (min(abs(x-y) for y in selected), -x))
        insert(best)
        if len(selected) == cfg.budget:
            break
        # Reserve capacity for periodic samples: no more than half the budget
        # after the already prioritised early observations.
        if sum(1 for x in selected if x in mids) >= max(1, cfg.budget // 2):
            break
    while len(selected) < min(cfg.budget, len(ordered)):
        pool = sorted(set(ordered) - set(selected))
        if not pool:
            break
        best = max(pool, key=lambda x: (min(abs(x-y) for y in selected), -x))
        insert(best)
    return tuple(sorted(selected))


def _perceptual(png):
    """Local grayscale dHash + RGB mean; flat red and blue are NOT duplicates."""
    try:
        from PIL import Image, UnidentifiedImageError
        with Image.open(BytesIO(png)) as im:
            if im.format != "PNG" or im.width < 1 or im.height < 1:
                raise FrameError("invalid_png")
            if im.width > MAX_EDGE or im.height > MAX_EDGE:
                raise FrameError("frame_dimensions_exceeded")
            im.load()
            rgb = im.convert("RGB")
            from PIL import ImageOps
            grayscale = ImageOps.grayscale(rgb).resize((9, 8))
            pixels = list(grayscale.getdata())
            dhash = 0
            for y in range(8):
                for x in range(8):
                    dhash = (dhash << 1) | int(pixels[y*9+x] > pixels[y*9+x+1])
            channels = rgb.resize((1, 1)).getpixel((0, 0))
            return dhash, tuple(int(x) for x in channels), im.width, im.height
    except FrameError:
        raise
    except Exception as exc:
        raise FrameError("invalid_png") from exc


def _equivalent(a, b, cfg):
    (a_hash, a_color), (b_hash, b_color) = a, b
    return ((a_hash ^ b_hash).bit_count() <= cfg.duplicate_hamming
            and sum(abs(x-y) for x,y in zip(a_color,b_color)) <= cfg.duplicate_color_l1)


def _time_base(value):
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,12}/[1-9][0-9]{0,12}", value) is None:
        raise FrameError("invalid_timebase")
    return Fraction(value)


def _actual_pts(log, requested, timebase, duration):
    if type(log) is not str or len(log) > 1_000_000:
        raise FrameError("invalid_pts_log")
    # FFmpeg showinfo is emitted AFTER setpts and select but BEFORE scale.
    observed = FRAME_RE.findall(log)
    if len(observed) != 1:
        raise FrameError("missing_or_ambiguous_pts")
    pts, pts_text = observed[0]
    ticks = int(pts)
    frame_time = Fraction(ticks) * timebase
    exact = Decimal(frame_time.numerator) / Decimal(frame_time.denominator)
    logged = _valid_time(pts_text)
    if abs(exact - logged) > Decimal("0.002"):
        raise FrameError("timebase_mismatch")
    # Rounding to 3 decimal digits is for wire format only.
    actual = _ms(exact)
    if actual < requested - Decimal("0.002") or actual >= duration:
        raise FrameError("observed_pts_out_of_range")
    return str(ticks), format(actual, ".3f")


def _one_frame(ffmpeg, *, runner, input_path, pass_fds, stream_index,
               requested, max_edge, timebase, duration):
    if not 0 <= stream_index <= 1024:
        raise FrameError("invalid_stream")
    # Decode from stream start; no inaccurate approximate keyframe seeking.
    # Each request independently verifies its actual selected frame PTS.
    vf=(f"setpts=PTS-STARTPTS,select='gte(t,{requested:.3f})',"
        f"showinfo,scale={max_edge}:{max_edge}:force_original_aspect_ratio=decrease,"
        f"format=rgb24")
    args=[ffmpeg,"-nostdin","-hide_banner","-v","info",
          "-xerror","-err_detect","explode",
          "-protocol_whitelist","file,pipe","-i",input_path,
          "-map",f"0:{stream_index}","-an","-sn","-dn",
          "-vf",vf,"-frames:v","1","-fps_mode","passthrough",
          "-f","image2pipe","-c:v","png","pipe:1"]
    try:
        # Image data is binary; a text=True subprocess is unsafe for PNG.
        kw={"stdin":subprocess.DEVNULL,"stdout":subprocess.PIPE,
            "stderr":subprocess.PIPE,"timeout":FRAME_TIMEOUT,"check":False}
        if pass_fds:
            kw["pass_fds"]=pass_fds
        result=runner(args,**kw)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise FrameError("frame_decoder_unavailable") from exc
    if (type(result.returncode) is not int or result.returncode!=0):
        raise FrameError("frame_decode_failed")
    data=result.stdout
    log=result.stderr
    if type(data) is not bytes or type(log) is not bytes:
        raise FrameError("invalid_decoder_output")
    if not 0 < len(data) <= MAX_PNG_BYTES or len(log) > 1_000_000:
        raise FrameError("frame_output_limit")
    pts_ticks, pts_seconds = _actual_pts(log.decode("utf-8",errors="replace"),
                                          requested,timebase,duration)
    h,color,width,height=_perceptual(data)
    return {
        "png":data,"sha256":sha256(data).hexdigest(),
        "pts_ticks":pts_ticks,"pts_sec":pts_seconds,"perceptual_hash":f"{h:016x}",
        "color_mean":color,"width":width,"height":height,
    }


def sample_frames(media: VerifiedFile, probe: ProbeInfo, *, roots, scenes=(),
                  config=None, runner=subprocess.run, ffmpeg=None):
    """Return a manifest *draft* and PNG bytes: caller must commit atomically.

    No filename is included in the public manifest, only content-addressed
    relative refs. Store adapter writes bytes + final digest atomically.
    """
    if not isinstance(media, VerifiedFile) or media.media_kind != "video":
        raise FrameError("verified_video_required")
    if not isinstance(probe, ProbeInfo):
        raise FrameError("probe_required")
    if not isinstance(probe.stream_index,int) or probe.stream_index < 0:
        raise FrameError("invalid_stream")
    cfg=SampleConfig() if config is None else config
    if not isinstance(cfg,SampleConfig):
        raise FrameError("invalid_config")
    duration=_valid_time(probe.duration,"duration")
    timebase=_time_base(probe.time_base)
    targets=plan_timestamps(duration,scenes=scenes,config=cfg)
    ffmpeg_bin=_binary("ffmpeg",ffmpeg)
    frames=[]
    payloads={}
    recent=[]
    duplicates=0
    with open_verified(media,roots=roots) as f:
        before=os.fstat(f.fileno())
        # Linux descriptor path locks FFmpeg onto already-verified bytes and
        # closes the classic path-swap race. Other platforms use the verified
        # absolute filename with post-run descriptor/hash validation.
        fd_pass = (f.fileno(),) if sys.platform.startswith("linux") and Path("/proc/self/fd").is_dir() else ()
        input_path=f"/proc/self/fd/{f.fileno()}" if fd_pass else media.path
        for idx,t in enumerate(targets,1):
            output=_one_frame(ffmpeg_bin,runner=runner,input_path=input_path,
                              pass_fds=fd_pass,stream_index=probe.stream_index,
                              requested=t,max_edge=cfg.max_edge,
                              timebase=timebase,duration=duration)
            signature=(int(output["perceptual_hash"],16),output["color_mean"])
            if any(_equivalent(signature,prev,cfg) for prev in recent):
                duplicates+=1
                continue
            recent.append(signature)
            wire_id=f"f{idx:03d}-{output['sha256']}"
            frames.append({
                "sample_index":idx,
                "requested_sec":format(t,".3f"),
                "observed_pts_ticks":output["pts_ticks"],
                "observed_sec":output["pts_sec"],
                "frame_ref":"ga-frame:"+wire_id,
                "png_sha256":output["sha256"],
                "png_byte_size":len(output["png"]),
                "width":output["width"],"height":output["height"],
                "perceptual_hash":output["perceptual_hash"],
            })
            payloads[wire_id+".png"]=output["png"]
        after=os.fstat(f.fileno())
        if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise FrameError("media_changed_during_sampling")
    if not frames:
        raise FrameError("all_frames_suppressed")
    intervals=[Decimal(x["observed_sec"]) for x in frames]
    # Coverage is strictly sparse. Sampling observations do not justify
    # asserting what happened between frames or across unsampled scenes.
    draft={
        "schema_version":FRAME_SCHEMA,
        "method_version":FRAME_METHOD,
        "source_media_sha256":media.sha256,
        "source_byte_size":media.byte_size,
        "source_duration_sec":format(_ms(duration),".3f"),
        "source_stream_index":probe.stream_index,
        "source_timebase":probe.time_base,
        "config":{
            "budget":cfg.budget,"max_edge":cfg.max_edge,
            "early_focus":cfg.early_focus,
            "duplicate_hamming":cfg.duplicate_hamming,
            "duplicate_color_l1":cfg.duplicate_color_l1,
        },
        "coverage":{
            "type":"sparse_observations",
            "visual_temporal_coverage":"unknown",
            "ocr_temporal_coverage":"unknown",
            "requested":len(targets),"retained":len(frames),
            "duplicates_suppressed":duplicates,
            "sampled_span_sec":format(max(intervals)-min(intervals),".3f"),
            "unsampled_intervals_present":True,
        },
        "frames":frames,
    }
    return {**draft,"manifest_sha256":canonical_sha256(draft)},payloads
