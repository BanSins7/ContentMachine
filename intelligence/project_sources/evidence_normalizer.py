"""PSI Stage 10: deterministic grounded evidence candidates from pinned immutable input.

NO LLM calls. A quote must exist verbatim in the specifically identified stored
output. Derived summaries/claims are not a substitute for source text.
"""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

VERSION='evidence-normalizer/1.0.0'
MAX_ITEMS=450
MAX_QUOTE=12000

class EvidenceProblem(Exception):
    def __init__(self, message, code='invalid_evidence', http_status=422):
        super().__init__(message);self.code=code;self.http_status=http_status


def canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def sha(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def fingerprint(text):
    return hashlib.sha256(re.sub(r'\s+',' ',unicodedata.normalize('NFC',text)).strip().casefold().encode('utf-8')).hexdigest()


def scope_for(source_type):
    return ('operational_product' if source_type in ('questionnaire_field','product_website')
            else 'competitor_research' if source_type in ('competitor_website','competitor_account','promoter_account')
            else 'creative_reference' if source_type=='user_video_reference' else 'general_research')


def typed_value(text):
    """Report explicitly written units only; no inferred values or absent=>zero."""
    s=text.strip()
    if len(s)>120:return None
    date_match=re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})',s)
    if date_match:
        try:return {'type':'date','value':date.fromisoformat(s).isoformat()}
        except ValueError:return None
    m=re.fullmatch(r'([+-]?\d+(?:[.,]\d+)?)\s*(%|USD|EUR|RUB|BYN|€|\$|₽|ms|s|sec|seconds|min|minutes|hours|h|days|kg|g|km|m|MB|GB)',s,re.I)
    if not m:return None
    try:v=Decimal(m.group(1).replace(',','.'))
    except InvalidOperation:return None
    if not v.is_finite():return None
    unit=m.group(2)
    return {'type':'percentage' if unit=='%' else 'measurement','value':str(v),'unit':unit}


def validate_quote(container, exact, start=None, end=None):
    if not isinstance(container,str) or not isinstance(exact,str) or not exact.strip() or len(exact)>MAX_QUOTE:
        raise EvidenceProblem('Пустая или чрезмерная цитата.','invalid_quote')
    if start is None and end is None:
        offset=container.find(exact)
        if offset<0:raise EvidenceProblem('Цитата отсутствует в закреплённом источнике.','unverified_quote')
        return offset,offset+len(exact)
    if type(start) is not int or type(end) is not int or start<0 or end<=start or container[start:end]!=exact:
        raise EvidenceProblem('Поддельное смещение или несовпадающая цитата.','unverified_quote')
    return start,end


def _candidate(text,locator,*,kind,method,trust,quality='complete',claim_key=None,extra=None):
    if not isinstance(text,str) or not text.strip() or len(text)>MAX_QUOTE:
        return None
    if not isinstance(locator,dict) or not locator:return None
    if kind not in ('user_claim','external_claim','observed_media','model_inference'):
        raise EvidenceProblem('Неизвестный тип доказательства.','invalid_kind')
    return {'text':text,'locator':locator,'kind':kind,'method':method,'trust':trust,
            'quality':quality if quality in ('complete','partial','unavailable') else 'partial',
            'claim_key':claim_key,'typed_value':typed_value(text),'metadata':extra or {},
            'review_status':'needs_review' if kind=='model_inference' or trust in ('model_reported_unverified','ocr_unverified') else 'pending'}


def profile_candidate(text,field_path,revision,profile_hash):
    if not isinstance(field_path,str) or not re.fullmatch(r'[A-Za-z0-9_\[\].-]{1,250}',field_path):
        raise EvidenceProblem('Некорректный путь поля.','profile_path')
    if type(revision) is not int or revision<1 or not re.fullmatch('[0-9a-f]{64}',str(profile_hash)):
        raise EvidenceProblem('Некорректная версия анкеты.','profile_version')
    c=_candidate(text,{'kind':'questionnaire','field_path':field_path,'start_char':0,'end_char':len(text),
                       'profile_revision':revision,'profile_hash':profile_hash},
                 kind='user_claim',method='immutable_profile_field',trust='direct_user_statement',
                 claim_key='profile:'+field_path)
    if not c:raise EvidenceProblem('Поле анкеты пусто или слишком длинное.','empty_profile')
    c['scope']='operational_product'
    return [c]


