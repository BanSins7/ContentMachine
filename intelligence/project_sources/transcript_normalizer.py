"""PSI Stage 07. Convert verified downloader sidecars / ASR segments to Transcript JSON v1.

No timestamps are fabricated. Text-only sources are deliberately not fused into
untimed speech. Does not infer languages or confidence from the text.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable

VERSION = 'transcript-normalizer/1.0.0'
MAX_SIDECAR = 16 * 1024 * 1024
MAX_SEGMENTS = 12_000
MAX_TEXT = 120_000
MAX_SEGMENT_TEXT = 1400
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_TIME = re.compile(r'^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})(?:\s+.*)?$')
_TAG = re.compile(r'<[^>]{0,120}>')


class TranscriptProblem(Exception):
    def __init__(self, message: str, code: str = 'invalid_transcript', http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def decode_text(data: bytes) -> tuple[str, str]:
    if not isinstance(data, bytes) or len(data) > MAX_SIDECAR:
        raise TranscriptProblem('Превышен размер субтитров.', 'sidecar_size')
    if b'\x00' in data[:200]:
        # UTF16 is not used by legacy workers; don't silently strip binary bytes.
        raise TranscriptProblem('Текст содержит NUL / бинарные данные.', 'invalid_encoding')
    for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            text = data.decode(encoding)
            if any(ord(c) < 9 or 13 < ord(c) < 32 for c in text):
                raise TranscriptProblem('Субтитры содержат управляющие символы.', 'invalid_encoding')
            return text, encoding
        except UnicodeDecodeError:
            continue
    raise TranscriptProblem('Не поддерживается кодировка субтитров.', 'invalid_encoding')


def language(value) -> str:
    """Unknown is explicit; auto is not a detected language."""
    if not isinstance(value, str):
        return 'unknown'
    value = value.strip().lower().replace('_', '-')
    if value in ('', 'auto', 'unknown', 'und', 'none', 'multilingual'):
        return 'unknown'
    if re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,2}', value):
        return value
    return 'unknown'


def _milliseconds(h, m, s, frac):
    if int(m) >= 60 or int(s) >= 60:
        raise ValueError('timestamp minutes/seconds out of range')
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, '0'))


def _validate_rows(rows: Iterable, *, duration_ms: int | None = None):
    out, warnings = [], []
    previous_end = 0
    for i, raw in enumerate(rows):
        if i >= MAX_SEGMENTS:
            warnings.append('segment_limit')
            break
        try:
            start, end, text, lang, source, confidence = raw
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError('bad_interval')
            if start < previous_end:
                raise ValueError('overlap_or_nonmonotonic')
            if duration_ms is not None and end > duration_ms + 500:
                raise ValueError('beyond_duration')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('empty_text')
            text = text.strip()
            if len(text) > MAX_SEGMENT_TEXT:
                raise ValueError('segment_text_limit')
            if source not in ('srt', 'asr'):
                raise ValueError('invalid_source')
            segment = {'start_ms': start, 'end_ms': end, 'text': text,
                       'language': language(lang), 'source': source}
            if confidence is not None:
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError('invalid_confidence')
                segment['confidence'] = round(float(confidence), 5)
            out.append(segment)
            previous_end = end
        except (TypeError, ValueError) as exc:
            code = str(exc)
            if code not in ('bad_interval','overlap_or_nonmonotonic','beyond_duration','empty_text',
                            'segment_text_limit','invalid_source','invalid_confidence'):
                code = 'invalid_row'
            if code not in warnings:
                warnings.append(code)
    return out, warnings


def parse_srt(data: bytes, *, lang='unknown', duration_ms=None):
    """Return (valid cues, warnings, raw text, encoding). Bad cues aren't assigned fake times."""
    raw, encoding = decode_text(data)
    text = raw.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff')
    blocks = [block.strip() for block in re.split(r'\n\s*\n', text) if block.strip()]
    rows, warnings = [], []
    for block in blocks:
        lines = block.split('\n')
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2:
            if 'invalid_cue' not in warnings: warnings.append('invalid_cue')
            continue
        found = _TIME.fullmatch(lines[0])
        if not found:
            if 'invalid_cue' not in warnings: warnings.append('invalid_cue')
            continue
        try:
            numbers=found.groups()
            start = _milliseconds(*numbers[:4]); end = _milliseconds(*numbers[4:])
        except ValueError:
            if 'invalid_timestamp' not in warnings: warnings.append('invalid_timestamp')
            continue
        cue = '\n'.join(x.strip() for x in lines[1:] if x.strip())
        cue = _TAG.sub('', cue).strip()
        rows.append((start, end, cue, lang, 'srt', None))
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]
        warnings.append('source_text_truncated')
    cues, rejected = _validate_rows(rows, duration_ms=duration_ms)
    for item in rejected:
        if item not in warnings: warnings.append(item)
    return cues, warnings, text, encoding


def _duration_ms(metadata: dict) -> int | None:
    duration = metadata.get('duration') or metadata.get('duration_seconds')
    if duration is None:
        return None
    if isinstance(duration, bool) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration <= 0 or duration > 60*60*24:
        return None
    return round(float(duration) * 1000)


def _source_language(metadata):
    for key in ('language','subtitle_language','detected_language'):
        value=language(metadata.get(key))
        if value!='unknown':return value
    return 'unknown'


