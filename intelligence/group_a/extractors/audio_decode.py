"""GA-MOD-09: local-only, bounded FFprobe + FFmpeg audio decoding.

A verified D1 video is mandatory. Audio stream absence, decoded digital
silence, broken audio and unobserved speech are different truth claims.
PCM bytes are memory-only: no source media or PCM is sent to a provider.
Time coordinates remain audio-stream-local until GA10/11 aligns streams.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from ..contracts_v1 import canonical_sha256
from ..media_manifest import VerifiedFile, open_verified

AUDIO_SCHEMA = "ga-audio-decode/v1"
AUDIO_METHOD = "group-a-audio-ffmpeg-v1"
SAMPLE_RATE = 16000
CHANNELS = 1
BITS_PER_SAMPLE = 16
MAX_AUDIO_SECONDS = 600
MAX_PCM_BYTES = MAX_AUDIO_SECONDS * SAMPLE_RATE * 2 + 32000
MAX_PROBE_BYTES = 2_000_000
MAX_LOG_BYTES = 2_000_000
PROBE_TIMEOUT = 30
DECODE_TIMEOUT = 210
SUPPORTED_AUDIO_CODECS = frozenset((
    "aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_s24le",
    "pcm_f32le", "alac", "ac3", "eac3", "wmav2",
))


class AudioDecodeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Group A audio decoding failed: " + code)


@dataclass(frozen=True, slots=True)
class AudioProbe:
    state: str                    # present or absent, NEVER inferred from silence
    stream_index: int | None
    codec_name: str | None
    source_channels: int | None
    source_sample_rate: int | None
    start_time_sec: str | None
    source_time_base: str | None
    duration_sec: str | None

    def __post_init__(self):
        if self.state not in ("present", "absent"):
            raise AudioDecodeError("invalid_audio_probe")
        if self.state == "absent" and any(x is not None for x in (
            self.stream_index, self.codec_name, self.source_channels,
            self.source_sample_rate, self.start_time_sec,
            self.source_time_base, self.duration_sec,
        )):
            raise AudioDecodeError("invalid_audio_probe")


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    probe: AudioProbe
    pcm: bytes                         # NEVER in public wire
    source_sha256: str
    sample_rate: int = SAMPLE_RATE
    channel_count: int = CHANNELS
    method_version: str = AUDIO_METHOD

    def __post_init__(self):
        if not isinstance(self.probe, AudioProbe) or type(self.pcm) is not bytes:
            raise AudioDecodeError("invalid_decoded_audio")
        if len(self.pcm) > MAX_PCM_BYTES or len(self.pcm) % 2:
            raise AudioDecodeError("invalid_pcm_size")
        if self.probe.state == "absent" and self.pcm:
            raise AudioDecodeError("absent_audio_has_pcm")
        if type(self.source_sha256) is not str or not re.fullmatch(r"[a-f0-9]{64}", self.source_sha256):
            raise AudioDecodeError("invalid_media_sha")

    @property
    def sample_count(self):
        return len(self.pcm) // 2

    @property
    def duration_ms(self):
        return self.sample_count * 1000 // SAMPLE_RATE

    def wire(self):
        if self.probe.state == "absent":
            status, reason, digital_silence = "absent", None, None
        else:
            samples = pcm_int16(self.pcm)
            peak = max((abs(x) for x in samples), default=0)
            digital_silence = peak == 0
            status, reason = ("silent" if digital_silence else "decoded"), None
        fields = {
            "schema_version": AUDIO_SCHEMA,
            "method_version": self.method_version,
            "source_media_sha256": self.source_sha256,
            "audio_status": status,
            "verified_audio_stream_absence": self.probe.state == "absent",
            "digital_silence": digital_silence,
            "speech_status": "not_run",
            "source_stream_index": self.probe.stream_index,
            "source_codec": self.probe.codec_name,
            "source_channels": self.probe.source_channels,
            "source_sample_rate": self.probe.source_sample_rate,
            "source_time_base": self.probe.source_time_base,
            "source_start_sec": self.probe.start_time_sec,
            "time_origin": "audio_stream_local_zero",
            "output_channels": CHANNELS,
            "output_sample_rate": SAMPLE_RATE,
            "output_sample_format": "s16le",
            "decoded_samples": self.sample_count,
            "decoded_duration_ms": self.duration_ms,
            "pcm_sha256": sha256(self.pcm).hexdigest() if self.pcm else None,
            "error_code": reason,
        }
        return {**fields, "observation_sha256": canonical_sha256(fields)}


def pcm_int16(pcm):
    if type(pcm) is not bytes or len(pcm) % 2:
        raise AudioDecodeError("invalid_pcm_size")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _positive_dec(value, *, allow_zero=False, allow_negative=False, max_value=600):
    if value in (None, "N/A"):
        return None
    if type(value) not in (str, int, Decimal) or type(value) is bool:
        raise AudioDecodeError("invalid_audio_time")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or (number < 0 and not allow_negative) or (
            not allow_zero and number == 0
        ) or abs(number) > max_value:
            raise ValueError()
        return format(number.quantize(Decimal("0.001")), ".3f")
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise AudioDecodeError("invalid_audio_time") from exc


def parse_audio_probe(raw, *, expected_video=True, chosen_audio_stream_index=None):
    try:
        if chosen_audio_stream_index is not None and (type(chosen_audio_stream_index) is not int or not 0 <= chosen_audio_stream_index <= 1024):
            raise AudioDecodeError("invalid_audio_stream_choice")
        if type(raw) is not str or len(raw.encode("utf-8")) > MAX_PROBE_BYTES:
            raise ValueError()
        parsed = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(parsed) is not dict or type(parsed.get("streams")) is not list:
            raise ValueError()
        streams = parsed["streams"]
        if expected_video and not any(type(x) is dict and x.get("codec_type") == "video"
                                      for x in streams):
            raise AudioDecodeError("not_a_video_container")
        audios = [x for x in streams if type(x) is dict and x.get("codec_type") == "audio"]
        if not audios:
            return AudioProbe("absent", None, None, None, None, None, None, None)
        # Multiple audio streams require an explicit choice. Language must
        # not be silently guessed from stream order or metadata.
        if chosen_audio_stream_index is None:
            if len(audios) != 1:
                raise AudioDecodeError("multiple_audio_streams")
            s=audios[0]
        else:
            selected=[x for x in audios if x.get("index")==chosen_audio_stream_index]
            if len(selected)!=1:
                raise AudioDecodeError("audio_stream_not_found")
            s=selected[0]
        idx, codec = s.get("index"), s.get("codec_name")
        channels, rate = s.get("channels"), s.get("sample_rate")
        base = s.get("time_base")
        if (type(idx) is not int or not 0 <= idx <= 1024 or
            type(codec) is not str or codec not in SUPPORTED_AUDIO_CODECS or
            type(channels) is not int or not 1 <= channels <= 8 or
            type(rate) not in (str, int) or
            not re.fullmatch(r"[1-9][0-9]{2,5}", str(rate)) or
            not 8000 <= int(rate) <= 192000 or
            type(base) is not str or not re.fullmatch(r"[1-9][0-9]{0,11}/[1-9][0-9]{0,11}", base)):
            raise AudioDecodeError("unsupported_audio_stream")
        start = _positive_dec(s.get("start_time"), allow_zero=True,
                              allow_negative=True, max_value=86400)
        length = _positive_dec(s.get("duration"))
        return AudioProbe("present", idx, codec, channels, int(rate), start, base, length)
    except AudioDecodeError:
        raise
    except (ValueError, TypeError, UnicodeError, KeyError, OverflowError) as exc:
        raise AudioDecodeError("invalid_audio_probe") from exc


def _bin(name, explicit):
    path = explicit if explicit is not None else shutil.which(name)
    if type(path) is not str or not Path(path).is_file():
        raise AudioDecodeError("missing_" + name)
    return str(Path(path).resolve())


def _call(args, *, runner, timeout):
    try:
        result = runner(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioDecodeError("audio_tool_unavailable") from exc
    if (type(result.returncode) is not int or result.returncode != 0):
        raise AudioDecodeError("audio_tool_failed")
    if (type(result.stdout) is not bytes or type(result.stderr) is not bytes or
        len(result.stdout) > MAX_PCM_BYTES or len(result.stderr) > MAX_LOG_BYTES):
        raise AudioDecodeError("audio_tool_output_limit")
    return result


def decode_audio(media: VerifiedFile, *, roots, runner=subprocess.run,
                 ffprobe=None, ffmpeg=None, audio_stream_index=None):
    """Decode from GA05 verified source FD, never URL or unchecked pathname.

    A confirmed no-audio result does NOT claim no speech/no music by
    acoustic analysis: it is a stream-absence fact. A present empty/broken
    decoder output is unavailable, not silent.
    """
    if not isinstance(media, VerifiedFile) or media.media_kind != "video":
        raise AudioDecodeError("verified_video_required")
    probe_bin = _bin("ffprobe", ffprobe)
    ffmpeg_bin = _bin("ffmpeg", ffmpeg)
    if audio_stream_index is not None and (
        type(audio_stream_index) is not int or not 0 <= audio_stream_index <= 1024
    ):
        raise AudioDecodeError("invalid_audio_stream_choice")
    with open_verified(media, roots=roots) as stream:
        # Verified fd is pinned and revalidated after each decode operation.
        use_fd = sys.platform.startswith("linux") and Path("/proc/self/fd").is_dir()
        fd = stream.fileno()
        source = f"/proc/self/fd/{fd}" if use_fd else media.path
        # Custom injectable runners receive pass_fds when on Linux just as
        # the real subprocess does.
        extra = (fd,) if use_fd else ()
        def run(args, **kwargs):
            if extra:
                kwargs["pass_fds"] = extra
            return runner(args, **kwargs)
        probe = _call([
            probe_bin, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "stream=index,codec_type,codec_name,channels,sample_rate,time_base,start_time,duration:format=duration",
            "-of", "json", source,
        ], runner=run, timeout=PROBE_TIMEOUT)
        metadata = parse_audio_probe(probe.stdout.decode("utf-8", "strict"),
                                     chosen_audio_stream_index=audio_stream_index)
        if metadata.state == "absent":
            if audio_stream_index is not None:
                raise AudioDecodeError("audio_stream_not_found")
            return DecodedAudio(metadata, b"", media.sha256)
        if audio_stream_index is not None and audio_stream_index != metadata.stream_index:
            raise AudioDecodeError("audio_stream_not_found")
        # pcm_s16le output is deterministic per input, FFmpeg build and config.
        # -vn / -sn / -dn forbid extracting video or subtitle data.
        output = _call([
            ffmpeg_bin, "-nostdin", "-hide_banner", "-v", "error",
            "-xerror", "-err_detect", "explode",
            "-protocol_whitelist", "file,pipe", "-i", source,
            "-map", f"0:{metadata.stream_index}", "-vn", "-sn", "-dn",
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
        ], runner=run, timeout=DECODE_TIMEOUT)
        if not output.stdout or len(output.stdout) % 2:
            raise AudioDecodeError("audio_pcm_unobservable")
        samples = len(output.stdout) // 2
        if samples > MAX_AUDIO_SECONDS * SAMPLE_RATE + SAMPLE_RATE:
            raise AudioDecodeError("audio_duration_limit")
        return DecodedAudio(metadata, output.stdout, media.sha256)