def extract(output,source_type,*,prepared=None):
    """Source-specific strict extraction; rejects fabricated quotes and bad refs.

    An untrusted malformed record is skipped with a warning, not silently treated
    as a successfully grounded statement. Returns bounded candidates + warnings.
    """
    if not isinstance(output,dict):raise EvidenceProblem('Нет закреплённого JSON.','missing_output')
    schema=output.get('schema_version');scope=scope_for(source_type)
    result=[];warnings=[]
    def add(x):
        if x is not None and len(result)<MAX_ITEMS:result.append(x)
        elif len(result)>=MAX_ITEMS and 'evidence_limit' not in warnings:warnings.append('evidence_limit')
    def warn(code):
        if code not in warnings:warnings.append(code)
    if schema=='document-segments/v1':
        if output.get('status') not in ('completed','partial','unavailable'):
            raise EvidenceProblem('Неверный статус парсера.','invalid_output')
        for i,row in enumerate(output.get('segments',[])[:MAX_ITEMS+1]):
            if not isinstance(row,dict) or not isinstance(row.get('locator'),dict):warn('invalid_document_segment');continue
            txt=row.get('text');loc=row['locator'];fmt=output.get('format')
            if not isinstance(txt,str) or not txt.strip():continue
            if not isinstance(loc.get('start_char'),int) or loc.get('end_char')!=loc['start_char']+len(txt):
                warn('invalid_document_offsets');continue
            location={'kind':'document','format':fmt,'segment_index':i,**loc}
            add(_candidate(txt,location,kind='external_claim',method=output.get('parser_version','document-parser'),
                           trust='local_document_extraction',quality='partial' if output.get('status')=='partial' else 'complete',
                           extra={'role':row.get('role')}))
    elif schema=='web-source-extract/v1':
        page=output.get('page_text')
        if (not isinstance(page,str) or output.get('page_text_sha256')!=hashlib.sha256(page.encode()).hexdigest()
            or output.get('trust_level')!='model_reported_unverified'):
            raise EvidenceProblem('Сайт не имеет подтверждённого model-reported текста.','web_integrity')
        # 'claim' is the model's interpretation; only 'quote' from page_text is evidence.
        for kind,items in [('claim',output.get('claims',[])),('section',output.get('sections',[]))]:
            if not isinstance(items,list):raise EvidenceProblem('Неверные блоки сайта.','invalid_output')
            for i,row in enumerate(items[:MAX_ITEMS+1]):
                if not isinstance(row,dict):warn('invalid_web_block');continue
                q=row.get('quote')
                try:a,b=validate_quote(page,q,row.get('quote_start'),row.get('quote_end'))
                except EvidenceProblem:warn('unverified_web_quote');continue
                loc={'kind':'web','url':output.get('page_url'),'section':kind,'index':i,'start_char':a,'end_char':b}
                if not isinstance(loc['url'],str) or not loc['url'].startswith('https://'):
                    warn('invalid_web_url');continue
                add(_candidate(q,loc,kind='external_claim',method='openrouter_web_fetch',trust='model_reported_unverified',
                               quality='partial',extra={'claim_kind':row.get('kind') if kind=='claim' else None,
                                                        'interpretation_unverified':True}))
    elif schema=='transcript/v1':
        if not re.fullmatch('[0-9a-f]{64}',str(output.get('video_sha256',''))):
            raise EvidenceProblem('Нет хеша MP4.','video_hash')
        last=0
        for i,row in enumerate(output.get('segments',[])[:MAX_ITEMS+1]):
            if not isinstance(row,dict):warn('invalid_speech');continue
            a,b,q=row.get('start_ms'),row.get('end_ms'),row.get('text')
            if type(a) is not int or type(b) is not int or a<last or b<=a:
                warn('invalid_speech_time');continue
            last=b
            if row.get('source') not in ('srt','asr'):warn('invalid_speech_source');continue
            add(_candidate(q,{'kind':'video','stream':'speech','segment_index':i,'start_ms':a,'end_ms':b,
                              'video_sha256':output['video_sha256']},kind='observed_media',
                           method=output.get('engine') or 'transcript',trust='measured_transcription',
                           quality=output.get('processing_status','partial'),extra={'language':row.get('language')}))
    elif schema=='vision-prepare/v1':
        if not re.fullmatch('[0-9a-f]{64}',str(output.get('video_sha256',''))):
            raise EvidenceProblem('Нет хеша MP4.','video_hash')
        seen=set()
        for i,frame in enumerate(output.get('frames',[])[:MAX_ITEMS+1]):
            if not isinstance(frame,dict):warn('invalid_frame');continue
            fid,t=frame.get('frame_id'),frame.get('time_ms')
            if fid in seen or not re.fullmatch(r'f\d{3}',str(fid)) or type(t) is not int or t<0:
                warn('invalid_frame');continue
            seen.add(fid);ocr=frame.get('ocr') or {}
            if ocr.get('status')!='ok':continue
            a=ocr.get('start_ms',t);b=ocr.get('end_ms',t+40)
            if type(a) is not int or type(b) is not int or a!=t or not a<b<=t+40:
                warn('invalid_ocr_time');continue
            add(_candidate(ocr.get('text'),{'kind':'video','stream':'ocr','frame_id':fid,'frame_sha256':frame.get('sha256'),
                                              'start_ms':a,'end_ms':b,'video_sha256':output['video_sha256']},
                           kind='observed_media',method='tesseract_ocr',trust='ocr_unverified',quality='partial'))
    elif schema=='visual-observations/v1':
        # An AI caption is only grounded to a frame actually sampled by the
        # persisted preparation. Never extend this caption to the whole scene.
        if not isinstance(prepared,dict) or prepared.get('schema_version')!='vision-prepare/v1':
            raise EvidenceProblem('Нет проверенной подготовки кадров.','missing_frame_provenance')
        if (not re.fullmatch('[0-9a-f]{64}',str(output.get('video_sha256',''))) or
            output['video_sha256']!=prepared.get('video_sha256')):
            raise EvidenceProblem('SHA Vision и видео не совпадают.','video_hash')
        frames={f['frame_id']:f for f in prepared.get('frames',[])
                if isinstance(f,dict) and isinstance(f.get('frame_id'),str)}
        scenes={s['scene_id']:s for s in prepared.get('scenes',[])
                if isinstance(s,dict) and isinstance(s.get('scene_id'),str)}
        duration=prepared.get('video_duration_ms')
        for i,scene in enumerate(output.get('scenes',[])[:MAX_ITEMS+1]):
            if not isinstance(scene,dict):warn('invalid_vision_scene');continue
            base=scenes.get(scene.get('scene_id'))
            if (not base or scene.get('start_ms')!=base.get('start_ms') or
                scene.get('end_ms')!=base.get('end_ms')):
                warn('invalid_vision_scene');continue
            for fid in (scene.get('frame_ids') or [])[:8]:
                f=frames.get(fid)
                if (not f or f.get('scene_id')!=scene['scene_id'] or fid not in base.get('frame_ids',[]) or
                    type(f.get('time_ms')) is not int or not re.fullmatch('[0-9a-f]{64}',str(f.get('sha256','')))):
                    warn('invalid_frame');continue
                t=f['time_ms'];end=min(t+40,duration) if type(duration) is int else t+40
                if end<=t:warn('invalid_frame_time');continue
                add(_candidate(scene.get('visual_description'),{'kind':'video','stream':'vision','frame_id':fid,
                         'scene_id':scene['scene_id'],'scene_index':i,'frame_sha256':f['sha256'],
                         'start_ms':t,'end_ms':end,'video_sha256':output['video_sha256']},
                         kind='model_inference',method='sonnet_vision',trust='model_reported_unverified',
                         quality='partial',extra={'sampled_frame_only':True}))
    elif schema=='video-intelligence/v1':
        # Fusion is a derived index: verified again against its embedded immutable
        # ASR/OCR/vision evidence refs. Prefer native outputs for primary evidence.
        ledger=output.get('evidence')
        if not isinstance(ledger,dict):raise EvidenceProblem('Нет evidence ledger.','invalid_fusion')
        for ref,row in list(sorted(ledger.items()))[:MAX_ITEMS+1]:
            if not isinstance(row,dict):warn('invalid_ledger');continue
            kind=row.get('kind');literal=row.get('exact_text');loc=row.get('locator')
            if not isinstance(loc,dict) or not isinstance(ref,str):warn('invalid_ledger');continue
            if kind=='asr_segment':
                matches=[r for r in output.get('transcript',{}).get('segments',[]) if r.get('evidence_id')==ref]
                if len(matches)!=1 or matches[0].get('text')!=literal:warn('unverified_fusion_quote');continue
                k='observed_media';trust='measured_transcription'
            elif kind=='ocr_text':
                matches=[r for r in output.get('ocr_segments',[]) if r.get('evidence_id')==ref]
                if len(matches)!=1 or matches[0].get('text')!=literal:warn('unverified_fusion_quote');continue
                k='observed_media';trust='ocr_unverified'
            elif kind=='model_visual_description':
                matches=[r for r in output.get('visual_observations',[]) if r.get('evidence_id')==ref]
                if len(matches)!=1 or matches[0].get('visual_description')!=literal:warn('unverified_fusion_quote');continue
                k='model_inference';trust='model_reported_unverified'
            else:continue  # geometric scene/frame metadata isn't a textual claim
            if not isinstance(literal,str) or not literal.strip():continue
            locator={'kind':'video','stream':kind,'ledger_ref':ref,**loc}
            if kind!='asr_segment' and not locator.get('frame_id'):warn('missing_frame');continue
            add(_candidate(literal,locator,kind=k,method='timeline_fusion_index',trust=trust,quality='partial'))
    else:
        raise EvidenceProblem('Формат вывода не поддерживается.','unsupported_schema',422)
    if len(result)>=MAX_ITEMS:warn('evidence_limit')
    for c in result:c['scope']=scope
    return result,warnings


def lexical_similarity(a,b):
    """Only suggest a reviewable near-duplicate, NEVER semantic equivalence."""
    def words(x):return set(re.findall(r'[^\W_]+',unicodedata.normalize('NFC',x).casefold()))
    x,y=words(a),words(b)
    if len(x)<5 or len(y)<5:return 0
    numeric=lambda z:set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?',z))
    if numeric(a)!=numeric(b):return 0
    return len(x&y)/len(x|y)
