"""Stage13 read-only project source processing activity, persisted in PostgreSQL.

Never infer 'ready' from a source merely being registered or a job queued.
"""
from __future__ import annotations
import re
from .context_service import _conn

VALID_PROJECT=re.compile(r'^[0-9a-f]{32}$')
STAGES={'web_fetch','document_parse','video_download','asr','ocr','vision',
        'timeline_fusion','evidence_normalization'}
PROCESSING={'queued','processing'}
FINISHED={'completed','partial','unavailable','failed','cancelled','needs_review'}

class ActivityProblem(Exception):
    def __init__(self,message,http_status=422,code='activity_error'):
        super().__init__(message)
        self.http_status=http_status
        self.code=code

def _project(pid):
    if not isinstance(pid,str) or not VALID_PROJECT.fullmatch(pid):
        raise ActivityProblem('Неверный проект.',400,'invalid_project')
    return pid

def _limit(offset,limit):
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=100000 or not 1<=limit<=100:
        raise ActivityProblem('Некорректная пагинация.',400,'invalid_pagination')

def _status(active_version,source_status,job_states,evidence_count):
    # A queued or processing job overrides stored completed-looking source metadata.
    if job_states and job_states[0] in PROCESSING:
        return job_states[0]
    if job_states and job_states[0] in {'failed','cancelled','unavailable','needs_review','partial'}:
        return job_states[0]
    if source_status=='partial':
        return 'partial'
    if active_version is None:
        return 'registered' if not job_states else 'partial'
    if evidence_count>0:
        return 'ready'
    if any(state in ('partial','completed') for state in job_states):
        return 'processed_without_evidence'
    if source_status in ('failed','unavailable','cancelled'):
        return source_status
    return 'registered'

def activity(pid,*,offset=0,limit=30):
    _project(pid);_limit(offset,limit)
    with _conn() as conn:
        if not conn.execute('SELECT 1 FROM project_registry WHERE id=%s',(pid,)).fetchone():
            raise ActivityProblem('Проект не найден.',404,'not_found')
        count=conn.execute("""SELECT count(*) FROM project_sources
            WHERE project_id=%s AND tombstoned_at IS NULL""",(pid,)).fetchone()[0]
        rows=conn.execute("""
          WITH page AS (
            SELECT id,source_type,label,status,active_version_id,created_at
            FROM project_sources WHERE project_id=%s AND tombstoned_at IS NULL
            ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s
          )
          SELECT s.id,s.source_type,s.label,s.status,s.active_version_id,s.created_at,
                 (SELECT count(*) FROM project_evidence e
                  WHERE e.project_id=%s AND e.source_id=s.id
                    AND e.source_version_id=s.active_version_id) AS evidence_count,
                 j.id,j.stage,j.state,j.attempts,j.max_attempts,j.error_code,
                 j.error_message,j.charge_state,j.cost_actual_usd,j.created_at,
                 j.source_version_id
          FROM page s LEFT JOIN LATERAL (
            SELECT id,stage,state,attempts,max_attempts,error_code,error_message,
                   charge_state,cost_actual_usd,created_at,source_version_id
            FROM source_extraction_jobs WHERE project_id=%s AND source_id=s.id
            -- Always include recent work on the *active* source version
            -- before showing historical versions (bounded at 12 jobs).
            ORDER BY (source_version_id=s.active_version_id) DESC,
                     created_at DESC,id DESC LIMIT 12
          ) j ON TRUE
          ORDER BY s.created_at DESC,s.id DESC,
                   (j.source_version_id=s.active_version_id) DESC,
                   j.created_at DESC,j.id DESC
        """,(pid,limit,offset,pid,pid)).fetchall()
    grouped={}
    for x in rows:
        sid=str(x[0])
        if sid not in grouped:
            grouped[sid]={
                'source_id':sid,'source_type':x[1],'label':x[2],
                'source_status':x[3],'active_source_version_id':str(x[4]) if x[4] else None,
                'registered_at':x[5].isoformat(),'evidence_count':int(x[6]),
                'jobs':[]}
        if x[7] is not None:
            grouped[sid]['jobs'].append({
                'job_id':str(x[7]),'stage':x[8],'state':x[9],
                'attempts':x[10],'max_attempts':x[11],
                'error_code':x[12],'error_message':x[13],
                'charge_state':x[14],
                'cost_actual_usd':str(x[15]) if x[15] is not None else None,
                'created_at':x[16].isoformat(),
                'source_version_id':str(x[17]) if x[17] else None,
                'is_current_version':x[17]==x[4]
            })
    items=list(grouped.values())
    for item in items:
        # Historical jobs must remain visible, but only the CURRENT pinned
        # source version can determine readiness/progress. A retry of v1 may
        # not turn newly uploaded v2 into "failed", "partial" or "ready".
        current=[j['state'] for j in item['jobs'] if j['is_current_version']]
        item['current_version_job_count']=len(current)
        item['historical_job_count']=len(item['jobs'])-len(current)
        item['display_state']=_status(item['active_source_version_id'],
            item['source_status'],current,item['evidence_count'])
    return {'project_id':pid,'items':items,'count':count,'offset':offset,'limit':limit,
            'more':offset+limit<count,'read_only':True,'no_model_calls':True}
