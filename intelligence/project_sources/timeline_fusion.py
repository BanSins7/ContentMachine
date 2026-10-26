"""Deterministic PSI Stage 09 fusion of pinned audio/vision/OCR outputs.

Pure code, zero calls to FFmpeg, OCR, ASR, vision model, or platform APIs.
Frame samples are point evidence, NOT observations spanning a whole scene.
All semantic classifications are explicitly unreviewed candidates, not Group A/B.
"""
from __future__ import annotations

import hashlib
import json
import re

VERSION = 'video-intelligence/v1'
ENGINE_VERSION = 'timeline-fusion/1.0.0'
MAX_EVENTS = 18_000
MAX_JSON = 12 * 1024 * 1024
MAX_EVIDENCE_TEXT = 2000


class FusionProblem(Exception):
    def __init__(self, message, code='invalid_fusion', http_status=422):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _time(a, b, duration):
    return (type(a) is int and type(b) is int and 0 <= a < b
            and (duration is None or b <= duration))


def _expect_hash(obj, label):
    if not isinstance(obj, dict) or not re.fullmatch('[0-9a-f]{64}', str(obj.get('video_sha256',''))):
        raise FusionProblem('Отсутствует SHA-256: '+label, 'missing_video_hash')
    return obj['video_sha256']


def _ref(kind, job_id, suffix):
    return f'{kind}:{job_id}:{suffix}'


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= MAX_EVIDENCE_TEXT


def _scene_at(scenes, ms):
    for scene in scenes:
        if scene['start_ms'] <= ms < scene['end_ms']:
            return scene['scene_id']
    return None