def _speech_indicator(metadata):
    for key in ('subtitle_status','subtitle_source','speech_status'):
        val=metadata.get(key)
        if isinstance(val,str):
            val=val.strip().lower()
            if val in ('no_speech','no-speech'):
                return 'no_speech'
            if val in ('error','asr_failed','failed','unavailable'):
                return 'asr_failed'
            if val in ('ok','completed','success','local_whisper','model'):
                return 'has_speech'
    return 'unknown'


def normalize(manifest: dict, *, srt: bytes | None = None, txt: bytes | None = None,
              asr_segments: list | None = None, detected_language=None, model_version=None):
    """Never changes manifest. All provided bytes were checked against its immutable hashes."""
    if not isinstance(manifest,dict) or not re.fullmatch('[a-f0-9]{64}',str(manifest.get('video_sha256',''))):
        raise TranscriptProblem('Отсутствует подтверждённый SHA-256 MP4.', 'unverified_video')
    meta=manifest.get('metadata')
    if not isinstance(meta,dict):
        raise TranscriptProblem('Нет метаданных подтверждённого видео.', 'missing_metadata')
    if srt is not None and asr_segments is not None:
        raise TranscriptProblem('Одновременно SRT и ASR сегменты не допускаются.', 'invalid_mode')
    duration = _duration_ms(meta)
    detected = language(detected_language) if detected_language else _source_language(meta)
    warnings=[]
    raw_txt=None
    if txt is not None:
        raw_txt, _ = decode_text(txt)
        if len(raw_txt)>MAX_TEXT:
            raw_txt=raw_txt[:MAX_TEXT];warnings.append('transcript_text_truncated')
    if asr_segments is not None:
        parsed=[]
        if type(asr_segments) is not list or len(asr_segments)>MAX_SEGMENTS:
            raise TranscriptProblem('Некорректные ASR сегменты.', 'invalid_asr')
        for row in asr_segments:
            if not isinstance(row,dict):
                parsed.append((None,None,'',detected,'asr',None));continue
            parsed.append((row.get('start_ms'),row.get('end_ms'),row.get('text'),
                           row.get('language',detected),'asr',row.get('confidence')))
        segments,invalid=_validate_rows(parsed,duration_ms=duration)
        warnings.extend(invalid)
        engine='local_whisper'
        model=str(meta.get('model') or 'faster-whisper')[:100]
        version=str(model_version or 'local')[:100]
        source='asr'
        raw_srt=None
    elif srt is not None:
        segments, invalid, raw_srt, _ = parse_srt(srt,lang=detected,duration_ms=duration)
        warnings.extend(invalid)
        engine=str(meta.get('subtitle_engine') or meta.get('subtitle_source') or 'legacy_srt')[:100]
        model=str(meta.get('subtitle_model') or meta.get('model') or 'unspecified')[:100]
        version=str(meta.get('model_version') or 'unknown')[:100]
        source='srt'
    else:
        segments=[];raw_srt=None;source='none';engine=str(meta.get('subtitle_engine') or 'unknown')[:100]
        model=str(meta.get('subtitle_model') or 'unknown')[:100]
        version=str(meta.get('model_version') or 'unknown')[:100]
    speech=_speech_indicator(meta) if asr_segments is None else 'unknown'
    if segments:
        speech_status='speech_detected'
        processing='partial' if warnings else 'completed'
        if speech in ('no_speech','asr_failed'):
            warnings.append('metadata_conflicts_speech');processing='partial'
    elif speech=='no_speech' and not (raw_txt and raw_txt.strip()) and not (raw_srt and raw_srt.strip()):
        speech_status='no_speech';processing='completed'
    elif speech=='asr_failed':
        speech_status='asr_error';processing='unavailable'
    else:
        speech_status='unknown';processing='unavailable'
        if raw_txt and raw_txt.strip():warnings.append('untimed_text_only')
        elif raw_srt and raw_srt.strip():warnings.append('no_valid_timed_cues')
        else:warnings.append('no_transcript_data')
    if raw_txt and raw_txt.strip() and segments:
        cleaned = re.sub(r'\s+',' ',raw_txt).strip()
        cues = re.sub(r'\s+',' ', ' '.join(s['text'] for s in segments)).strip()
        if cleaned != cues:
            warnings.append('srt_txt_mismatch')
            processing='partial'
    languages=sorted({s['language'] for s in segments}) or ([detected] if detected!='unknown' else [])
    timed=sum(row['end_ms']-row['start_ms'] for row in segments)
    first=segments[0]['start_ms'] if segments else None
    last=segments[-1]['end_ms'] if segments else None
    coverage=round(min(1,timed/duration),6) if duration is not None else None
    output={'schema_version':'transcript/v1','normalizer_version':VERSION,
            'video_sha256':manifest['video_sha256'],
            'source':source,'platform':manifest.get('platform'), 'native_id':manifest.get('native_id'),
            'engine':engine,'model':model,'model_version':version,
            'speech_status':speech_status,'processing_status':processing,
            'detected_language':detected,'languages':languages,
            'timing_coverage':{'duration_ms':duration,'first_start_ms':first,'last_end_ms':last,
                               'speech_timed_ms':timed,'fraction_of_video':coverage},
            'source_text':{'srt':raw_srt,'txt':raw_txt},'segments':segments,
            'warnings':list(dict.fromkeys(warnings)), 'segment_count':len(segments)}
    if len(canonical(output))>MAX_OUTPUT_BYTES:
        raise TranscriptProblem('Нормализованный транскрипт превышает 8 МиБ.', 'output_limit')
    return output
