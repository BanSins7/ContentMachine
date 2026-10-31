"""Marketing-only FFmpeg compatibility adapter for the frozen Group A sampler.

Keep _copied/group_a pristine. A one-PNG FFmpeg extraction may still push
several decoded frames through showinfo before the output -frames:v 1 cap
takes effect. The frozen sampler correctly refuses ambiguous PTS logs.
Cap the *filter's output* first, then observe exactly that emitted frame.
No synthetic timestamps, no altered evidence, no provider calls.
"""
from __future__ import annotations

import subprocess


def run_frame_ffmpeg(args, **kwargs):
    """Execute a frozen Group A frame request with a one-frame filter fence.

    Only used as sample_frames(runner=...). The frozen module still verifies
    observed PTS against the probed timebase, source hash and PNG bytes.
    """
    if not isinstance(args, (tuple, list)):
        raise ValueError("invalid_frame_command")
    cmd = list(args)
    if "-vf" not in cmd or "-frames:v" not in cmd:
        raise ValueError("not_a_frame_sampling_command")
    vf_index = cmd.index("-vf") + 1
    vf = cmd[vf_index]
    if (not isinstance(vf, str) or "select=" not in vf
            or vf.count(",showinfo,") != 1):
        raise ValueError("unexpected_frame_sampling_filter")
    # trim=end_frame=1 is *before* showinfo. Otherwise an encoder receiving
    # one frame can still have multiple earlier decoded showinfo records.
    cmd[vf_index] = vf.replace(
        ",showinfo,", ",trim=end_frame=1,showinfo,", 1)
    # Some Windows builds buffer frames across filter threads, and some PNG
    # encoders fail when initialized with automatic threading.
    cmd[cmd.index("-vf"):cmd.index("-vf")] = ["-filter_threads", "1"]
    output_pos = cmd.index("pipe:1")
    cmd[output_pos:output_pos] = ["-threads:v", "1"]

    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0 and "-fps_mode" in cmd:
        # Older FFmpeg builds do not support -fps_mode passthrough.
        fallback = list(cmd)
        i = fallback.index("-fps_mode")
        del fallback[i:i + 2]
        result = subprocess.run(fallback, **kwargs)
    return result
