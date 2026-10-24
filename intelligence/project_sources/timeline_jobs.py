"""Durable, project-scoped, FREE Stage09 fusion of pinned, immutable processing outputs."""
from __future__ import annotations

import argparse
import json
import re
import time
from uuid import UUID

from .timeline_fusion import ENGINE_VERSION, FusionProblem, canonical, fuse, sha

LEASE=90
MAX_PAGE=100


def _pid(pid):
    from .video_jobs import _pid as valid
    try:return valid(pid)
    except Exception as exc:raise FusionProblem('Проект отсутствует.','not_found',404) from exc


def _conn():
    from .video_jobs import _conn as connect
    return connect()


def _uuid(raw,label):
    try:return str(UUID(str(raw)))
    except (ValueError,TypeError) as exc:raise FusionProblem('Некорректный '+label,'invalid_id',404) from exc


def _project_source(conn,pid,sid,lock=False):
    result=conn.execute('SELECT id FROM project_sources WHERE project_id=%s AND id=%s'+(' FOR UPDATE' if lock else ''),(pid,sid)).fetchone()
    if not result:raise FusionProblem('Источник проекта отсутствует.','not_found',404)


def _acquisition(conn,pid,sid,aid=None):
    q='''SELECT a.job_id,a.result_source_version_id,a.result_output_id,o.output,o.output_sha256
         FROM project_video_acquisition_runs a
         JOIN source_extraction_jobs j ON j.project_id=a.project_id AND j.id=a.job_id
         JOIN source_processing_outputs o ON o.project_id=a.project_id AND o.id=a.result_output_id
         WHERE a.project_id=%s AND a.source_id=%s AND j.state IN ('completed','partial')
         AND a.result_source_version_id IS NOT NULL AND o.schema_version='video-acquisition/v1' '''
    args=[pid,sid]
    if aid:q+=' AND a.job_id=%s';args.append(aid)
    q+=' ORDER BY a.finished_at DESC NULLS LAST,a.created_at DESC LIMIT 1'
    x=conn.execute(q,args).fetchone()
    if not x:raise FusionProblem('Нет готового проверенного видео Stage06.','no_video',409)
    if not isinstance(x[3],dict) or (not x[3].get('acquisition_verified') and x[3].get('trust')!='verified_local_asset'):
        raise FusionProblem('Нет подтверждения MP4.','unverified_video',409)
    return x


def _input(conn,table,kind,pid,sid,ver,jobid=None,parent=None):
    # Only committed outputs can be pinned; modes/source/version are not guessed.
    sql=f'''SELECT r.job_id,r.result_output_id,o.output_sha256
       FROM {table} r JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
       JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
       WHERE r.project_id=%s AND r.source_id=%s AND r.video_source_version_id=%s
         AND j.state IN ('completed','partial','unavailable') '''
    args=[pid,sid,ver]
    if kind=='prepare':sql+=" AND r.mode='prepare' AND o.schema_version='vision-prepare/v1'"
    elif kind=='analyze':sql+=" AND r.mode='analyze' AND o.schema_version='visual-observations/v1'"
    else:sql+=" AND o.schema_version='transcript/v1'"
    if jobid:sql+=' AND r.job_id=%s';args.append(jobid)
    if parent:sql+=' AND r.parent_prepare_job_id=%s';args.append(parent)
    sql+=' ORDER BY r.created_at DESC,r.job_id DESC LIMIT 1'
    result=conn.execute(sql,args).fetchone()
    if jobid and not result:raise FusionProblem('Выбранный вход отсутствует или имеет другую версию видео.','input_mismatch',409)
    return result