def fuse(*, acquisition: dict, project_id: str, source_id: str, source_version_id: str,
         acquisition_job_id: str, transcript: dict | None = None,
         transcript_job_id: str | None = None, prepared: dict | None = None,
         prepare_job_id: str | None = None, observations: dict | None = None,
         vision_job_id: str | None = None, input_provenance: dict | None = None,
         cost_summary: dict | None = None) -> dict:
    """Make a self-contained snapshot from exact immutable records supplied by repository.

    `input_provenance` contains only SQL ids/digests of immutable outputs, not raw
    local file paths. Even valid model IDs never become verified visual evidence.
    """
    video_hash = _expect_hash(acquisition, 'acquisition')
    if not acquisition.get('acquisition_verified') and acquisition.get('trust') != 'verified_local_asset':
        raise FusionProblem('Видео не подтверждено этапом 06.', 'unverified_asset')
    if not re.fullmatch('[0-9a-f]{64}', video_hash):
        raise FusionProblem('Некорректный SHA видео.', 'invalid_hash')
    if not transcript and not prepared:
        raise FusionProblem('Нет ни транскрипта, ни визуальной подготовки.', 'missing_modalities')
    if bool(transcript) != bool(transcript_job_id) or bool(prepared) != bool(prepare_job_id):
        raise FusionProblem('Не указан pinned input job.', 'unpinned_input')
    if bool(observations) != bool(vision_job_id) or (observations is not None and prepared is None):
        raise FusionProblem('Vision AI требует подготовленные кадры.', 'missing_preparation')

    duration = None
    if prepared is not None:
        if prepared.get('schema_version') != 'vision-prepare/v1' or _expect_hash(prepared, 'prepare') != video_hash:
            raise FusionProblem('Подготовка принадлежит другому видео.', 'vision_video_mismatch')
        if prepared.get('video_source_version_id') != source_version_id or prepared.get('acquisition_job_id') != acquisition_job_id:
            raise FusionProblem('Vision подготовка имеет другую версию MP4.', 'mismatched_source_version')
        duration = prepared.get('video_duration_ms')
        if type(duration) is not int or not 1 <= duration <= 86_400_000:
            raise FusionProblem('Некорректная длительность.', 'bad_duration')
    speech = []
    speech_status='unavailable'
    if transcript is not None:
        if transcript.get('schema_version') != 'transcript/v1' or _expect_hash(transcript, 'transcript') != video_hash:
            raise FusionProblem('Транскрипт другого видео.', 'transcript_video_mismatch')
        speech_status=transcript.get('speech_status')
        if speech_status not in ('speech_detected','no_speech','asr_error','unknown'):
            raise FusionProblem('Некорректный статус речи.', 'invalid_speech_status')
        if transcript.get('processing_status') not in ('completed','partial','unavailable'):
            raise FusionProblem('Некорректный статус транскрипта.', 'invalid_transcript_status')
        timing=transcript.get('timing_coverage')
        if not isinstance(timing,dict):
            raise FusionProblem('Нет coverage транскрипта.', 'invalid_transcript')
        audio_d=timing.get('duration_ms')
        if audio_d is not None:
            if type(audio_d) is not int or audio_d<=0:
                raise FusionProblem('Неверная длительность ASR.', 'invalid_transcript')
            if duration is not None and abs(audio_d-duration)>max(1000,round(duration*.02)):
                raise FusionProblem('ASR и видео имеют разную длительность.', 'duration_mismatch')
            if duration is None:duration=audio_d
        values=transcript.get('segments')
        if not isinstance(values,list) or len(values)>12000:
            raise FusionProblem('Некорректные сегменты ASR.', 'invalid_transcript')
        last=0
        for i,s in enumerate(values):
            if not isinstance(s,dict) or not _time(s.get('start_ms'),s.get('end_ms'),duration) or s['start_ms']<last or not _text(s.get('text')):
                raise FusionProblem('Некорректные или пересекающиеся таймкоды ASR.', 'invalid_transcript_time')
            if s.get('source') not in ('srt','asr') or not isinstance(s.get('language'),str):
                raise FusionProblem('Некорректное происхождение речи.', 'invalid_transcript')
            last=s['end_ms']
            speech.append({**s,'evidence_id':_ref('speech',transcript_job_id,f'seg{i+1:05d}')})
        if speech_status=='no_speech' and speech:
            raise FusionProblem('Противоречие no_speech и сегментов.', 'speech_conflict')
    if duration is None:
        raise FusionProblem('Длительность неизвестна; синхронизация невозможна.', 'duration_unknown',409)

    scenes=[];frames=[];ocr=[];visual=[];scene_index={};frame_index={}
    warnings=[]
    if prepared is not None:
        raw_scenes=prepared.get('scenes')
        raw_frames=prepared.get('frames')
        if not isinstance(raw_scenes,list) or not isinstance(raw_frames,list) or len(raw_frames)>8 or len(raw_scenes)>110:
            raise FusionProblem('Повреждён Vision manifest.', 'invalid_vision_manifest')
        prev=-1
        for s in raw_scenes:
            if not isinstance(s,dict) or not _time(s.get('start_ms'),s.get('end_ms'),duration) or s['start_ms']<prev or not re.fullmatch(r'sc\d{3}',str(s.get('scene_id'))):
                raise FusionProblem('Неверные сцены.', 'invalid_scene')
            if s['scene_id'] in scene_index:raise FusionProblem('Повтор scene ID.', 'duplicate_scene')
            prev=s['end_ms'];scene_index[s['scene_id']]=s
            scenes.append({'scene_id':s['scene_id'],'start_ms':s['start_ms'],'end_ms':s['end_ms'],
                           'boundary_kind':s.get('boundary_kind'),'frame_ids':s.get('frame_ids',[]),
                           'evidence_id':_ref('scene',prepare_job_id,s['scene_id'])})
        used_times=set()
        for f in raw_frames:
            if not isinstance(f,dict) or not re.fullmatch(r'f\d{3}',str(f.get('frame_id'))) or f['frame_id'] in frame_index:
                raise FusionProblem('Неверный frame ID.', 'invalid_frame')
            t=f.get('time_ms');sc=f.get('scene_id')
            if type(t) is not int or not 0<=t<duration or t in used_times or sc not in scene_index or not scene_index[sc]['start_ms']<=t<scene_index[sc]['end_ms'] or f['frame_id'] not in scene_index[sc].get('frame_ids',[]):
                raise FusionProblem('Кадр не соответствует таймлайну.', 'invalid_frame_time')
            if not re.fullmatch(r'[0-9a-f]{64}',str(f.get('sha256',''))):
                raise FusionProblem('Нет хеша кадра.', 'missing_frame_hash')
            used_times.add(t);frame_index[f['frame_id']]=f
            o=f.get('ocr') or {};a=o.get('start_ms',t);b=o.get('end_ms',min(t+40,duration))
            if not _time(a,b,duration) or a!=t or b>t+40:
                raise FusionProblem('OCR не привязан к одному кадру.', 'invalid_ocr_time')
            frames.append({'frame_id':f['frame_id'],'scene_id':sc,'time_ms':t,'frame_sha256':f['sha256'],
                           'evidence_id':_ref('frame',prepare_job_id,f['frame_id']),
                           'sampling_kind':'single_frame'})
            if o.get('status')=='ok' and _text(o.get('text')):
                ocr.append({'start_ms':a,'end_ms':b,'text':o['text'],
                            'frame_id':f['frame_id'],'frame_sha256':f['sha256'],
                            'evidence_id':_ref('ocr',prepare_job_id,f['frame_id']),
                            'measurement':'ocr_single_frame','quality':'unverified_ocr'})
        if not frames:raise FusionProblem('Нет кадров Vision.', 'no_frames')
    if observations is not None:
        if observations.get('schema_version') != 'visual-observations/v1' or _expect_hash(observations,'vision')!=video_hash or observations.get('video_source_version_id') != source_version_id or observations.get('prepare_job_id')!=prepare_job_id:
            raise FusionProblem('Несоответствие Vision результата.', 'visual_version_mismatch')
        obs=observations.get('scenes')
        if not isinstance(obs,list) or len(obs)>len(scenes):raise FusionProblem('Повреждена Vision схема.', 'invalid_model_json')
        used=set()
        for s in obs:
            scene=scene_index.get(s.get('scene_id')) if isinstance(s,dict) else None
            if scene is None or s['scene_id'] in used or s.get('start_ms')!=scene['start_ms'] or s.get('end_ms')!=scene['end_ms']:
                raise FusionProblem('Vision выдумал сцену.', 'invalid_model_reference')
            used.add(s['scene_id']);refs=s.get('frame_ids')
            if not isinstance(refs,list) or not refs or len(refs)>8 or len(refs)!=len(set(refs)) or not set(refs).issubset(set(scene.get('frame_ids',[]))):
                raise FusionProblem('Vision выдумал кадр.', 'invalid_model_reference')
            description=s.get('visual_description')
            if not _text(description):raise FusionProblem('Пустое описание Vision.', 'invalid_model_json')
            for fid in refs:
                frame=frame_index[fid];t=frame['time_ms'];end=min(duration,t+40)
                visual.append({'start_ms':t,'end_ms':end,'frame_id':fid,'scene_id':s['scene_id'],
                               'frame_sha256':frame['sha256'],'visual_description':description,
                               'observed_visible':s.get('observed_visible',[]),'editing_observations':s.get('camera_editing',[]),
                               'product_logo_visibility':s.get('product_logo_visibility','uncertain'),
                               'inferred_interpretation':s.get('inferred_interpretation'),
                               'uncertainty':s.get('uncertainty'),
                               'evidence_id':_ref('model_vision',vision_job_id,f'{s["scene_id"]}:{fid}'),
                               'trust':'model_reported_unverified','coverage':'sampled_frame_only'})
    else:
        warnings.append('vision_ai_not_run')
    if transcript is None:warnings.append('transcript_missing')
    elif not speech and speech_status!='no_speech':warnings.append('timed_speech_missing')
    if prepared is None:warnings.append('visual_preparation_missing')
    elif not ocr:warnings.append('ocr_missing_or_no_text')
    if transcript and isinstance(transcript.get('warnings'),list):
        warnings.extend('transcript:'+str(x)[:80] for x in transcript['warnings'][:30])
    if prepared and isinstance(prepared.get('warnings'),list):
        warnings.extend('vision:'+str(x)[:80] for x in prepared['warnings'][:30])
    # A frame is observable only at a sampled instant; its timestamp span is
    # at most 40 ms. Scene metadata can span shots; semantic description cannot.
    boundaries={0,duration}
    for s in speech+ocr+visual:
        boundaries.add(s['start_ms']);boundaries.add(s['end_ms'])
    for s in scenes:boundaries.add(s['start_ms']);boundaries.add(s['end_ms'])
    boundaries=sorted(boundaries)
    total_intervals=len(boundaries)-1
    truncated=total_intervals>MAX_EVENTS
    if truncated:
        boundaries=boundaries[:MAX_EVENTS+1]
        warnings.append('timeline_interval_limit')
    timeline=[]
    speech_cursor=0
    for start,end in zip(boundaries,boundaries[1:]):
        while speech_cursor<len(speech) and speech[speech_cursor]['end_ms']<=start:
            speech_cursor+=1
        spoken=[speech[speech_cursor]] if speech_cursor<len(speech) and speech[speech_cursor]['start_ms']<=start and speech[speech_cursor]['end_ms']>=end else []
        screen=[x for x in ocr if x['start_ms']<=start and x['end_ms']>=end]
        seen=[x for x in visual if x['start_ms']<=start and x['end_ms']>=end]
        timeline.append({'start_ms':start,'end_ms':end,'spoken_text':' '.join(x['text'] for x in spoken) or None,
                         'on_screen_text':' '.join(x['text'] for x in screen) or None,
                         'visual_description':' | '.join(dict.fromkeys(x['visual_description'] for x in seen)) or None,
                         'scene_id':_scene_at(scenes,start),
                         'speech_evidence_ids':[x['evidence_id'] for x in spoken],
                         'ocr_evidence_ids':[x['evidence_id'] for x in screen],
                         'vision_evidence_ids':[x['evidence_id'] for x in seen],
                         'measurement':'sample_only' if screen or seen else 'timeline_interval'})
    evidence={}
    for s in speech:
        evidence[s['evidence_id']]={'kind':'asr_segment','locator':{'start_ms':s['start_ms'],'end_ms':s['end_ms']},'exact_text':s['text'],'source_job_id':transcript_job_id,'trust':'measured_transcription'}
    for s in scenes:
        evidence[s['evidence_id']]={'kind':'scene_boundary','locator':{'start_ms':s['start_ms'],'end_ms':s['end_ms'],'scene_id':s['scene_id']},'source_job_id':prepare_job_id,'trust':'local_scene_detection'}
    for f in frames:
        evidence[f['evidence_id']]={'kind':'sampled_frame','locator':{'time_ms':f['time_ms'],'frame_id':f['frame_id'],'sha256':f['frame_sha256']},'source_job_id':prepare_job_id,'trust':'local_frame_sample'}
    for s in ocr:
        evidence[s['evidence_id']]={'kind':'ocr_text','locator':{'start_ms':s['start_ms'],'end_ms':s['end_ms'],'frame_id':s['frame_id']},'exact_text':s['text'],'source_job_id':prepare_job_id,'trust':'ocr_unverified'}
    for s in visual:
        evidence[s['evidence_id']]={'kind':'model_visual_description','locator':{'time_ms':s['start_ms'],'frame_id':s['frame_id'],'scene_id':s['scene_id'],'frame_sha256':s['frame_sha256']},'exact_text':s['visual_description'],'source_job_id':vision_job_id,'trust':'model_reported_unverified','needs_review':True,'frame_evidence_id':_ref('frame',prepare_job_id,s['frame_id'])}
    for row in timeline:
        refs=row['speech_evidence_ids']+row['ocr_evidence_ids']+row['vision_evidence_ids']
        if any(x not in evidence for x in refs):raise FusionProblem('Неверная ссылка на evidence.', 'missing_evidence')
    # B1 Hook candidates: literal text evidence only, never a taxonomy label.
    opening=[x for x in speech+ocr if x['start_ms']<5000][:12]
    cta_re=re.compile(r'(?i)\b(?:подписывайтесь|подпишись|купите|закажите|переходите|ссылка\s+в\s+профиле|subscribe|follow\s+me|link\s+in\s+bio|shop\s+now)\b')
    ctas=[{'text':x['text'],'start_ms':x['start_ms'],'evidence_id':x['evidence_id'],
           'review_status':'candidate_needs_review'} for x in speech+ocr if cta_re.search(x['text'])][:30]
    sidecar_present=bool(transcript and transcript.get('source')=='srt' and speech)
    speech_ms=sum(x['end_ms']-x['start_ms'] for x in speech)
    words=sum(len(re.findall(r'\b[^\W_]+\b',x['text'],flags=re.UNICODE)) for x in speech)
    measured={'duration_sec':round(duration/1000,3),'aspect_ratio': None,
              'scene_count':None,  # FFmpeg sampled-only scenes cannot establish true full count
              'sampled_scene_count':len(scenes) if prepared else None,
              'scene_count_method':'sampled_detected_or_binned_not_full_count' if prepared else 'unknown',
              'spoken_words':words if speech else (0 if speech_status=='no_speech' else None),
              'speech_wpm':round(words/(speech_ms/60000),2) if speech_ms>0 else None,
              'speech_coverage_pct':round(100*speech_ms/duration,3) if transcript and speech else (0 if speech_status=='no_speech' else None),
              'first_speech_sec':round(speech[0]['start_ms']/1000,3) if speech else None,
              'first_text_sec':None,  # OCR covers sampled frames, not verified first appearance
              'first_sampled_text_sec':round(min(x['start_ms'] for x in ocr)/1000,3) if ocr else None,
              'has_subtitles':None,  # SRT sidecar is not proof captions are visible on-screen
              'has_subtitle_sidecar':sidecar_present,
              'cta_start_sec': None,
              'music_sfx':None}
    creative={'hook':{'candidate_spoken_or_screen_text':[{'text':x['text'],'evidence_id':x['evidence_id'],'start_ms':x['start_ms']} for x in opening],
                      'mechanisms':None,'review_status':'candidate_needs_review' if opening else 'unavailable'},
              'content_format':{'label':None,'review_status':'unclassified'},
              'narrative':{'summary':None,'story_pattern':None,'review_status':'unclassified'},
              'key_shots':[{'frame_id':v['frame_id'],'time_ms':v['start_ms'],'description':v['visual_description'],
                            'evidence_id':v['evidence_id'],'trust':'model_reported_unverified'} for v in visual],
              'editing_observations':[{'text':x,'evidence_id':v['evidence_id'],'review_status':'needs_review'} for v in visual for x in v['editing_observations']][:60],
              'subtitle_style':{'label':None,'review_status':'unclassified'},
              'cta':{'candidates':ctas,'review_status':'needs_review' if ctas else 'unavailable_not_confirmed_absent'},
              'brand_product_appearance':{'observations':[{'value':v['product_logo_visibility'],'evidence_id':v['evidence_id'],'review_status':'needs_review'} for v in visual],
                                          'not_inferred_from_absence':True},
              'audience_signals':{'observations':[],'review_status':'not_classified'}}
    status=('partial' if warnings or truncated or transcript and transcript['processing_status']!='completed'
            or prepared and prepared.get('status')!='completed' or observations and observations.get('status')!='completed' else 'completed')
    coverage={'duration_ms':duration,'timed_speech_segments':len(speech),'ocr_samples_with_text':len(ocr),
              'frames_sampled':len(frames),'frames_with_ai_description':len({v['frame_id'] for v in visual}),
              'all_frames_analyzed':False,'vision_is_sparse':True,'speech_status':speech_status,
              'timeline_intervals_total':total_intervals,'timeline_intervals_emitted':len(timeline),
              'timeline_truncated':truncated,'full_visual_coverage':False}
    data={'schema_version':VERSION,'fusion_engine_version':ENGINE_VERSION,
          'processing_status':status,'platform':acquisition.get('platform'),'platform_url':acquisition.get('canonical_url'),
          'native_video_id':acquisition.get('native_id'),'video_sha256':video_hash,
          'project_id':project_id,'source_id':source_id,'video_source_version_id':source_version_id,
          'transcript':{'job_id':transcript_job_id,'speech_status':speech_status,'segments':speech,
                        'languages':transcript.get('languages',[]) if transcript else [],
                        'processing_status':transcript['processing_status'] if transcript else 'unavailable'},
          'visual_scenes':scenes,'frame_samples':frames,'ocr_segments':ocr,'visual_observations':visual,
          'timeline':timeline,'evidence':evidence,'coverage':coverage,
          'creative_analysis':creative,'group_a_compatible_unpublished':measured,
          'group_b_candidates_unpublished':{'hook':creative['hook'],'format':creative['content_format'],
                                           'story':creative['narrative'],'editing':creative['editing_observations'],
                                           'subtitles':creative['subtitle_style'],'cta':creative['cta'],
                                           'visual':creative['brand_product_appearance'],'topics':None},
          'provenance':input_provenance or {},'cost_summary':cost_summary or {'fusion_usd':0,'upstream_usd':None},
          'warnings':list(dict.fromkeys(warnings)),
          'review':{'status':'needs_review','group_a_published':False,'group_b_published':False}}
    if len(canonical(data))>MAX_JSON:
        raise FusionProblem('Fusion JSON превышает лимит; сохрани более короткую версию входа.', 'result_too_large')
    return data
