"""PSI Stage 11: deterministic, immutable Project Context v1 assembly.

No file reads, no network/LLM, no implicit publication. The only facts in this
object are attributed, still-typed evidence and immutable analysis references.
"""
from __future__ import annotations
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

VERSION='project-context/v1'
BUILDER_VERSION='project-context-builder/1.0.0'
MAX_SOURCES=180
MAX_OUTPUTS=750
MAX_EVIDENCE=3000
MAX_PAYLOAD_BYTES=5_000_000
PROFILE_FIELDS=('name','niche','goal','product_description','product_type','product_functions',
                'audience_problems','product_url','competitors','competitor_socials',
                'promoter_accounts','video_references')
REDACT=re.compile(r'(?i)\b(?:api[_-]?key|access[_-]?token|token|secret|password|authorization|bearer)\b\s*[:=]\s*[^\s,;]+')
LOCAL_PATH=re.compile(r'(?i)(?:[A-Z]:\\(?:[^\s]+)|/(?:home|Users|mnt/data|var|etc)/[^\s]+)')
SENSITIVE_PARAM=re.compile(r'(?i)(?:token|key|secret|pass|auth|credential|session)')

class ContextProblem(Exception):
    def __init__(self,message,code='context_error',http_status=422):
        super().__init__(message);self.code=code;self.http_status=http_status

def canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')

def sha(obj):return hashlib.sha256(canonical(obj)).hexdigest()

def safe_text(value,limit=12000):
    if not isinstance(value,str):return None
    value=LOCAL_PATH.sub('[local path omitted]',REDACT.sub('[credential omitted]',value))
    return value[:limit]

def safe_url(value):
    if not isinstance(value,str):return None
    try:
        u=urlsplit(value)
        if u.scheme not in ('https','http') or not u.hostname:return None
        host=u.hostname.encode('idna').decode('ascii')
        port=':'+str(u.port) if u.port else ''
        params=[(k,v) for k,v in parse_qsl(u.query,keep_blank_values=True) if not SENSITIVE_PARAM.search(k)]
        return urlunsplit((u.scheme,host+port,u.path,urlencode(params),''))[:2048]
    except (ValueError,UnicodeError):return None

def sanitize_profile(obj):
    if not isinstance(obj,dict):raise ContextProblem('Некорректный профиль.','invalid_profile',409)
    # Real wizard mirrors persist a wrapper with nested 'profile'. Flat test
    # fixtures remain supported. Ignore additional keys and secrets.
    if isinstance(obj.get('profile'),dict):obj=obj['profile']
    # Support the existing flat wizard; NEVER copy arbitrary nested keys or secrets.
    result={}
    for key in PROFILE_FIELDS:
        raw=obj.get(key)
        if isinstance(raw,str):result[key]=safe_url(raw) if key=='product_url' and raw.startswith(('https://','http://')) else safe_text(raw)
        elif isinstance(raw,(list,dict)):
            # Keep explicit structured lists of user-provided reference strings only.
            if isinstance(raw,list):result[key]=[safe_text(v,1500) for v in raw[:60] if isinstance(v,str)]
            else:result[key]='[structured value omitted; see pinned profile revision]'
        elif raw is None:result[key]=None
        else:result[key]=str(raw)[:250] if type(raw) in (int,float,bool) else None
    return result

def _date(dt):return dt.isoformat() if hasattr(dt,'isoformat') else (dt if isinstance(dt,str) else None)

