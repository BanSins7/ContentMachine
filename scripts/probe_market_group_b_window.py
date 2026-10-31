"""One-window paid Marketing B vision diagnostic; never retries whole video.

Use existing 01.mp4 and previous SHA-pinned evidence. The local Group A pass is
offline; re-sampled PNGs MUST match the first pass's full manifest digest.
Exactly one selected <=8-frame window is sent to OpenRouter with a process-
local explicit opt-in. Output remains a partial semantic observation only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from intelligence import config
from intelligence.market_intelligence.replica_b_timeline import _safe_error_code
from intelligence.market_intelligence.replica_vision import (
    MAX_INPUT_FRAMES, classify_grounded,
)
from intelligence.market_intelligence._copied.group_a.media_manifest import verify_file
from intelligence.market_intelligence._copied.group_a.extractors.frames import (
    sample_frames, SampleConfig,
)
from intelligence.market_intelligence.market_frame_compat import run_frame_ffmpeg
from scripts.probe_market_group_b_live import (
    preflight, BoundedLiveRequester, LiveRequestError,
)

MAX_WINDOW_REQUESTS = 1


def select_window(manifest: dict, *, number: int, duration_sec: float):
    """Window numbering matches the original B vision's 8-frame chunks."""
    frames = manifest.get("frames", [])
    if type(number) is not int or number < 1:
        raise ValueError("invalid_window_number")
    if not isinstance(frames, list) or not frames:
        raise ValueError("no_verified_frames")
    offset = (number - 1) * MAX_INPUT_FRAMES
    if offset >= len(frames):
        raise ValueError("window_out_of_range")
    selected = frames[offset:offset + MAX_INPUT_FRAMES]
    start = float(selected[0]["observed_sec"])
    at_end = offset + MAX_INPUT_FRAMES >= len(frames)
    end = (duration_sec if at_end
           else float(frames[offset + MAX_INPUT_FRAMES]["observed_sec"]))
    if not 0 <= start < end <= duration_sec:
        raise ValueError("invalid_sampled_window_bounds")
    return selected, {
        "window": number,
        "start_sec": start,
        "end_sec": end,
        "sampled_frame_count": len(selected),
        "end_boundary_kind": ("video_end_unobserved" if at_end
                              else "next_sampled_frame"),
    }