def enqueue(pid,sid,body):
    _pid(pid);sid=_uuid(sid,'source_id')
    allowed={'acquisition_job_id','transcript_job_id','prepare_job_id','vision_job_id',
             'include_transcript','include_vision','include_vision_ai'}
    if not isinstance(body,dict) or set(body)-allowed:
        raise FusionProblem('Недопустимые поля fusion.','invalid_body',400)
    flags={key:body.get(key,True) for key in ('include_transcript','include_vision','include_vision_ai')}
    if any(type(value) is not bool for value in flags.values()):
        raise FusionProblem('Режимы должны быть boolean.','invalid_body',400)
    if not flags['include_vision'] and flags['include_vision_ai']:
        flags['include_vision_ai']=False
    for key in ('transcript_job_id','prepare_job_id','vision_job_id'):
        if key in body and not flags['include_transcript' if key=='transcript_job_id' else 'include_vision_ai' if key=='vision_job_id' else 'include_vision']:
            raise FusionProblem('Выбранный job исключён настройками.','invalid_body',400)
    ids={x:(_uuid(body[x],x) if body.get(x) is not None else None)
         for x in ('acquisition_job_id','transcript_job_id','prepare_job_id','vision_job_id')}
    with _conn() as conn:
        _project_source(conn,pid,sid,lock=True)
        acq=_acquisition(conn,pid,sid,ids['acquisition_job_id'])
        a,ver,ao,manifest,adigest=acq
        tr=_input(conn,'project_transcript_runs','transcript',pid,sid,ver,ids['transcript_job_id']) if flags['include_transcript'] else None
        vp=_input(conn,'project_vision_runs','prepare',pid,sid,ver,ids['prepare_job_id']) if flags['include_vision'] else None
        va=_input(conn,'project_vision_runs','analyze',pid,sid,ver,ids['vision_job_id'],vp[0]) if vp and flags['include_vision_ai'] else None
        if ids['vision_job_id'] and not va:raise FusionProblem('Для Vision AI не найдена подготовка.','missing_preparation',409)
        if not tr and not vp:raise FusionProblem('Сначала получи ASR или Vision подготовку.','missing_modalities',409)
        pinned={'acquisition':[str(a),str(ao),adigest],
                'transcript':[str(tr[0]),str(tr[1]),tr[2]] if tr else None,
                'prepare':[str(vp[0]),str(vp[1]),vp[2]] if vp else None,
                'vision':[str(va[0]),str(va[1]),va[2]] if va else None}
        digest=sha(canonical({'version':ENGINE_VERSION,'video_version':str(ver),'inputs':pinned}))
        exists=conn.execute('''SELECT r.job_id,j.state FROM project_video_fusion_runs r
            JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
            WHERE r.project_id=%s AND r.source_id=%s AND r.video_source_version_id=%s
              AND r.config_sha256=%s''',(pid,sid,ver,digest)).fetchone()
        if exists:return {'job_id':str(exists[0]),'state':exists[1],'created':False}
        busy=conn.execute("SELECT 1 FROM source_extraction_jobs WHERE project_id=%s AND source_id=%s AND stage='timeline_fusion' AND state IN ('queued','processing')",(pid,sid)).fetchone()
        if busy:raise FusionProblem('У источника уже собирается таймлайн.','busy',409)
        job=conn.execute('''INSERT INTO source_extraction_jobs
           (project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts)
           VALUES(%s,%s,%s,'timeline_fusion','queued',%s,3) RETURNING id''',
           (pid,sid,ver,'fusion:'+digest)).fetchone()[0]
        conn.execute('''INSERT INTO project_video_fusion_runs
             (project_id,job_id,source_id,video_source_version_id,acquisition_job_id,acquisition_output_id,
              transcript_job_id,transcript_output_id,prepare_job_id,prepare_output_id,vision_job_id,vision_output_id,
              config_sha256,engine_version)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (pid,job,sid,ver,a,ao,tr[0] if tr else None,tr[1] if tr else None,
             vp[0] if vp else None,vp[1] if vp else None,va[0] if va else None,
             va[1] if va else None,digest,ENGINE_VERSION))
    return {'job_id':str(job),'state':'queued','created':True,'video_source_version_id':str(ver),
            'included':{'transcript':bool(tr),'visual_preparation':bool(vp),'vision_ai':bool(va)}}


def list_jobs(pid,sid):
    _pid(pid);sid=_uuid(sid,'source_id')
    with _conn() as conn:
        _project_source(conn,pid,sid)
        rows=conn.execute('''SELECT r.job_id,j.state,j.attempts,j.error_code,r.video_source_version_id,
              r.created_at,r.finished_at,r.result_output_id
              FROM project_video_fusion_runs r JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
              WHERE r.project_id=%s AND r.source_id=%s ORDER BY r.created_at DESC LIMIT 100''',(pid,sid)).fetchall()
    return {'jobs':[{'job_id':str(a),'state':b,'attempts':c,'error_code':d,
             'video_source_version_id':str(e),'created_at':f.isoformat(),
             'finished_at':g.isoformat() if g else None,'ready':h is not None} for a,b,c,d,e,f,g,h in rows]}


def result(pid,sid,jid,*,offset=0,limit=50,export=False):
    _pid(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=100000 or not 1<=limit<=MAX_PAGE:
        raise FusionProblem('Неверная пагинация.','invalid_pagination',400)
    with _conn() as conn:
        row=conn.execute('''SELECT j.state,j.error_code,j.attempts,r.video_source_version_id,o.output,o.output_sha256
          FROM project_video_fusion_runs r JOIN source_extraction_jobs j
          ON j.project_id=r.project_id AND j.id=r.job_id
          LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
          WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s''',(pid,sid,jid)).fetchone()
    if not row:raise FusionProblem('Задание отсутствует.','not_found',404)
    state,error,attempts,ver,data,checksum=row
    res={'job_id':jid,'state':state,'error_code':error,'attempts':attempts,
         'video_source_version_id':str(ver),'result_sha256':checksum}
    if data is not None:
        if sha(canonical(data))!=checksum:raise FusionProblem('Fusion результат повреждён.','output_integrity',409)
        if export:res['intelligence']=data
        else:
            ts=data['timeline']
            res['intelligence']={k:v for k,v in data.items() if k not in ('timeline','evidence')}
            res['intelligence'].update(timeline=ts[offset:offset+limit],
                timeline_count=len(ts),offset=offset,has_more=offset+limit<len(ts),
                evidence={key:value for key,value in data['evidence'].items() if key in
                    {ref for t in ts[offset:offset+limit] for ref in
                        t['speech_evidence_ids']+t['ocr_evidence_ids']+t['vision_evidence_ids']}})
    return res


def retry(pid,sid,jid):
    _pid(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    with _conn() as conn:
        row=conn.execute('''SELECT j.state,j.attempts,j.max_attempts FROM project_video_fusion_runs r
          JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s FOR UPDATE OF j''',(pid,sid,jid)).fetchone()
        if not row:raise FusionProblem('Задание отсутствует.','not_found',404)
        state,attempts,cap=row
        if state not in ('failed','unavailable') or attempts>=cap:
            raise FusionProblem('Повтор недоступен.','retry_unavailable',409)
        other=conn.execute("SELECT 1 FROM source_extraction_jobs WHERE project_id=%s AND source_id=%s AND stage='timeline_fusion' AND state IN ('queued','processing') AND id<>%s",(pid,sid,jid)).fetchone()
        if other:raise FusionProblem('Источник уже обрабатывается.','busy',409)
        conn.execute("UPDATE source_extraction_jobs SET state='queued',lease_until=NULL,error_code=NULL,error_message=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s",(pid,jid))
    return {'job_id':jid,'state':'queued'}


def _claim(conn):
    row=conn.execute('''SELECT j.id,j.project_id,j.source_id,j.source_version_id,j.attempts,
        r.acquisition_job_id,r.acquisition_output_id,r.transcript_job_id,r.transcript_output_id,
        r.prepare_job_id,r.prepare_output_id,r.vision_job_id,r.vision_output_id
      FROM source_extraction_jobs j JOIN project_video_fusion_runs r
      ON r.project_id=j.project_id AND r.job_id=j.id
      WHERE j.stage='timeline_fusion' AND j.attempts<j.max_attempts AND
        (j.state='queued' OR (j.state='processing' AND j.lease_until<clock_timestamp()))
      ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''').fetchone()
    if not row:return None
    jid,pid,sid,ver,attempts,aj,ao,tj,to,pj,po,vj,vo=row
    count=attempts+1
    conn.execute("UPDATE source_extraction_jobs SET state='processing',attempts=%s,lease_until=clock_timestamp()+make_interval(secs=>%s),updated_at=clock_timestamp(),error_code=NULL,error_message=NULL WHERE id=%s",(count,LEASE,jid))
    return {'job_id':jid,'project_id':pid,'source_id':sid,'version_id':ver,'attempt':count,
            'acquisition':[aj,ao],'transcript':[tj,to] if tj else None,
            'prepare':[pj,po] if pj else None,'vision':[vj,vo] if vj else None}


def _read_input(conn,job,role,pair):
    if pair is None:return None,None
    jid,oid=pair
    row=conn.execute('''SELECT o.output,o.output_sha256,o.schema_version,o.source_version_id
         FROM source_processing_outputs o WHERE o.project_id=%s AND o.source_id=%s
         AND o.id=%s AND (o.extraction_job_id=%s OR %s='acquisition')''',
         (job['project_id'],job['source_id'],oid,jid,role)).fetchone()
    expected={'acquisition':'video-acquisition/v1','transcript':'transcript/v1',
              'prepare':'vision-prepare/v1','vision':'visual-observations/v1'}[role]
    if not row or row[2]!=expected or str(row[3])!=str(job['version_id']):
        raise FusionProblem('Исходный '+role+' поменялся или отсутствует.','input_mismatch')
    data,digest=row[0],row[1]
    if not isinstance(data,dict) or sha(canonical(data))!=digest:
        raise FusionProblem('Нарушена целостность '+role+'.','input_integrity')
    return data,{'job_id':str(jid),'output_id':str(oid),'output_sha256':digest,'schema_version':expected}


def run_one(*,builder=fuse):
    """Process one queued task, deterministic and without any model/media I/O."""
    with _conn() as conn:job=_claim(conn)
    if not job:return None
    failure=None;output=None
    try:
        with _conn() as conn:
            inputs={role:_read_input(conn,job,role,job[role]) for role in ('acquisition','transcript','prepare','vision')}
            # Revalidate version and exact relationship of all pinned SQL output
            # records. The builder does not load archives or make external calls.
            acq=inputs['acquisition'][0]
            if acq.get('source_version_id')!=str(job['version_id']):
                raise FusionProblem('Manifest MP4 относится к другой версии.','video_version_mismatch')
            costs=[]
            for pair in (job['transcript'],job['prepare'],job['vision']):
                if pair:
                    amount=conn.execute('SELECT cost_actual_usd FROM source_extraction_jobs WHERE project_id=%s AND id=%s',(job['project_id'],pair[0])).fetchone()
                    if amount and amount[0] is not None:costs.append(str(amount[0]))
        output=builder(acquisition=inputs['acquisition'][0],project_id=job['project_id'],
              source_id=str(job['source_id']),source_version_id=str(job['version_id']),
              acquisition_job_id=str(job['acquisition'][0]),
              transcript=inputs['transcript'][0],transcript_job_id=str(job['transcript'][0]) if job['transcript'] else None,
              prepared=inputs['prepare'][0],prepare_job_id=str(job['prepare'][0]) if job['prepare'] else None,
              observations=inputs['vision'][0],vision_job_id=str(job['vision'][0]) if job['vision'] else None,
              input_provenance={role:value[1] for role,value in inputs.items() if value[1] is not None},
              cost_summary={'fusion_usd':0,'upstream_reported_actual_usd':[x for x in costs],
                            'upstream_cost_total_is_known':False})
        if output.get('schema_version')!='video-intelligence/v1':
            raise FusionProblem('Неверная схема fusion.','invalid_output')
    except FusionProblem as exc:
        failure=(exc.code,str(exc)[:400])
    except BaseException:
        failure=('fusion_failed','Не удалось объединить источники.')
    with _conn() as conn:
        state=conn.execute('SELECT state,attempts FROM source_extraction_jobs WHERE project_id=%s AND id=%s FOR UPDATE',(job['project_id'],job['job_id'])).fetchone()
        if state!=('processing',job['attempt']):return {'job_id':str(job['job_id']),'state':'stale'}
        if failure:
            stage='unavailable' if failure[0] in ('input_mismatch','input_integrity','video_version_mismatch') else 'failed'
            conn.execute("UPDATE source_extraction_jobs SET state=%s,error_code=%s,error_message=%s,lease_until=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s",(stage,failure[0],failure[1],job['project_id'],job['job_id']))
        else:
            from psycopg.types.json import Jsonb
            digest=sha(canonical(output))
            oid=conn.execute('''INSERT INTO source_processing_outputs
              (project_id,source_id,source_version_id,extraction_job_id,
               output_type,schema_version,output,output_sha256)
              VALUES(%s,%s,%s,%s,'video_analysis','video-intelligence/v1',%s,%s) RETURNING id''',
              (job['project_id'],job['source_id'],job['version_id'],job['job_id'],Jsonb(output),digest)).fetchone()[0]
            stage=output['processing_status']
            conn.execute("UPDATE source_extraction_jobs SET state=%s,output_ref=%s,lease_until=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s",(stage,str(oid),job['project_id'],job['job_id']))
            conn.execute('UPDATE project_video_fusion_runs SET result_output_id=%s,finished_at=clock_timestamp() WHERE project_id=%s AND job_id=%s',(oid,job['project_id'],job['job_id']))
    return {'job_id':str(job['job_id']),'state':stage,'source_version_id':str(job['version_id'])}


def main():
    p=argparse.ArgumentParser(description='Deterministic PSI Stage09 timeline fusion, no paid/remote calls')
    p.add_argument('--once',action='store_true');p.add_argument('--poll',type=float,default=2)
    a=p.parse_args()
    if not 0.2<=a.poll<=60:p.error('poll interval: 0.2..60')
    while True:
        item=run_one()
        if item:print(json.dumps(item,ensure_ascii=False),flush=True)
        if a.once:break
        if item is None:time.sleep(a.poll)

if __name__=='__main__':main()
