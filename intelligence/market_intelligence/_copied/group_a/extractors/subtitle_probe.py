"""GA-MOD-14 read-only local FFprobe subtitle packet audit.

Evidence of an actual embedded subtitle *stream with packets* is stronger
than stream metadata alone. An unavailable probe never means absence.
Only accepts an already GA05 SHA-verified VIDEO file and a local binary;
no URL, subtitles downloads, side effects or database writes.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from ..contracts_v1 import canonical_sha256
from ..media_manifest import VerifiedFile, open_verified
from .subtitles import SUBTITLE_CODECS, SubtitleError

SCHEMA="ga-subtitle-stream-probe/v1"
METHOD="group-a-ffprobe-subtitle-packets-v1"
MAX_OUTPUT=1048576
TIMEOUT_SEC=45


def parse_subtitle_probe(raw,source_sha):
    if type(source_sha) is not str or not re.fullmatch(r"[a-f0-9]{64}",source_sha):
        raise SubtitleError("invalid_source_sha")
    try:
        if type(raw) is not bytes or len(raw)>MAX_OUTPUT:
            raise SubtitleError("subtitle_probe_output_invalid")
        body=json.loads(raw.decode("utf-8","strict"),
                        parse_constant=lambda x:(_ for _ in ()).throw(ValueError()))
        if type(body) is not dict or type(body.get("streams")) is not list or len(body["streams"])>64:
            raise SubtitleError("subtitle_probe_output_invalid")
        rows=[];seen=set()
        for item in body["streams"]:
            if type(item) is not dict or item.get("codec_type")!="subtitle":
                raise SubtitleError("subtitle_probe_output_invalid")
            index=item.get("index")
            codec=item.get("codec_name")
            if (type(index) is not int or index<0 or index>1024 or index in seen
                or type(codec) is not str or codec not in SUBTITLE_CODECS):
                raise SubtitleError("subtitle_codec_unsupported")
            seen.add(index)
            n=item.get("nb_read_packets")
            if n in (None,"N/A"):
                count=None
            else:
                if type(n) is not str or re.fullmatch(r"[0-9]{1,8}",n) is None:
                    raise SubtitleError("subtitle_packet_count_invalid")
                count=int(n)
                if count>10000000:
                    raise SubtitleError("subtitle_packet_count_invalid")
            tags=item.get("tags",{})
            if type(tags) is not dict:
                raise SubtitleError("subtitle_language_invalid")
            language=tags.get("language")
            if language is not None and (
                type(language) is not str or
                re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z]{2,8})?",language) is None):
                language=None  # do not invent language if codec reports "und"
            rows.append({"stream_index":index,"codec_name":codec,
                         "packet_count":count,"language":language})
    except SubtitleError:
        raise
    except (ValueError,TypeError,UnicodeError,KeyError,OverflowError) as exc:
        raise SubtitleError("subtitle_probe_output_invalid") from exc
    core={"schema_version":SCHEMA,"method_version":METHOD,
          "source_media_sha256":source_sha,"status":"inspected",
          "error_code":None,"tracks":rows}
    return {**core,"observation_sha256":canonical_sha256(core)}


def _unavailable(media,reason):
    core={"schema_version":SCHEMA,"method_version":METHOD,
          "source_media_sha256":media.sha256,"status":"unavailable",
          "error_code":reason,"tracks":[]}
    return {**core,"observation_sha256":canonical_sha256(core)}


def probe_subtitle_tracks(media:VerifiedFile,*,roots,ffprobe=None,
                          runner=subprocess.run):
    """Count local embedded subtitle packets; failures yield unavailable."""
    if not isinstance(media,VerifiedFile) or media.media_kind!="video":
        raise SubtitleError("verified_video_required")
    if not callable(runner):
        raise SubtitleError("invalid_subtitle_runner")
    executable=ffprobe if ffprobe is not None else shutil.which("ffprobe")
    if type(executable) is not str or not Path(executable).is_file():
        return _unavailable(media,"ffprobe_unavailable")
    with open_verified(media,roots=roots) as stream:
        fd=stream.fileno()
        linux=sys.platform.startswith("linux") and Path("/proc/self/fd").is_dir()
        src=f"/proc/self/fd/{fd}" if linux else media.path
        opts={"stdin":subprocess.DEVNULL,"stdout":subprocess.PIPE,
              "stderr":subprocess.PIPE,"check":False,"timeout":TIMEOUT_SEC}
        if linux:opts["pass_fds"]=(fd,)
        args=[str(Path(executable).resolve()),"-v","error",
              "-protocol_whitelist","file,pipe",
              "-select_streams","s","-count_packets",
              "-show_entries","stream=index,codec_type,codec_name,nb_read_packets:stream_tags=language",
              "-of","json",src]
        try:
            result=runner(args,**opts)
        except (OSError,subprocess.TimeoutExpired):
            return _unavailable(media,"ffprobe_failed")
        if (type(result.returncode) is not int or result.returncode!=0
            or type(result.stdout) is not bytes or type(result.stderr) is not bytes):
            return _unavailable(media,"ffprobe_failed")
        if len(result.stdout)>MAX_OUTPUT or len(result.stderr)>MAX_OUTPUT:
            return _unavailable(media,"ffprobe_output_limit")
        try:
            return parse_subtitle_probe(result.stdout,media.sha256)
        except SubtitleError:
            return _unavailable(media,"ffprobe_output_invalid")