def inspect_window(video: Path, *, number: int, authorized: bool,
                   baseline_report: Path | None = None):
    # No media is sent without explicit authorization, even if a saved key exists.
    model = preflight(video, authorized=authorized)
    if number < 1 or number > 6:
        raise ValueError("window_number_out_of_range")
    roots = [str(video.parent)]
    hasher = hashlib.sha256()
    with video.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    sha = hasher.hexdigest()
    baseline_window = None
    if baseline_report is not None:
        if not baseline_report.is_file() or baseline_report.stat().st_size > 8_000_000:
            raise ValueError("baseline_report_missing_or_too_large")
        try:
            baseline = json.loads(baseline_report.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise ValueError("baseline_report_invalid") from None
        if (type(baseline) is not dict or baseline.get("video_sha256") != sha
                or type(baseline.get("vision_windows")) is not list
                or number > len(baseline["vision_windows"])):
            raise ValueError("baseline_video_or_window_mismatch")
        baseline_window = baseline["vision_windows"][number - 1]
        if (type(baseline_window) is not dict
                or baseline_window.get("state") != "unavailable"):
            raise ValueError("baseline_window_was_not_failed")

    # First pass: existing Group A, completely OFFLINE.
    os.environ["CM_MARKET_AUDIO_EVENTS_ENABLED"] = "0"
    from intelligence.market_intelligence.replica_media import analyze_media, FRAME_BUDGET
    tech, observation = analyze_media(
        video, sha, roots=roots, project_id="b" * 32,
        srt_rows=[], allow_provider=False,
    )

    # Rehydrate verified PNG bytes for this one selected chunk. The earlier
    # offline Group A manifest is the cryptographic reference, not the path.
    source = verify_file(str(video), media_kind="video", sha256=sha, roots=roots)
    budget = min(FRAME_BUDGET, max(12, int(float(tech.measurement.duration_sec) * 2)))
    manifest, pngs = sample_frames(
        source, tech.probe, roots=roots,
        scenes=tech.measurement.scenes,
        config=SampleConfig(budget=budget, max_edge=512, early_focus=4),
        runner=run_frame_ffmpeg,
    )
    previous = observation.get("group_a_frame_manifest") or {}
    if (not previous.get("manifest_sha256")
            or manifest["manifest_sha256"] != previous["manifest_sha256"]):
        raise ValueError("source_frame_manifest_mismatch")
    selected, window = select_window(
        manifest, number=number,
        duration_sec=float(tech.measurement.duration_sec),
    )
    if baseline_window is not None:
        if (abs(float(baseline_window.get("start_sec", -1))
                - window["start_sec"]) > 0.001
                or abs(float(baseline_window.get("end_sec", -1))
                       - window["end_sec"]) > 0.001
                or baseline_window.get("sampled_frame_count")
                    != window["sampled_frame_count"]):
            raise ValueError("baseline_sampled_window_mismatch")

    # The original B timeline accepts ONLY time-verified GA11 transcript.
    transcript = [
        item for item in (observation.get("asr_segments") or [])
        if isinstance(item, dict)
        and type(item.get("start_sec")) in (float, int)
        and window["start_sec"] - 0.1 <= item["start_sec"] <= window["end_sec"] + 0.1
    ]
    report = {
        "schema_version": "marketing-single-b-window-live-check/v1",
        "video_sha256": sha,
        "frame_manifest_sha256": manifest["manifest_sha256"],
        "group_a_report_sha256": observation["group_a_report"]["report_sha256"],
        "model": model,
        **window,
        "frame_refs": [item["frame_ref"] for item in selected],
        "attempted_remote_requests": 0,
        "completed_remote_requests": 0,
        "max_remote_requests": MAX_WINDOW_REQUESTS,
        "full_video_semantic_certified": False,
        "whole_group_b_certified": False,
    }

    # Process-local opt-in affects only the selected paid vision call.
    os.environ["CM_MARKET_VISION_MODEL"] = model
    os.environ["CM_MARKET_VISION_ENABLED"] = "1"
    os.environ["CM_MARKET_MEDIA_TO_PROVIDER_ALLOWED"] = "1"
    requester = BoundedLiveRequester(max_requests=MAX_WINDOW_REQUESTS)
    try:
        inferred = classify_grounded(
            frames=selected, png_blobs=pngs,
            transcript=transcript,
            duration_sec=float(tech.measurement.duration_sec),
            requester=requester,
        )
        report["state"] = inferred["state"]
        report["vision_evidence"] = inferred
        report["reason"] = inferred.get("reason")
    except Exception as exc:
        # Omit exception messages: the provider may echo user media or secrets.
        report["state"] = "unavailable"
        report["reason"] = "model_or_validation_error"
        report["error_type"] = type(exc).__name__
        report["error_code"] = _safe_error_code(exc)
    report["attempted_remote_requests"] = requester.attempted
    report["completed_remote_requests"] = requester.completed
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window", type=int, default=4)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--allow-media-upload", action="store_true")
    args = parser.parse_args(argv)
    try:
        video = args.video.resolve(strict=True)
        report = inspect_window(
            video, number=args.window, authorized=args.allow_media_upload,
            baseline_report=args.baseline_report)
    except (ValueError, LiveRequestError) as exc:
        # Preflight error codes contain no secrets; never dump a traceback
        # for a provider response in this test utility.
        parser.exit(2, "Preflight/local verification failed: " + str(exc) + "\n")
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(
        report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Report:", target)
    print("Window:", report["window"],
          "start:", report["start_sec"], "end:", report["end_sec"])
    print("Requests attempted:", report["attempted_remote_requests"])
    print("State:", report["state"])
    if report.get("error_code"):
        print("Error code:", report["error_code"])
    if report.get("error_type"):
        print("Error type:", report["error_type"])
    return 0 if report["state"] == "model_observed_partial" else 2


if __name__ == "__main__":
    sys.exit(main())
