"""Stage 06 durable video acquisition (not D1 Discovery and not Stage 07 ASR).

One explicit registry-video request creates an idempotent PostgreSQL job pinned
at source_version. The existing downloaders are invoked in a killable spawn
process, their exact-ID and media verification stays authoritative. No paid ASR:
legacy workers are called with engine='local'. Sidecars are opportunistic.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import time
from uuid import UUID,uuid4
from .video_adapters import AcquisitionProblem, adapter_for
from .document_parser import canonical

LEASE_SEC=120
DOWNLOAD_TIMEOUT_SEC=900
PROCESS_DELAY=1.0
MAX_PREVIEW=40


def _pid(pid):
    from .repository import _project
    try:return _project(pid)
    except Exception as exc:
        from .repository import RegistryProblem
        if isinstance(exc,RegistryProblem):raise AcquisitionProblem(str(exc),'project_unavailable',exc.http_status) from exc
        raise


def _conn():
    from .repository import _connection,RegistryProblem
    try:return _connection()
    except RegistryProblem as exc:raise AcquisitionProblem(str(exc),'db_unavailable',exc.http_status) from exc


def _uuid(value):
    try:return str(UUID(str(value)))
    except (TypeError,ValueError) as exc:raise AcquisitionProblem('Некорректный идентификатор.','invalid_id',404) from exc


def _source(conn,pid,sid,lock=False):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT id,project_id,source_type,platform,platform_native_id,canonical_url,
          link_kind,active_version_id,tombstoned_at FROM project_sources
          WHERE project_id=%s AND id=%s'''+(' FOR UPDATE' if lock else ''),(pid,sid))
        s=cur.fetchone()
    if not s:raise AcquisitionProblem('Ссылка отсутствует в проекте.','not_found',404)
    if s['tombstoned_at'] is not None:raise AcquisitionProblem('Источник архивирован.','archived',409)
    if s['link_kind']!='video' or s['source_type'] not in ('user_video_reference','research_link','other'):
        raise AcquisitionProblem('Требуется ссылка на конкретный ролик.','not_video')
    if not s['active_version_id']:raise AcquisitionProblem('У источника нет активной версии.','missing_version',409)
    a=adapter_for(s['platform'])
    identity=a.validate_url(s['canonical_url'])
    if s['platform_native_id']!=identity.native_id:
        raise AcquisitionProblem('ID источника не совпадает с платформой.','wrong_id',409)
    return s,a,identity


def enqueue(pid,sid,data):
    _pid(pid);sid=_uuid(sid)
    if not isinstance(data,dict) or set(data)-{'refresh'} or type(data.get('refresh',False)) is not bool:
        raise AcquisitionProblem('Разрешено только поле refresh (boolean).','invalid_body',400)
    refresh=data.get('refresh',False)
    with _conn() as conn:
        s,a,ident=_source(conn,pid,sid,lock=True)
        if not refresh:
            old=conn.execute('''SELECT j.id,j.state,r.phase FROM project_video_acquisition_runs r
              JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
              WHERE r.project_id=%s AND r.source_id=%s
                AND j.state IN ('queued','processing','completed','partial')
              ORDER BY r.created_at DESC LIMIT 1''',(pid,sid)).fetchone()
            if old:return {'job_id':str(old[0]),'state':old[1],'phase':old[2],'created':False,'cache_reused':True}
        active=conn.execute('''SELECT j.id,j.state FROM source_extraction_jobs j
           WHERE j.project_id=%s AND j.source_id=%s AND j.stage='video_download'
           AND j.state IN ('queued','processing') LIMIT 1''',(pid,sid)).fetchone()
        if active:return {'job_id':str(active[0]),'state':active[1],'created':False,'cache_reused':False}
        key='vid:'+str(s['active_version_id'])+':'+uuid4().hex
        row=conn.execute('''INSERT INTO source_extraction_jobs
          (project_id,source_id,source_version_id,stage,idempotency_key,state,max_attempts)
          VALUES(%s,%s,%s,'video_download',%s,'queued',3) RETURNING id''',
          (pid,sid,s['active_version_id'],key)).fetchone()
        jid=row[0]
        conn.execute('''INSERT INTO project_video_acquisition_runs
          (project_id,job_id,source_id,input_source_version_id,platform,native_id,canonical_url)
          VALUES(%s,%s,%s,%s,%s,%s,%s)''',
          (pid,jid,sid,s['active_version_id'],ident.platform,ident.native_id,ident.canonical_url))
    return {'job_id':str(jid),'state':'queued','phase':'queued','created':True,'cache_reused':False}


def list_jobs(pid,sid):
    _pid(pid);sid=_uuid(sid)
    with _conn() as conn:
        _source(conn,pid,sid)
        rows=conn.execute('''SELECT r.job_id,j.state,j.attempts,j.error_code,r.platform,r.native_id,
         r.phase,r.progress,r.cache_reused,r.cancel_requested,r.created_at
         FROM project_video_acquisition_runs r JOIN source_extraction_jobs j
         ON j.project_id=r.project_id AND j.id=r.job_id
         WHERE r.project_id=%s AND r.source_id=%s
         ORDER BY r.created_at DESC LIMIT 80''',(pid,sid)).fetchall()
    return {'jobs':[{'job_id':str(a),'state':b,'attempts':c,'error_code':d,
        'platform':e,'native_id':f,'phase':g,'progress':float(h) if h is not None else None,
        'cache_reused':i,'cancel_requested':k,'created_at':t.isoformat()}
        for a,b,c,d,e,f,g,h,i,k,t in rows]}


def result(pid,sid,jid):
    _pid(pid);sid=_uuid(sid);jid=_uuid(jid)
    with _conn() as conn:
        row=conn.execute('''SELECT j.state,j.error_code,j.attempts,r.phase,r.progress,
             r.cache_reused,r.video_sha256,r.unavailable_reason,o.output
             FROM project_video_acquisition_runs r
             JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
             LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
             WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s''',(pid,sid,jid)).fetchone()
    if not row:raise AcquisitionProblem('Задание отсутствует.','not_found',404)
    state,error,attempts,phase,progress,cached,sha,reason,payload=row
    return {'job_id':jid,'state':state,'error_code':error,'attempts':attempts,
            'phase':phase,'progress':float(progress) if progress is not None else None,
            'cache_reused':cached,'video_sha256':sha,'unavailable_reason':reason,'result':payload}


def cancel(pid,sid,jid):
    _pid(pid);sid=_uuid(sid);jid=_uuid(jid)
    with _conn() as conn:
        row=conn.execute('''SELECT j.state FROM project_video_acquisition_runs r
          JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s FOR UPDATE OF j''',(pid,sid,jid)).fetchone()
        if not row:raise AcquisitionProblem('Задание отсутствует.','not_found',404)
        if row[0] not in ('queued','processing'):
            raise AcquisitionProblem('Задание уже завершено.','conflict',409)
        conn.execute('UPDATE project_video_acquisition_runs SET cancel_requested=TRUE WHERE project_id=%s AND job_id=%s',(pid,jid))
        if row[0]=='queued':
            conn.execute("UPDATE source_extraction_jobs SET state='cancelled',updated_at=clock_timestamp() WHERE id=%s",(jid,))
    return {'job_id':jid,'state':'cancelled' if row[0]=='queued' else 'cancelling'}


def retry(pid,sid,jid):
    _pid(pid);sid=_uuid(sid);jid=_uuid(jid)
    with _conn() as conn:
        row=conn.execute('''SELECT j.state,j.attempts,j.max_attempts FROM project_video_acquisition_runs r
         JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
         WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s FOR UPDATE OF j''',(pid,sid,jid)).fetchone()
        if not row:raise AcquisitionProblem('Задание отсутствует.','not_found',404)
        state,attempts,max_attempts=row
        if state not in ('failed','unavailable','cancelled') or attempts>=max_attempts:
            raise AcquisitionProblem('Повторная попытка недоступна.','conflict',409)
        conn.execute("UPDATE source_extraction_jobs SET state='queued',error_code=NULL,error_message=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s",(jid,))
        conn.execute("UPDATE project_video_acquisition_runs SET phase='queued',progress=NULL,cancel_requested=FALSE,unavailable_reason=NULL WHERE project_id=%s AND job_id=%s",(pid,jid))
    return {'job_id':jid,'state':'queued'}


def _claim(conn):
    rows=conn.execute('''SELECT j.id,j.project_id,j.source_id,j.source_version_id,j.attempts,
         r.platform,r.native_id,r.canonical_url,r.cancel_requested
         FROM source_extraction_jobs j JOIN project_video_acquisition_runs r
         ON r.project_id=j.project_id AND r.job_id=j.id
         WHERE j.stage='video_download' AND j.attempts<j.max_attempts
          AND (j.state='queued' OR (j.state='processing' AND j.lease_until<clock_timestamp()))
         ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 30''').fetchall()
    # Do not let one busy native video lock starve jobs from other platforms/IDs.
    for row in rows:
        jid,pid,sid,source_version,prior,platform,native,url,cancelled=row
        key='psi-video:'+platform+':'+native
        acquired=conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(key,)).fetchone()[0]
        if not acquired:
            continue
        if cancelled:
            conn.execute("UPDATE source_extraction_jobs SET state='cancelled',updated_at=clock_timestamp() WHERE id=%s",(jid,))
            conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))
            continue
        attempt=prior+1
        conn.execute('''UPDATE source_extraction_jobs SET state='processing',attempts=%s,
           lease_until=clock_timestamp()+make_interval(secs=>%s),updated_at=clock_timestamp()
           WHERE id=%s''',(attempt,LEASE_SEC,jid))
        conn.execute("UPDATE project_video_acquisition_runs SET phase='prepare',progress=NULL,started_at=clock_timestamp() WHERE project_id=%s AND job_id=%s",(pid,jid))
        return {'job_id':jid,'project_id':pid,'source_id':sid,'source_version_id':source_version,
                'attempt':attempt,'platform':platform,'native_id':native,'canonical_url':url,'lock_key':key}
    return None


def _download_child(send,job_id,platform,url):
    try:
        from worker import local_worker as w
        a=adapter_for(platform)
        ident=a.resolve_identity(url)
        # In-memory legacy status is scoped to this isolated child process.
        w.jobs[str(job_id)]={'status':'queued','progress':None,'phase':'prepare','cancel_requested':False}
        original=w.update_job
        def report(id,**kw):
            original(id,**kw)
            try:send.send({'kind':'progress','status':w.jobs[str(job_id)].get('status'),
                           'phase':w.jobs[str(job_id)].get('phase','prepare'),
                           'progress':w.jobs[str(job_id)].get('progress')})
            except (BrokenPipeError,EOFError,OSError):pass
        w.update_job=report
        a.request_download(str(job_id),ident)
        state=w.jobs[str(job_id)]
        send.send({'kind':'done','status':state.get('status'),
                   'error':str(state.get('error') or '')[:350]})
    except BaseException as exc:
        try:send.send({'kind':'done','status':'failed','error':str(exc)[:350]})
        except Exception:pass
    finally:send.close()


def _run_download(job,progress,cancelled,timeout=DOWNLOAD_TIMEOUT_SEC):
    ctx=mp.get_context('spawn')
    rx,tx=ctx.Pipe(duplex=False)
    p=ctx.Process(target=_download_child,args=(tx,str(job['job_id']),job['platform'],job['canonical_url']),daemon=False)
    p.start();tx.close();last={'status':'failed','error':'Обработчик завершился без результата.'}
    started=time.monotonic()
    try:
        while True:
            if cancelled():return {'status':'cancelled','error':'Отменено пользователем.'}
            if time.monotonic()-started>timeout:
                return {'status':'failed','error':'Превышен лимит времени загрузки.'}
            if rx.poll(PROCESS_DELAY):
                try:message=rx.recv()
                except EOFError:break
                if message.get('kind')=='progress':progress(message)
                elif message.get('kind')=='done':last=message;break
            if not p.is_alive() and not rx.poll():break
        return last
    finally:
        if p.is_alive():
            p.terminate();p.join(5)
            if p.is_alive():p.kill();p.join()
        else:p.join(2)
        rx.close()


def _persist_manifest(conn,job,asset,cached):
    """Append-only acquired source version; reattach same verified bytes, never move D1 IDs."""
    from psycopg.types.json import Jsonb
    pid,sid,source_version=job['project_id'],job['source_id'],job['source_version_id']
    sha=asset['video_sha256'];size=asset['byte_size']
    if len(asset['video_path'])>512 or any(len(v['path'])>512 for v in asset['attachments'].values()):
        raise AcquisitionProblem('Слишком длинный путь архивного файла.','unsafe_path',409)
    # Existing source version for same verified MP4 may be reused. New project
    # reference gets its own project_id/source_id provenance even if path shared.
    old=conn.execute('''SELECT sv.id,o.id,m.id FROM project_source_versions sv
      JOIN project_media_assets m ON m.project_id=sv.project_id AND m.source_version_id=sv.id AND m.asset_kind='video'
      JOIN source_processing_outputs o ON o.project_id=sv.project_id AND o.source_version_id=sv.id AND o.output_type='other'
      WHERE sv.project_id=%s AND sv.source_id=%s AND sv.content_sha256=%s
      ORDER BY sv.version_no DESC LIMIT 1''',(pid,sid,sha)).fetchone()
    if old:
        vid,output_id,asset_id=old
    else:
        number=conn.execute('''SELECT COALESCE(MAX(version_no),0)+1 FROM project_source_versions
          WHERE project_id=%s AND source_id=%s''',(pid,sid)).fetchone()[0]
        vid=conn.execute('''INSERT INTO project_source_versions
          (project_id,source_id,version_no,content_sha256,byte_size,media_type,
           fetched_or_uploaded_at,source_locator,immutable_asset_ref)
          VALUES (%s,%s,%s,%s,%s,'video/mp4',clock_timestamp(),%s,%s) RETURNING id''',
          (pid,sid,number,sha,size,Jsonb({'kind':'verified_local_video','platform':asset['platform'],
             'native_id':asset['native_id'],'source_url':asset['canonical_url']}),asset['video_path'])).fetchone()[0]
        asset_id=conn.execute('''INSERT INTO project_media_assets
           (project_id,source_id,source_version_id,asset_kind,content_sha256,byte_size,media_type,storage_ref)
           VALUES(%s,%s,%s,'video',%s,%s,'video/mp4',%s) RETURNING id''',
           (pid,sid,vid,sha,size,asset['video_path'])).fetchone()[0]
        for label,entry in asset['attachments'].items():
            kind={'subtitles':'subtitle','transcript':'other','metadata':'other'}[label]
            mime={'subtitles':'application/x-subrip','transcript':'text/plain','metadata':'application/json'}[label]
            conn.execute('''INSERT INTO project_media_assets
              (project_id,source_id,source_version_id,asset_kind,content_sha256,byte_size,media_type,storage_ref)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
              (pid,sid,vid,kind,entry['sha256'],entry['byte_size'],mime,entry['path']))
        payload={'schema_version':'video-acquisition/v1','trust':'verified_local_asset',
          'source_id':str(sid),'source_version_id':str(vid),
          'platform':asset['platform'],'native_id':asset['native_id'],
          'canonical_url':asset['canonical_url'],'video_sha256':sha,'byte_size':size,
          'video_path':asset['video_path'],'availability_status':'available',
          'attachments':asset['attachments'],'metadata':asset['metadata'],
          'cached_from_archive':cached,'downloader_family':'existing_manual_workers'}
        digest=hashlib.sha256(canonical(payload)).hexdigest()
        output_id=conn.execute('''INSERT INTO source_processing_outputs
          (project_id,source_id,source_version_id,output_type,schema_version,output,output_sha256)
          VALUES (%s,%s,%s,'other','video-acquisition/v1',%s,%s) RETURNING id''',
          (pid,sid,vid,Jsonb(payload),digest)).fetchone()[0]
    conn.execute('''UPDATE project_sources SET active_version_id=%s,updated_at=clock_timestamp()
      WHERE project_id=%s AND id=%s''',(vid,pid,sid))
    return vid,output_id,asset_id


def run_one(*,runner=_run_download):
    """One durable claim; runner injectable for offline QA (no real downloads)."""
    conn=_conn();locked=None
    try:
        job=_claim(conn)
        conn.commit()
        if not job:return None
        locked=job['lock_key']
        pid,jid,sid=job['project_id'],job['job_id'],job['source_id']
        a=adapter_for(job['platform']);identity=a.resolve_identity(job['canonical_url'])
        failure=None;cached=False;asset=None
        try:
            asset=a.collect_outputs(identity);cached=True
        except AcquisitionProblem:
            pass
        if asset is None:
            def progress(msg):
                with _conn() as c:
                    r=c.execute('''SELECT j.state,j.attempts,r.cancel_requested FROM source_extraction_jobs j
                     JOIN project_video_acquisition_runs r ON r.project_id=j.project_id AND r.job_id=j.id
                     WHERE j.id=%s FOR UPDATE OF j''',(jid,)).fetchone()
                    if not r or r[0]!='processing' or r[1]!=job['attempt']:return
                    phase=str(msg.get('phase') or 'download')[:40]
                    value=msg.get('progress')
                    if not isinstance(value,(int,float)) or not 0<=value<=100:value=None
                    c.execute('''UPDATE project_video_acquisition_runs SET phase=%s,progress=%s
                        WHERE project_id=%s AND job_id=%s''',(phase,value,pid,jid))
                    c.execute('''UPDATE source_extraction_jobs SET lease_until=clock_timestamp()+make_interval(secs=>%s)
                        WHERE id=%s''',(LEASE_SEC,jid))
            def check_cancel():
                with _conn() as c:
                    r=c.execute('''SELECT j.state,j.attempts,r.cancel_requested FROM source_extraction_jobs j
                        JOIN project_video_acquisition_runs r ON r.project_id=j.project_id AND r.job_id=j.id
                        WHERE j.id=%s''',(jid,)).fetchone()
                    # heartbeat on idle progress to avoid reclaims during slow remote downloads
                    if r and r[0]=='processing' and r[1]==job['attempt']:
                        c.execute('''UPDATE source_extraction_jobs SET lease_until=clock_timestamp()+make_interval(secs=>%s)
                                 WHERE id=%s''',(LEASE_SEC,jid))
                    return not r or r[0]!='processing' or r[1]!=job['attempt'] or r[2]
            outcome=runner(job,progress,check_cancel)
            if outcome.get('status')=='cancelled':
                failure=('cancelled',outcome.get('error') or 'Отменено')
            elif outcome.get('status') not in ('complete','completed'):
                failure=('unavailable',outcome.get('error') or 'Платформа не предоставила видео.')
            # Allow partial MP4 only if verified from existing official metadata.
            try:
                asset=a.collect_outputs(identity)
            except AcquisitionProblem as exc:
                if not failure:failure=(exc.code,str(exc))
        with _conn() as c:
            row=c.execute('''SELECT j.state,j.attempts,r.cancel_requested FROM source_extraction_jobs j
              JOIN project_video_acquisition_runs r ON r.project_id=j.project_id AND r.job_id=j.id
              WHERE j.id=%s FOR UPDATE OF j''',(jid,)).fetchone()
            if not row or row[0]!='processing' or row[1]!=job['attempt']:
                return {'job_id':str(jid),'state':'stale'}
            if row[2]:failure=('cancelled','Отменено пользователем.')
            if failure and (failure[0]=='cancelled' or asset is None):
                code,desc=failure
                state='cancelled' if code=='cancelled' else 'unavailable' if code in ('unavailable','asset_unavailable','missing_metadata','wrong_id','wrong_url') else 'failed'
                c.execute('''UPDATE source_extraction_jobs SET state=%s,error_code=%s,error_message=%s,
                  lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s''',(state,code[:120],str(desc)[:400],jid))
                c.execute('''UPDATE project_video_acquisition_runs SET phase=%s,unavailable_reason=%s,
                  finished_at=clock_timestamp() WHERE job_id=%s AND project_id=%s''',(state,str(desc)[:400],jid,pid))
            else:
                vid,out,asset_id=_persist_manifest(c,job,asset,cached)
                state='partial' if failure else 'completed'
                c.execute('''UPDATE project_video_acquisition_runs SET result_source_version_id=%s,
                  result_output_id=%s,video_asset_id=%s,video_sha256=%s,cache_reused=%s,
                  phase=%s,progress=100,finished_at=clock_timestamp(),unavailable_reason=%s
                  WHERE job_id=%s AND project_id=%s''',(vid,out,asset_id,asset['video_sha256'],cached,
                      'complete' if not failure else 'partial',str(failure[1])[:400] if failure else None,jid,pid))
                c.execute('''UPDATE source_extraction_jobs SET state=%s,lease_until=NULL,updated_at=clock_timestamp(),
                  output_ref=%s WHERE id=%s''',(state,str(out),jid))
        return {'job_id':str(jid),'state':state,'cache_reused':cached,'platform':job['platform']}
    finally:
        if locked:
            try:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(locked,));conn.commit()
            except Exception:pass
        conn.close()


def main():
    p=argparse.ArgumentParser(description='PSI stage 06: durable existing-downloader wrapper')
    p.add_argument('--once',action='store_true');p.add_argument('--poll',type=float,default=3)
    a=p.parse_args()
    if not 0.2<=a.poll<=60:p.error('poll interval must be 0.2..60s')
    while True:
        out=run_one()
        if out:print(json.dumps(out),flush=True)
        if a.once:break
        if out is None:time.sleep(a.poll)

if __name__=='__main__':main()


def preview_media(pid,sid,jid):
    """Read-only project/job-bound verified MP4 for Stage13 browser preview.

    No arbitrary local path is accepted from the request. The file must be
    registered as a project media asset under an adapter's expected archive
    root and match its immutable SHA/byte-size before any bytes are served.
    """
    from pathlib import Path
    from .video_adapters import adapter_for,_safe,_sha,MAX_VIDEO
    _pid(pid);sid=_uuid(sid);jid=_uuid(jid)
    with _conn() as conn:
        row=conn.execute("""SELECT j.state,s.platform,s.canonical_url,
              o.output,m.storage_ref,m.byte_size,m.content_sha256,m.media_type
           FROM project_video_acquisition_runs r
           JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
           JOIN project_sources s ON s.project_id=r.project_id AND s.id=r.source_id
           JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
           JOIN project_media_assets m ON m.project_id=o.project_id
             AND m.source_id=o.source_id AND m.source_version_id=o.source_version_id
             AND m.asset_kind='video'
           WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s""",
           (pid,sid,jid)).fetchone()
    if not row or row[0]!='completed':
        raise AcquisitionProblem('Подтверждённый MP4 ещё не готов.','asset_unavailable',409)
    _,platform,url,payload,ref,size,digest,media=row
    if (not isinstance(payload,dict) or payload.get('schema_version')!='video-acquisition/v1'
        or payload.get('video_sha256')!=digest or payload.get('byte_size')!=size
        or payload.get('video_path')!=ref or media!='video/mp4'):
        raise AcquisitionProblem('Привязка видео и хеша повреждена.','asset_integrity',409)
    adapter=adapter_for(platform)
    ident=adapter.resolve_identity(url)
    raw=Path(ref)
    valid=False
    for root,mp4,_ in adapter.candidate_paths(ident):
        if mp4.resolve()!=raw.resolve():continue
        _safe(Path(root),raw)
        valid=True;break
    if not valid:
        raise AcquisitionProblem('Видео вне зарегистрированного архива.','unsafe_path',409)
    try:
        actual_sha,actual_size=_sha(raw,MAX_VIDEO)
        if actual_sha!=digest or actual_size!=size:
            raise AcquisitionProblem('Видеофайл изменился после регистрации.','hash_mismatch',409)
        with raw.open('rb') as f:
            header=f.read(12)
        if len(header)<12 or header[4:8]!=b'ftyp':
            raise AcquisitionProblem('Неверный MP4 asset.','invalid_media',409)
    except OSError as exc:
        raise AcquisitionProblem('Архив видео недоступен.','asset_unavailable',404) from exc
    return raw,size,digest