def build(*,project_id,profile_revision,profile_hash,profile_data,source_rows,output_rows,evidence_rows,
          relationship_rows=(),created_dates=None):
    """Caller must supply a consistent transactional read and verified SQL pins.

    source_rows: id,type,label,url,status,tombstone,version_id,version_no,
        content_sha256,byte_size,media_type,locator,recorded_at,initial_status
    output_rows: id,source_id,version_id,output_type,schema_version,output_sha256,
        output JSON, created_at
    evidence_rows: id,source_id,version_id,processing_output_id,quote,kind,locator,
        quality,review_status,trust,scope,citation,review_decision,created_at
    """
    if not isinstance(project_id,str) or not re.fullmatch('[0-9a-f]{32}',project_id):
        raise ContextProblem('Некорректный ID проекта.','invalid_project',400)
    if type(profile_revision) is not int or profile_revision<1 or not re.fullmatch('[0-9a-f]{64}',str(profile_hash)):
        raise ContextProblem('Некорректная привязка анкеты.','profile_pin',409)
    if len(source_rows)>MAX_SOURCES or len(output_rows)>MAX_OUTPUTS or len(evidence_rows)>MAX_EVIDENCE:
        raise ContextProblem('Превышен лимит контекста. Источники не будут молча отброшены.','context_limit',409)
    source_index={};manifest=[];missing=[]
    for s in sorted(source_rows,key=lambda x:str(x['id'])):
        sid=str(s['id']);vid=str(s['version_id']) if s.get('version_id') else None
        if sid in source_index:raise ContextProblem('Дублирован источник.','duplicate_source',409)
        loc=s.get('locator') or {}
        provenance_kind=('model_reported_text_sha256' if loc.get('raw_response_captured') is False or
                         loc.get('retrieval')=='model_reported_web_fetch' else
                         'source_byte_sha256' if s.get('content_sha256') else 'not_captured')
        item={'source_id':sid,'source_type':s['source_type'],'label':safe_text(s.get('label'),250),
              'canonical_url':safe_url(s.get('canonical_url')),'source_version_id':vid,
              'version_no':s.get('version_no'),'sha256':s.get('content_sha256'),
              'sha256_kind':provenance_kind,'byte_size':s.get('byte_size'),
              'media_type':s.get('media_type'),'recorded_at':_date(s.get('recorded_at')),
              'processing_status':s.get('initial_status') or s.get('status'),
              'analysis_refs':[],'evidence_count':0,'approved_evidence_count':0,
              'excluded_reason':None}
        if not vid:item['excluded_reason']='source_unversioned'
        if s.get('tombstoned'):item['excluded_reason']='source_archived'
        source_index[sid]=item;manifest.append(item)
    output_index={}
    for o in sorted(output_rows,key=lambda x:str(x['id'])):
        oid=str(o['id']);sid=str(o['source_id']);vid=str(o['version_id']);source=source_index.get(sid)
        if not source or source['source_version_id']!=vid:
            raise ContextProblem('Output не принадлежит pinned версии источника.','output_pin_mismatch',409)
        if oid in output_index:raise ContextProblem('Повтор analysis output.','duplicate_output',409)
        data=o['output'];digest=o['output_sha256']
        if not isinstance(data,dict) or sha(data)!=digest:
            raise ContextProblem('Хеш сохранённого анализа не совпадает.','output_integrity',409)
        if not re.fullmatch('[0-9a-f]{64}',digest):raise ContextProblem('Недопустимый SHA256.','output_integrity',409)
        method=(data.get('parser_version') or data.get('extractor_version') or data.get('normalizer_version')
                or data.get('fusion_engine_version') or data.get('model') or data.get('schema_version'))
        ref={'output_id':oid,'source_id':sid,'source_version_id':vid,'output_type':o['output_type'],
             'schema_version':o['schema_version'],'output_sha256':digest,'engine_version':str(method)[:160] if method else None,
             'model':str(data['model'])[:160] if isinstance(data.get('model'),str) else None,
             'processing_config_sha256':sha(data['config']) if isinstance(data.get('config'),dict) else None,
             'created_at':_date(o.get('created_at')),
             'processing_status':data.get('processing_status') or data.get('status') or data.get('fetch_status')}
        output_index[oid]=ref;source['analysis_refs'].append(oid)
    evidence_index={};evidence=[]
    for e in sorted(evidence_rows,key=lambda x:str(x['id'])):
        eid=str(e['id']);sid=str(e['source_id']);vid=str(e['version_id']);source=source_index.get(sid)
        if not source or source['source_version_id']!=vid:
            raise ContextProblem('Evidence не принадлежит pinned версии источника.','evidence_pin_mismatch',409)
        oid=str(e['output_id']) if e.get('output_id') else None
        if oid and oid not in output_index:raise ContextProblem('Evidence ссылается на неподключённый output.','evidence_output_missing',409)
        if eid in evidence_index:raise ContextProblem('Дублирован evidence_id.','duplicate_evidence',409)
        if e.get('review_decision') not in (None,'approved','rejected'):
            raise ContextProblem('Неизвестное решение человека.','invalid_review',409)
        item={'evidence_id':eid,'source_id':sid,'source_version_id':vid,'processing_output_id':oid,
              'kind':e['kind'],'knowledge_scope':e['scope'],'trust_label':e['trust'],
              'quality_status':e['quality'],'review_status':e['review_status'],
              'review_decision':e.get('review_decision'),'citation':e['citation'],
              'exact_text_sha256':hashlib.sha256(e['quote'].encode('utf-8')).hexdigest(),
              'locator_sha256':sha(e['locator']),
              'created_at':_date(e.get('created_at'))}
        evidence_index[eid]=item;evidence.append(item)
        source['evidence_count']+=1
        if item['review_decision']=='approved':source['approved_evidence_count']+=1
    relations=[]
    for x in relationship_rows:
        a,b=str(x['a']),str(x['b'])
        if a not in evidence_index or b not in evidence_index:continue
        relations.append({'a':a,'b':b,'relation':x['relation'],'review_state':'needs_review'})
    relations.sort(key=lambda x:(x['a'],x['b'],x['relation']))
    for item in manifest:
        if item['excluded_reason'] is None and item['evidence_count']==0:
            item['excluded_reason']='no_grounded_evidence'
        if item['excluded_reason'] is not None:
            missing.append({'source_id':item['source_id'],'source_version_id':item['source_version_id'],
                            'reason':item['excluded_reason']})
    warnings=[]
    if missing:warnings.append('source_coverage_incomplete')
    if any(e['review_decision'] is None for e in evidence):warnings.append('unreviewed_evidence')
    if relations:warnings.append('reviewable_evidence_relationships')
    if any(e['trust_label']=='model_reported_unverified' for e in evidence):warnings.append('model_reported_unverified')
    # Only direct evidence review is considered approved. This is not truth verification.
    context={'schema_version':VERSION,'builder_version':BUILDER_VERSION,
            'project_id':project_id,'profile':{'revision':profile_revision,'hash':profile_hash,
               'fields':sanitize_profile(profile_data)},
            'source_manifest':manifest,
            'analysis_refs':sorted(output_index.values(),key=lambda x:x['output_id']),
            'evidence_manifest':evidence,'evidence_ids':sorted(evidence_index),
            'approved_evidence_ids':sorted(x['evidence_id'] for x in evidence if x['review_decision']=='approved'),
            'reviewable_relationships':relations,
            'unavailable_or_excluded_sources':missing,
            'coverage':{'sources_total':len(manifest),'sources_incomplete':len(missing),
                        'analysis_outputs':len(output_index),'evidence_total':len(evidence),
                        'reviewed_approved':sum(x['review_decision']=='approved' for x in evidence),
                        'reviewed_rejected':sum(x['review_decision']=='rejected' for x in evidence),
                        'review_pending':sum(x['review_decision'] is None for x in evidence)},
            'warnings':warnings,'selection_policy':'evidence-on-demand/v1',
            'approval_status':'pending'}
    if len(canonical(context))>MAX_PAYLOAD_BYTES:
        raise ContextProblem('JSON превышает безопасный лимит; требуется разбивка без потери provenance.','context_limit',409)
    return context

