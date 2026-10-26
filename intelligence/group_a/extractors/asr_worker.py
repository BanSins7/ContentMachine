"""Child-process entrypoint for *installed local* faster-whisper only.

No media URL, model repository ID or network acquisition is accepted. Parent
ensures external lease/timeouts and all temp files are deleted at exit.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .asr import ASRError, _second, _text, _language
from .local_asr import _model_inventory, MAX_JSON


def _collect(model_dir, wavfile, output_path, settings, digest, version):
    # All file locations are absolute local; parent has independently pinned
    # the model inventory. Verify the same inventory in this child.
    model_dir=Path(model_dir)
    source=Path(wavfile)
    destination=Path(output_path)
    if (not model_dir.is_absolute() or not source.is_absolute()
        or not destination.is_absolute() or not source.is_file()
        or source.is_symlink() or destination.exists()):
        raise ASRError("asr_worker_paths_invalid")
    if _model_inventory(model_dir)!=digest:
        raise ASRError("asr_model_digest_mismatch")
    if type(settings) is not dict or set(settings)!={
        "language","device","compute_type","beam_size","word_timestamps"
    }:
        raise ASRError("invalid_asr_config")
    if (settings["language"] not in ("auto","ru","en") or
        settings["device"] not in ("cpu","cuda") or
        settings["compute_type"] not in ("int8","float16","float32") or
        type(settings["beam_size"]) is not int or not 1<=settings["beam_size"]<=8 or
        type(settings["word_timestamps"]) is not bool):
        raise ASRError("invalid_asr_config")
    # Delayed import intentionally keeps the pure GA contracts usable without
    # heavyweight ASR libraries and prevents implicit HF model downloads.
    from faster_whisper import WhisperModel
    import importlib.metadata
    engine_version=importlib.metadata.version("faster-whisper")
    model=WhisperModel(str(model_dir),device=settings["device"],
        compute_type=settings["compute_type"],local_files_only=True,
        num_workers=1,cpu_threads=2)
    language=None if settings["language"]=="auto" else settings["language"]
    segments,info=model.transcribe(str(source),beam_size=settings["beam_size"],
        language=language,word_timestamps=settings["word_timestamps"],
        vad_filter=False,condition_on_previous_text=False)
    detected=_language(getattr(info,"language",None))
    rows=[]
    for segment in segments:
        if len(rows)>=12000:
            raise ASRError("too_many_asr_segments")
        if not (getattr(segment,"text",None) and getattr(segment,"start",None) is not None
                and getattr(segment,"end",None) is not None):
            raise ASRError("invalid_local_asr_segment")
        a=_second(segment.start)
        b=_second(segment.end)
        words=getattr(segment,"words",None) if settings["word_timestamps"] else None
        word_rows=None
        if words is not None:
            word_rows=[]
            for word in words:
                if len(word_rows)>50000:
                    raise ASRError("too_many_asr_words")
                word_rows.append({
                    "start_sec":_second(word.start),
                    "end_sec":_second(word.end),
                    "text":_text(word.word),
                    "confidence":float(word.probability) if word.probability is not None else None,
                })
        rows.append({"start_sec":a,"end_sec":b,"text":_text(segment.text),
                     "words":word_rows,"confidence":None})
    payload={"language":detected,"segments":rows,
             "model_sha256":digest,"model_version":version,
             "engine_version":engine_version}
    blob=json.dumps(payload,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
    if not 0<len(blob)<=MAX_JSON:
        raise ASRError("asr_worker_output_limit")
    destination.write_bytes(blob)


def main(args=None):
    argv=sys.argv[1:] if args is None else args
    if len(argv)!=6:
        return 2
    try:
        _collect(argv[0],argv[1],argv[2],json.loads(argv[3]),argv[4],argv[5])
        return 0
    except Exception:
        # No source transcript, local media path or provider token leaks.
        return 1


if __name__=="__main__":
    raise SystemExit(main())
