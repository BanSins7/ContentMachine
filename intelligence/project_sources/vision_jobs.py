"""Stage 08: pinned scene/OCR checkpoint + consent-gated visual Sonnet 4.6.

HTTP never touches media or calls a provider. One local durable job prepares
sampled frames. A second explicit and quoted job may send ONLY those frames.
Uncertain paid requests are never retried automatically or silently.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import re
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from . import vision_engine as eng
from .video_adapters import AcquisitionProblem,adapter_for

PREP_TIMEOUT=240
PREP_LEASE=300
AI_LEASE=180
MAX_RESULT=100


def _pid(pid):
    from .video_jobs import _pid
    try:return _pid(pid)
    except AcquisitionProblem as e:raise eng.VisionProblem(str(e),e.code,e.http_status) from e

def _conn():
    from .video_jobs import _conn
    try:return _conn()
    except AcquisitionProblem as e:raise eng.VisionProblem(str(e),e.code,e.http_status) from e

def _uuid(value,label='id'):
    try:return str(UUID(str(value)))
    except (ValueError,TypeError) as e:raise eng.VisionProblem('Некорректный '+label,'invalid_id',404) from e

def _source(conn,pid,sid):
    row=conn.execute('SELECT id FROM project_sources WHERE project_id=%s AND id=%s FOR UPDATE',(pid,sid)).fetchone()
    if row is None:raise eng.VisionProblem('Источник не найден.','not_found',404)

def _acquisition(conn,pid,sid,aid=None):
    query='''SELECT r.job_id,r.result_source_version_id,o.output
      FROM project_video_acquisition_runs r
      JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
      JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
      WHERE r.project_id=%s AND r.source_id=%s AND j.state IN ('completed','partial')
        AND r.result_source_version_id IS NOT NULL AND o.schema_version='video-acquisition/v1' '''
    args=[pid,sid]
    if aid:query+=' AND r.job_id=%s';args.append(aid)
    query+=' ORDER BY r.finished_at DESC NULLS LAST,r.created_at DESC LIMIT 1'
    row=conn.execute(query,args).fetchone()
    if not row:raise eng.VisionProblem('Нет подтверждённого MP4 этапа 06.','no_video',409)
    if not isinstance(row[2],dict) or not row[2].get('acquisition_verified'):
        if not isinstance(row[2],dict) or row[2].get('trust')!='verified_local_asset':
            raise eng.VisionProblem('Видео не подтверждено.','unverified',409)
    return row

def _active(conn,pid,sid):
    return conn.execute("""SELECT id FROM source_extraction_jobs WHERE project_id=%s AND source_id=%s
       AND stage='vision' AND state IN ('queued','processing') LIMIT 1""",(pid,sid)).fetchone()


def enqueue_prepare(pid,sid,body):
    _pid(pid);sid=_uuid(sid,'source_id')
    if not isinstance(body,dict) or set(body)-{'config','acquisition_job_id'}:
        raise eng.VisionProblem('Недопустимые поля.','invalid_body',400)
    conf=eng.settings(body.get('config') or {})
    acquisition_id=_uuid(body['acquisition_job_id'],'acquisition_job_id') if body.get('acquisition_job_id') else None
    digest=eng.sha(eng.canonical({'config':conf,'engine':eng.VERSION}))
    from psycopg.types.json import Jsonb
    with _conn() as conn:
        _source(conn,pid,sid)
        aid,version,_=_acquisition(conn,pid,sid,acquisition_id)
        existing=conn.execute('''SELECT r.job_id,j.state FROM project_vision_runs r
             JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
             WHERE r.project_id=%s AND r.source_id=%s AND r.video_source_version_id=%s
               AND r.mode='prepare' AND r.config_sha256=%s''',(pid,sid,version,digest)).fetchone()
        if existing:return {'job_id':str(existing[0]),'state':existing[1],'created':False,'mode':'prepare'}
        if _active(conn,pid,sid):raise eng.VisionProblem('Источник уже обрабатывается.','busy',409)
        job=conn.execute('''INSERT INTO source_extraction_jobs
             (project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts)
             VALUES(%s,%s,%s,'vision','queued',%s,3) RETURNING id''',
             (pid,sid,version,'vision:prepare:'+str(version)+':'+digest[:28])).fetchone()[0]
        conn.execute('''INSERT INTO project_vision_runs
             (project_id,job_id,source_id,video_source_version_id,acquisition_job_id,mode,config,config_sha256)
             VALUES(%s,%s,%s,%s,%s,'prepare',%s,%s)''',
             (pid,job,sid,version,aid,Jsonb(conf),digest))
    return {'job_id':str(job),'state':'queued','created':True,'mode':'prepare'}


def _prepared(conn,pid,sid,job_id):
    row=conn.execute('''SELECT r.video_source_version_id,r.acquisition_job_id,o.output,j.state
       FROM project_vision_runs r JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
       LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
       WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s AND r.mode='prepare' ''',
       (pid,sid,job_id)).fetchone()
    if not row or row[3] not in ('completed','partial') or not row[2] or row[2].get('schema_version')!=eng.VERSION:
        raise eng.VisionProblem('Сначала подготовь кадры и OCR.','not_prepared',409)
    return row


def quote(pid,sid,body):
    _pid(pid);sid=_uuid(sid,'source_id')
    if not isinstance(body,dict) or set(body)!={'prepare_job_id'}:
        raise eng.VisionProblem('Требуется prepare_job_id.','invalid_body',400)
    prep=_uuid(body['prepare_job_id'],'prepare_job_id')
    with _conn() as conn:
        _source(conn,pid,sid)
        version,aid,payload,_=_prepared(conn,pid,sid,prep)
        n=len(payload.get('frames',[]))
        if n<1 or n>eng.MAX_FRAMES:raise eng.VisionProblem('Недопустимая выборка кадров.','invalid_frames',409)
        result=conn.execute('''INSERT INTO project_vision_quotes
          (project_id,source_id,video_source_version_id,prepare_job_id,model,estimate_usd,frame_count,expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '20 minutes')
          RETURNING id,expires_at''',(pid,sid,version,prep,eng.MODEL,eng.VISION_ESTIMATE_USD,n)).fetchone()
    return {'quote_id':str(result[0]),'expires_at':result[1].isoformat(),
            'estimate_usd':eng.VISION_ESTIMATE_USD,'model':eng.MODEL,'frame_count':n,
            'price_is_guaranteed_cap':False,'requires_confirmation':True}


def enqueue_analysis(pid,sid,body):
    _pid(pid);sid=_uuid(sid,'source_id')
    if not isinstance(body,dict) or set(body)!={'quote_id','approved_estimate_usd'}:
        raise eng.VisionProblem('Нужны quote_id и approved_estimate_usd.','invalid_body',400)
    qid=_uuid(body['quote_id'],'quote_id')
    try:approved=Decimal(str(body['approved_estimate_usd']))
    except (ValueError,TypeError):raise eng.VisionProblem('Некорректное подтверждение суммы.','invalid_cost',400)
    if not approved.is_finite():raise eng.VisionProblem('Некорректная сумма.','invalid_cost',400)
    from psycopg.types.json import Jsonb
    with _conn() as conn:
        _source(conn,pid,sid)
        q=conn.execute('''SELECT source_id,video_source_version_id,prepare_job_id,estimate_usd,expires_at,
              clock_timestamp()<expires_at FROM project_vision_quotes
              WHERE project_id=%s AND source_id=%s AND id=%s''',(pid,sid,qid)).fetchone()
        if not q:raise eng.VisionProblem('Смета не найдена.','not_found',404)
        if approved!=q[3]:raise eng.VisionProblem('Подтверждённая сумма отличается от сметы.','cost_changed',409)
        existing=conn.execute('''SELECT r.job_id,j.state FROM project_vision_runs r
          JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          WHERE r.project_id=%s AND r.quote_id=%s''',(pid,qid)).fetchone()
        if existing:return {'job_id':str(existing[0]),'state':existing[1],'created':False,'mode':'analyze'}
        if not q[5]:raise eng.VisionProblem('Срок сметы истёк.','quote_expired',409)
        version,aid,_,_=_prepared(conn,pid,sid,q[2])
        if version!=q[1]:raise eng.VisionProblem('Версия источника изменилась.','version_changed',409)
        if _active(conn,pid,sid):raise eng.VisionProblem('Источник занят.','busy',409)
        consent='vision:'+qid
        idem='vision:ai:'+qid
        jid=conn.execute('''INSERT INTO source_extraction_jobs
           (project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts,
            cost_estimate_usd,consent_ref,charge_state)
           VALUES(%s,%s,%s,'vision','queued',%s,1,%s,%s,'reserved') RETURNING id''',
           (pid,sid,version,idem,approved,consent)).fetchone()[0]
        conf={'model':eng.MODEL,'prompt_version':eng.PROMPT_VERSION,'prepare_job_id':str(q[2])}
        conn.execute('''INSERT INTO project_vision_runs
            (project_id,job_id,source_id,video_source_version_id,acquisition_job_id,
             mode,config,config_sha256,parent_prepare_job_id,quote_id)
            VALUES(%s,%s,%s,%s,%s,'analyze',%s,%s,%s,%s)''',
            (pid,jid,sid,version,aid,Jsonb(conf),eng.sha(eng.canonical(conf)),q[2],qid))
    return {'job_id':str(jid),'state':'queued','created':True,'mode':'analyze'}


def list_jobs(pid,sid):
    _pid(pid);sid=_uuid(sid,'source_id')
    with _conn() as conn:
        _source(conn,pid,sid)
        rows=conn.execute('''SELECT r.job_id,j.state,j.attempts,j.error_code,r.mode,
             r.video_source_version_id,r.created_at,j.charge_state,j.cost_actual_usd
             FROM project_vision_runs r JOIN source_extraction_jobs j
               ON j.project_id=r.project_id AND j.id=r.job_id
             WHERE r.project_id=%s AND r.source_id=%s
             ORDER BY r.created_at DESC,r.job_id DESC LIMIT 100''',(pid,sid)).fetchall()
    return {'jobs':[{'job_id':str(a),'state':b,'attempts':c,'error_code':d,'mode':e,
       'video_source_version_id':str(f),'created_at':g.isoformat(),
       'charge_state':h,'cost_actual_usd':str(i) if i is not None else None} for a,b,c,d,e,f,g,h,i in rows]}


def result(pid,sid,jid,offset=0,limit=20):
    _pid(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=10000 or not 1<=limit<=50:
        raise eng.VisionProblem('Неверная пагинация.','invalid_query',400)
    with _conn() as conn:
        row=conn.execute('''SELECT j.state,j.error_code,j.attempts,r.mode,j.charge_state,j.cost_actual_usd,
                o.output,o.output_sha256,r.video_source_version_id FROM project_vision_runs r
                JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
                LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
                WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s''',(pid,sid,jid)).fetchone()
    if not row:raise eng.VisionProblem('Задание не найдено.','not_found',404)
    state,code,attempts,mode,charge,cost,payload,digest,ver=row
    out={'job_id':jid,'state':state,'error_code':code,'attempts':attempts,
         'mode':mode,'charge_state':charge,'cost_actual_usd':str(cost) if cost is not None else None,
         'video_source_version_id':str(ver),'result_sha256':digest}
    if payload:
        field='frames' if mode=='prepare' else 'scenes'
        pieces=payload.get(field,[])
        out['result']={**payload,field:pieces[offset:offset+limit],
                       'total':len(pieces),'offset':offset,'has_more':offset+limit<len(pieces)}
    return out


def retry_prepare(pid,sid,jid):
    _pid(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    with _conn() as conn:
        row=conn.execute('''SELECT j.state,j.attempts,j.max_attempts,r.mode
           FROM project_vision_runs r JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
           WHERE r.project_id=%s AND r.source_id=%s AND j.id=%s FOR UPDATE OF j''',(pid,sid,jid)).fetchone()
        if not row:raise eng.VisionProblem('Задание отсутствует.','not_found',404)
        if row[3]!='prepare' or row[0] not in ('failed','unavailable') or row[1]>=row[2] or _active(conn,pid,sid):
            raise eng.VisionProblem('Повтор доступен только для локальной подготовки.','retry_unavailable',409)
        conn.execute("UPDATE source_extraction_jobs SET state='queued',error_code=NULL,error_message=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s",(jid,))
    return {'job_id':jid,'state':'queued'}


def _claim(conn):
    # Recover a charged/in-flight job without resending it: provider outcome unknown.
    conn.execute('''UPDATE source_extraction_jobs j SET state='needs_review',charge_state='unknown',
        error_code='provider_outcome_unknown',lease_until=NULL,updated_at=clock_timestamp()
        FROM project_vision_runs r WHERE j.project_id=r.project_id AND j.id=r.job_id
        AND r.mode='analyze' AND j.stage='vision' AND j.state='processing'
        AND j.lease_until<clock_timestamp()''')
    row=conn.execute('''SELECT j.id,j.project_id,j.source_id,j.source_version_id,j.attempts,
          r.mode,r.config,r.acquisition_job_id,r.parent_prepare_job_id,a.result_output_id
        FROM source_extraction_jobs j JOIN project_vision_runs r ON r.project_id=j.project_id AND r.job_id=j.id
        JOIN project_video_acquisition_runs a ON a.project_id=r.project_id AND a.job_id=r.acquisition_job_id
        WHERE j.stage='vision' AND j.attempts<j.max_attempts
          AND (j.state='queued' OR (j.state='processing' AND j.lease_until<clock_timestamp() AND r.mode='prepare'))
        ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''').fetchone()
    if not row:return None
    jid,pid,sid,ver,attempts,mode,config,aid,parent,output_id=row
    n=attempts+1
    conn.execute('''UPDATE source_extraction_jobs SET state='processing',attempts=%s,
        lease_until=clock_timestamp()+make_interval(secs=>%s),updated_at=clock_timestamp(),
        error_code=NULL,error_message=NULL WHERE id=%s''',
        (n,PREP_LEASE if mode=='prepare' else AI_LEASE,jid))
    conn.execute('UPDATE project_vision_runs SET started_at=clock_timestamp() WHERE job_id=%s AND project_id=%s',(jid,pid))
    return {'job_id':jid,'project_id':pid,'source_id':sid,'video_source_version_id':ver,'attempt':n,
            'mode':mode,'config':config,'acquisition_job_id':aid,'prepare_job_id':parent,
            'acquisition_output_id':output_id}


def _asset(job):
    with _conn() as conn:
        row=conn.execute('SELECT output FROM source_processing_outputs WHERE project_id=%s AND id=%s',
                         (job['project_id'],job['acquisition_output_id'])).fetchone()
    if not row or not isinstance(row[0],dict):raise eng.VisionProblem('Manifest видео отсутствует.','asset_missing')
    data=row[0]
    if data.get('source_version_id')!=str(job['video_source_version_id']):
        raise eng.VisionProblem('Неверная версия видео.','video_version_mismatch')
    adapter=adapter_for(data.get('platform'))
    ident=adapter.resolve_identity(data.get('canonical_url',''))
    if ident.native_id!=data.get('native_id'):
        raise eng.VisionProblem('Video ID не совпадает.','native_id_mismatch')
    actual=adapter.collect_outputs(ident)
    if (actual['video_sha256']!=data.get('video_sha256') or actual['byte_size']!=data.get('byte_size')):
        raise eng.VisionProblem('Исходный MP4 изменился.','video_hash_mismatch')
    return Path(actual['video_path']),data


def _frame_dir(pid,jid):
    from .file_library import _project_folder
    try:root=_project_folder(pid)
    except Exception as exc:raise eng.VisionProblem('Папка проекта недоступна.','project_storage_unavailable') from exc
    p=root/'vision_assets'/str(jid)
    if root.is_symlink() or (root/'vision_assets').is_symlink() or p.is_symlink():
        raise eng.VisionProblem('Недопустимый путь кадров.','unsafe_path')
    return root,p


def _save_frame(pid,jid,frame_id,jpeg):
    if not re.fullmatch(r'f[0-9]{3}',frame_id) or len(jpeg)>eng.MAX_FRAME_BYTES:
        raise eng.VisionProblem('Недопустимый кадр.','invalid_frame')
    root,folder=_frame_dir(pid,jid)
    folder.mkdir(parents=True,exist_ok=True)
    if folder.is_symlink():raise eng.VisionProblem('Недопустимый путь.','unsafe_path')
    name=f'{frame_id}_{eng.sha(jpeg)}.jpg'
    p=folder/name
    # Atomic writes, no replacement if same filename already holds different bytes.
    if p.exists():
        if p.is_symlink() or p.read_bytes()!=jpeg:raise eng.VisionProblem('Коллизия кадров.','frame_collision')
    else:
        fd,tmp=tempfile.mkstemp(prefix='.incoming-',dir=folder)
        try:
            with os.fdopen(fd,'wb') as f:f.write(jpeg);f.flush();os.fsync(f.fileno())
            os.replace(tmp,p)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return p.relative_to(root).as_posix()


def _frame_bytes(pid,prepared):
    root,_=_frame_dir(pid,'read')
    result=[]
    for f in prepared['frames']:
        ref=f.get('storage_ref','')
        if not isinstance(ref,str) or not re.fullmatch(r'vision_assets/[0-9a-f-]{36}/f[0-9]{3}_[a-f0-9]{64}\.jpg',ref):
            raise eng.VisionProblem('Недопустимый путь кадра.','frame_path')
        path=root/ref
        if (path.is_symlink() or any(p.is_symlink() for p in (path.parent,root/'vision_assets')) or
            not path.is_file() or not path.resolve().is_relative_to(root.resolve())):
            raise eng.VisionProblem('Кадр отсутствует или путь небезопасен.','frame_missing')
        jpeg=path.read_bytes()
        if eng.sha(jpeg)!=f['sha256'] or len(jpeg)!=f['byte_size']:
            raise eng.VisionProblem('SHA-256 кадра изменился.','frame_integrity')
        result.append((f['frame_id'],jpeg))
    return result


def _prepare_child(send,video,config,pid,jid):
    try:
        result=eng.prepare(Path(video),config,lambda fid,data:_save_frame(pid,jid,fid,data))
        send.send(('ok',result))
    except BaseException:
        try:send.send(('fail','local_preparation_failed'))
        except Exception:pass
    finally:send.close()


def isolated_prepare(video,config,pid,jid,timeout=PREP_TIMEOUT):
    ctx=mp.get_context('spawn');rx,tx=ctx.Pipe(duplex=False)
    p=ctx.Process(target=_prepare_child,args=(tx,str(video),config,pid,str(jid)),daemon=True)
    p.start();tx.close()
    try:
        if not rx.poll(timeout):raise eng.VisionProblem('Таймаут подготовки видео.','prepare_timeout')
        try:status,payload=rx.recv()
        except EOFError:raise eng.VisionProblem('FFmpeg завершился без результата.','prepare_failed')
        if status!='ok':raise eng.VisionProblem('Ошибка локальной обработки.','prepare_failed')
        return payload
    finally:
        rx.close();p.join(2)
        if p.is_alive():p.terminate();p.join(3)
        if p.is_alive():p.kill();p.join()


def _prepared_payload(job):
    with _conn() as conn:
        row=conn.execute('''SELECT o.output FROM project_vision_runs r
          JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
          WHERE r.project_id=%s AND r.job_id=%s AND r.video_source_version_id=%s AND r.mode='prepare' ''',
          (job['project_id'],job['prepare_job_id'],job['video_source_version_id'])).fetchone()
    if not row or not isinstance(row[0],dict):raise eng.VisionProblem('Подготовка недоступна.','not_prepared')
    return row[0]


def run_one(*,local_runner=isolated_prepare,remote_runner=eng.openrouter_vision,key_provider=None):
    """At most one task, injected runners for tests. No external calls in CI."""
    with _conn() as conn:
        job=_claim(conn)
    if not job:return None
    pid,jid,sid,ver=job['project_id'],job['job_id'],job['source_id'],job['video_source_version_id']
    response=None;failure=None;cost=None;request_id=None;unknown=False
    try:
        video,asset=_asset(job)
        if job['mode']=='prepare':
            response=local_runner(video,job['config'],pid,jid)
            if response.get('schema_version')!=eng.VERSION or not response.get('frames'):
                raise eng.VisionProblem('Локальный результат невалиден.','invalid_prepare')
            response={**response,'video_sha256':asset['video_sha256'],
                'video_source_version_id':str(ver),'acquisition_job_id':str(job['acquisition_job_id'])}
        else:
            prepared=_prepared_payload(job)
            if prepared.get('video_sha256')!=asset['video_sha256']:
                raise eng.VisionProblem('SHA подготовленного видео не совпадает.','video_hash_mismatch')
            frames=_frame_bytes(pid,prepared)
            if key_provider is None:
                from worker.openrouter_transcribe import get_key
                key_provider=lambda:get_key(Path(__file__).resolve().parents[2]/'worker')
            key=key_provider()
            if not key:raise eng.VisionProblem('Нет API-ключа OpenRouter.','missing_key')
            unknown=True  # once the provider call starts, failure may incur cost
            api=remote_runner(prepared,frames,key)
            response={**api['observations'],'video_sha256':asset['video_sha256'],
                'video_source_version_id':str(ver),'prepare_job_id':str(job['prepare_job_id']),
                'provider_usage':api.get('usage',{})}
            request_id=api.get('provider_request_id') or None
            cost=api.get('usage',{}).get('cost_usd')
    except eng.VisionProblem as exc:
        failure=(exc.code,str(exc)[:380]);unknown=unknown and job['mode']=='analyze'
    except BaseException:
        failure=('worker_failed','Обработка завершилась ошибкой.');unknown=unknown and job['mode']=='analyze'
    with _conn() as conn:
        state=conn.execute('''SELECT state,attempts FROM source_extraction_jobs
            WHERE project_id=%s AND id=%s FOR UPDATE''',(pid,jid)).fetchone()
        if state!=('processing',job['attempt']):return {'job_id':str(jid),'state':'stale'}
        if failure:
            code,message=failure
            status='needs_review' if unknown else 'unavailable' if code in ('video_hash_mismatch','frame_missing','missing_key') else 'failed'
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,error_code=%s,error_message=%s,
                lease_until=NULL,charge_state=%s,updated_at=clock_timestamp()
                WHERE id=%s''',(status,code[:120],message,
                  'unknown' if unknown else 'not_applicable',jid))
        else:
            from psycopg.types.json import Jsonb
            digest=eng.sha(eng.canonical(response))
            out=conn.execute('''INSERT INTO source_processing_outputs
                (project_id,source_id,source_version_id,extraction_job_id,output_type,schema_version,output,output_sha256)
                VALUES(%s,%s,%s,%s,'visual_scenes',%s,%s,%s) RETURNING id''',
                (pid,sid,ver,jid,eng.VERSION if job['mode']=='prepare' else eng.OBS_VERSION,
                 Jsonb(response),digest)).fetchone()[0]
            status=response['status']
            if job['mode']=='analyze':
                charge='charged' if cost is not None else 'unknown'
            else:charge='not_applicable'
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,output_ref=%s,
                cost_actual_usd=%s,charge_state=%s,lease_until=NULL,updated_at=clock_timestamp()
                WHERE id=%s''',(status,str(out),cost,charge,jid))
            conn.execute('''UPDATE project_vision_runs SET result_output_id=%s,
                provider_request_id=%s,finished_at=clock_timestamp()
                WHERE project_id=%s AND job_id=%s''',(out,request_id,pid,jid))
    return {'job_id':str(jid),'state':status,'mode':job['mode'],
            'cost_actual_usd':str(cost) if cost is not None else None}


def main():
    p=argparse.ArgumentParser(description='PSI stage 08 local vision and explicitly priced remote vision')
    p.add_argument('--once',action='store_true');p.add_argument('--poll',type=float,default=3)
    a=p.parse_args()
    if not 0.2<=a.poll<=60:p.error('--poll: 0.2..60 seconds')
    while True:
        out=run_one()
        if out:print(json.dumps(out),flush=True)
        if a.once:break
        if out is None:time.sleep(a.poll)

if __name__=='__main__':main()