def choose_evidence(payload,quotes,*,token_budget=1200):
    """A compact LLM retrieval view; the immutable full context is unchanged."""
    if type(token_budget) is not int or not 100<=token_budget<=8000:
        raise ContextProblem('token_budget: 100..8000.','invalid_budget',400)
    index={str(x['evidence_id']):x for x in payload['evidence_manifest']}
    # 4 chars/token is a conservative budget heuristic, not a tokenizer guarantee.
    ceiling=token_budget*3
    rows=[];used=0
    priority={'operational_product':0,'competitor_research':1,'general_research':2,'creative_reference':3}
    ranked=sorted((x for x in payload['evidence_manifest'] if x['review_decision']!='rejected'),
        key=lambda x:(x['review_decision']!='approved',priority.get(x['knowledge_scope'],4),x['evidence_id']))
    for ev in ranked:
        line=quotes.get(ev['evidence_id'])
        if not isinstance(line,str) or not line.strip():continue
        text=safe_text(line[:1700])
        if not text:continue
        footprint=len(text)+len(ev['citation'])+150
        if footprint>ceiling-used:continue
        rows.append({'evidence_id':ev['evidence_id'],'citation':ev['citation'],
                     'text':text,'trust_label':ev['trust_label'],
                     'knowledge_scope':ev['knowledge_scope'],'review_decision':ev['review_decision']})
        used+=footprint
    return {'schema_version':'context-evidence-selection/v1','context_hash':sha(payload),
            'token_budget':token_budget,'estimated_chars':used,'selected':rows,
            'selected_count':len(rows),'total_eligible':len(ranked),
            'all_evidence_ids_remain_in_immutable_context':True}
