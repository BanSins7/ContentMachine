"""PSI stage 07: project-scoped durable, zero-cost transcript normalization.

A result is pinned to one verified Stage 06 acquisition version. The ordinary
path reads the recorded SRT/TXT sidecars, never calls a remote model. An
explicit separate mode runs *local* faster-whisper on the already verified MP4.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import re
import time
from pathlib import Path
from uuid import UUID

from .transcript_normalizer import (VERSION, TranscriptProblem, canonical,
                                    decode_text, language, normalize, MAX_SIDECAR)
from .video_adapters import AcquisitionProblem, adapter_for

ASR_TIMEOUT = 600
LEASE_SECONDS = 660
MAX_RESULTS = 150


def _project(pid):
    from .video_jobs import _pid
    try: return _pid(pid)
    except AcquisitionProblem as e:raise TranscriptProblem(str(e), e.code, e.http_status) from e


def _connection():
    from .video_jobs import _conn
    try: return _conn()
    except AcquisitionProblem as e:raise TranscriptProblem(str(e), e.code, e.http_status) from e


def _uuid(value, label):
    try:return str(UUID(str(value)))
    except (ValueError,TypeError) as exc:raise TranscriptProblem('Некорректный '+label,'invalid_id',404) from exc


def _settings(body):
    if not isinstance(body,dict) or set(body)-{'mode','language','acquisition_job_id'}:
        raise TranscriptProblem('Недопустимые параметры транскрибации.', 'invalid_settings',400)
    mode=body.get('mode','reuse_sidecar')
    if mode not in ('reuse_sidecar','local_asr'):
        raise TranscriptProblem('Допустимы reuse_sidecar и local_asr.', 'invalid_mode',400)
    lng=body.get('language','auto')
    if lng not in ('auto','ru','en'):
        raise TranscriptProblem('Язык: auto, ru или en.', 'invalid_language',400)
    return {'mode':mode,'language':lng}


def _source_exists(conn,pid,sid):
    row=conn.execute('SELECT id FROM project_sources WHERE project_id=%s AND id=%s',(pid,sid)).fetchone()
    if not row:raise TranscriptProblem('Источник отсутствует в проекте.','not_found',404)


def _acquisition(conn,pid,sid,aid=None):
    sql='''SELECT r.job_id,r.result_source_version_id,o.output
         FROM project_video_acquisition_runs r
         JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
         JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
         WHERE r.project_id=%s AND r.source_id=%s AND j.state IN ('completed','partial')
           AND r.result_source_version_id IS NOT NULL AND o.schema_version='video-acquisition/v1' '''
    params=[pid,sid]
    if aid:
        sql+=' AND r.job_id=%s';params.append(aid)
    sql+=' ORDER BY r.finished_at DESC NULLS LAST,r.created_at DESC LIMIT 1'
    row=conn.execute(sql,params).fetchone()
    if not row:raise TranscriptProblem('Нет подтверждённой версии MP4 после этапа 06.','no_video',409)
    return row


def enqueue(pid,sid,body):
    _project(pid);sid=_uuid(sid,'source_id')
    settings=_settings(body)
    aid=_uuid(body['acquisition_job_id'],'acquisition_job_id') if body.get('acquisition_job_id') else None
    config_hash=hashlib.sha256(canonical({'normalizer_version':VERSION, **settings})).hexdigest()
    from psycopg.types.json import Jsonb
    with _connection() as conn:
        # Serialize enqueue against the source, so a repeated click is idempotent.
        _source_exists(conn,pid,sid)
        conn.execute('SELECT id FROM project_sources WHERE project_id=%s AND id=%s FOR UPDATE',(pid,sid))
        acquisition,version,manifest=_acquisition(conn,pid,sid,aid)
        if not isinstance(manifest,dict) or not manifest.get('acquisition_verified',False):
            # Some older 06 manifests explicitly record only trust; verify below
            if not isinstance(manifest,dict) or manifest.get('trust')!='verified_local_asset':
                raise TranscriptProblem('Видео не подтверждено.','unverified_video',409)
        present=conn.execute('''SELECT r.job_id,j.state FROM project_transcript_runs r
           JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
           WHERE r.project_id=%s AND r.source_id=%s AND r.video_source_version_id=%s
             AND r.mode=%s AND r.config_sha256=%s''',
           (pid,sid,version,settings['mode'],config_hash)).fetchone()
        if present:return {'job_id':str(present[0]),'state':present[1],'created':False,'mode':settings['mode']}
        # One active transcription per project source; retries explicit.
        active=conn.execute('''SELECT id FROM source_extraction_jobs WHERE project_id=%s AND source_id=%s
             AND stage='asr' AND state IN ('queued','processing')''',(pid,sid)).fetchone()
        if active:raise TranscriptProblem('У источника уже выполняется транскрибация.','busy',409)
        idem='transcript:'+str(version)+':'+settings['mode']+':'+config_hash[:24]
        jid=conn.execute('''INSERT INTO source_extraction_jobs
           (project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts)
           VALUES (%s,%s,%s,'asr','queued',%s,3) RETURNING id''',
           (pid,sid,version,idem)).fetchone()[0]
        conn.execute('''INSERT INTO project_transcript_runs
           (project_id,job_id,source_id,video_source_version_id,acquisition_job_id,
           mode,normalizer_version,settings,config_sha256)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
           (pid,jid,sid,version,acquisition,settings['mode'],VERSION,Jsonb(settings),config_hash))
    return {'job_id':str(jid),'state':'queued','mode':settings['mode'],'created':True}


def list_jobs(pid,sid):
    _project(pid);sid=_uuid(sid,'source_id')
    with _connection() as conn:
        _source_exists(conn,pid,sid)
        rows=conn.execute('''SELECT r.job_id,j.state,j.attempts,j.error_code,r.mode,
             r.video_source_version_id,r.created_at
             FROM project_transcript_runs r JOIN source_extraction_jobs j
             ON j.project_id=r.project_id AND j.id=r.job_id
             WHERE r.project_id=%s AND r.source_id=%s
             ORDER BY r.created_at DESC,r.job_id DESC LIMIT %s''',(pid,sid,MAX_RESULTS)).fetchall()
    return {'jobs':[{'job_id':str(a),'state':b,'attempts':c,'error_code':d,
             'mode':e,'video_source_version_id':str(f),'created_at':g.isoformat()}
             for a,b,c,d,e,f,g in rows]}


def result(pid,sid,jid,*,offset=0,limit=50):
    _project(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=100000 or not 1<=limit<=100:
        raise TranscriptProblem('Некорректная пагинация.','invalid_pagination',400)
    with _connection() as conn:
        row=conn.execute('''SELECT j.state,j.error_code,j.attempts,r.mode,r.video_source_version_id,
                 o.output,o.output_sha256 FROM project_transcript_runs r
                 JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
                 LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
                 WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s''',(pid,sid,jid)).fetchone()
    if not row:raise TranscriptProblem('Задание отсутствует.','not_found',404)
    state,error,attempts,mode,version,payload,digest=row
    out={'job_id':jid,'state':state,'error_code':error,'attempts':attempts,
         'mode':mode,'video_source_version_id':str(version),'result_sha256':digest}
    if payload is not None:
        segments=payload.get('segments',[])
        out['transcript']={**payload,'segments':segments[offset:offset+limit],
                           'offset':offset,'has_more':offset+limit<len(segments),
                           'segment_count':len(segments)}
    return out


def retry(pid,sid,jid):
    _project(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    with _connection() as conn:
        row=conn.execute('''SELECT j.state,j.attempts,j.max_attempts FROM project_transcript_runs r
           JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
           WHERE r.project_id=%s AND r.source_id=%s AND j.id=%s FOR UPDATE OF j''',(pid,sid,jid)).fetchone()
        if not row:raise TranscriptProblem('Задание отсутствует.','not_found',404)
        state,tries,cap=row
        if state not in ('failed','unavailable') or tries>=cap:
            raise TranscriptProblem('Повтор недоступен.','conflict',409)
        # Prevent two queued ASRs for a single source even when retrying old runs.
        other=conn.execute('''SELECT id FROM source_extraction_jobs
          WHERE project_id=%s AND source_id=%s AND stage='asr'
            AND state IN ('queued','processing') AND id<>%s''',(pid,sid,jid)).fetchone()
        if other:raise TranscriptProblem('Источник уже обрабатывается.','busy',409)
        conn.execute('''UPDATE source_extraction_jobs SET state='queued',error_code=NULL,
            error_message=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s''',(jid,))
    return {'job_id':jid,'state':'queued'}


def _claim(conn):
    row=conn.execute('''SELECT j.id,j.project_id,j.source_id,j.source_version_id,j.attempts,
            r.mode,r.settings,r.acquisition_job_id,o.output
       FROM source_extraction_jobs j JOIN project_transcript_runs r
         ON r.project_id=j.project_id AND r.job_id=j.id
       JOIN project_video_acquisition_runs a ON a.project_id=r.project_id AND a.job_id=r.acquisition_job_id
       JOIN source_processing_outputs o ON o.project_id=a.project_id AND o.id=a.result_output_id
       WHERE j.stage='asr' AND j.attempts<j.max_attempts
         AND (j.state='queued' OR (j.state='processing' AND j.lease_until<clock_timestamp()))
       ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''').fetchone()
    if not row:return None
    jid,pid,sid,vid,attempts,mode,settings,acquisition,manifest=row
    n=attempts+1
    conn.execute('''UPDATE source_extraction_jobs SET state='processing',attempts=%s,
          lease_until=clock_timestamp()+make_interval(secs=>%s),
          updated_at=clock_timestamp(),error_code=NULL,error_message=NULL WHERE id=%s''',
          (n,LEASE_SECONDS,jid))
    return {'job_id':jid,'project_id':pid,'source_id':sid,'video_source_version_id':vid,
            'attempt':n,'mode':mode,'settings':settings,'acquisition_job_id':acquisition,'manifest':manifest}


def _match_manifest(job):
    manifest=job['manifest']
    if not isinstance(manifest,dict) or manifest.get('source_version_id')!=str(job['video_source_version_id']):
        raise TranscriptProblem('Manifest относится к другой версии.','source_version_mismatch',409)
    platform=manifest.get('platform')
    adapter=adapter_for(platform)
    identity=adapter.resolve_identity(manifest.get('canonical_url',''))
    if manifest.get('native_id')!=identity.native_id:
        raise TranscriptProblem('ID видео не совпадает.','native_id_mismatch',409)
    actual=adapter.collect_outputs(identity)  # existing strict source-url, MP4 and metadata checks
    if actual['video_sha256']!=manifest.get('video_sha256') or actual['byte_size']!=manifest.get('byte_size'):
        raise TranscriptProblem('Подменена версия MP4. Требуется новое получение видео.','video_hash_mismatch',409)
    saved=manifest.get('attachments',{})
    if not isinstance(saved,dict):raise TranscriptProblem('Некорректный manifest вложений.','invalid_manifest')
    for label in ('subtitles','transcript','metadata'):
        original=saved.get(label)
        new=actual['attachments'].get(label)
        if (original is None)!=(new is None):
            raise TranscriptProblem('Изменился состав файлов расшифровки.','sidecar_changed',409)
        if original is not None and (new['sha256']!=original.get('sha256') or
             new['byte_size']!=original.get('byte_size') or new['path']!=original.get('path')):
            raise TranscriptProblem('Изменился файл расшифровки.','sidecar_changed',409)
    return manifest,actual


def _read_sidecar(attachment):
    if attachment is None:return None
    if attachment.get('byte_size',0)>MAX_SIDECAR:
        raise TranscriptProblem('Субтитры слишком большие.','sidecar_size')
    path=Path(attachment['path'])
    # adapter._safe already verified paths, symlinks, SHA and trusted root.
    data=path.read_bytes()
    if len(data)!=attachment['byte_size'] or hashlib.sha256(data).hexdigest()!=attachment['sha256']:
        raise TranscriptProblem('Исходный файл субтитров изменился во время чтения.','sidecar_changed')
    return data


def _local_child(channel,video_path,lng):
    try:
        from worker import local_worker as w
        model=w.get_model()
        it,info=model.transcribe(video_path,language=None if lng=='auto' else lng,
                                  beam_size=5,vad_filter=True)
        rows=[]
        detected=language(getattr(info,'language',None))
        for i,seg in enumerate(it):
            if i>=12000:raise TranscriptProblem('Слишком много ASR сегментов.','segment_limit')
            start,end=getattr(seg,'start',None),getattr(seg,'end',None)
            if not isinstance(start,(int,float)) or not isinstance(end,(int,float)):
                raise TranscriptProblem('Нет точных ASR таймкодов.','invalid_asr')
            if not all((__import__('math').isfinite(start),__import__('math').isfinite(end))):
                raise TranscriptProblem('Неверный ASR таймкод.','invalid_asr')
            rows.append({'start_ms':round(start*1000),'end_ms':round(end*1000),
                         'text':str(getattr(seg,'text','')),
                         'language':detected})
        channel.send(('ok',rows,detected,w.MODEL_NAME))
    except BaseException as exc:
        try:channel.send(('error',str(type(exc).__name__)[:100]))
        except (BrokenPipeError,OSError):pass
    finally:channel.close()


def isolated_local_asr(video_path,lng,timeout=ASR_TIMEOUT):
    ctx=mp.get_context('spawn');recv,send=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_local_child,args=(send,str(video_path),lng),daemon=False)
    process.start();send.close()
    try:
        if not recv.poll(timeout):raise TranscriptProblem('Локальный ASR превысил время.','asr_timeout')
        try: response=recv.recv()
        except EOFError as exc:raise TranscriptProblem('Локальный ASR не вернул сегменты.','asr_failed') from exc
        if response[0]!='ok':raise TranscriptProblem('Локальный ASR завершился ошибкой.','asr_failed')
        return response[1:]
    finally:
        recv.close();process.join(timeout=1)
        if process.is_alive():
            process.terminate();process.join(timeout=4)
            if process.is_alive():process.kill();process.join()


def run_one(*, local_asr=isolated_local_asr):
    with _connection() as conn:
        job=_claim(conn)
        conn.commit()
    if job is None:return None
    pid,sid,jid,attempt=job['project_id'],job['source_id'],job['job_id'],job['attempt']
    payload=None;error=None
    try:
        manifest,verified=_match_manifest(job)
        attachments=verified['attachments']
        srt=_read_sidecar(attachments.get('subtitles'))
        txt=_read_sidecar(attachments.get('transcript'))
        if job['mode']=='local_asr':
            rows,lng,model=local_asr(verified['video_path'],job['settings']['language'])
            payload=normalize(manifest,asr_segments=rows,detected_language=lng,model_version=model)
        else:
            payload=normalize(manifest,srt=srt,txt=txt)
        if job['mode']=='local_asr':
            payload['engine']='local_whisper';payload['model']=model;payload['model_version']=str(model)
        # Inference with an uncertain no_speech status must not be called no_speech.
        if job['mode']=='local_asr' and not rows:
            payload['speech_status']='no_speech'
            payload['processing_status']='completed'
            payload['warnings']=[x for x in payload['warnings'] if x!='no_transcript_data']
    except TranscriptProblem as exc:
        error=(exc.code,str(exc)[:400])
    except (AcquisitionProblem,OSError,ValueError,TypeError,KeyError) as exc:
        error=('asset_unavailable','Проверенный видеофайл или субтитры недоступны.')
    except BaseException:
        error=('worker_error','Не удалось обработать сегменты.')
    with _connection() as conn:
        row=conn.execute('''SELECT state,attempts FROM source_extraction_jobs
             WHERE project_id=%s AND id=%s FOR UPDATE''',(pid,jid)).fetchone()
        if row!=('processing',attempt):return {'job_id':str(jid),'state':'stale'}
        if error:
            code,msg=error
            state='unavailable' if code in ('sidecar_changed','video_hash_mismatch','asset_unavailable','no_video','asr_failed') else 'failed'
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,error_code=%s,
                error_message=%s,lease_until=NULL,updated_at=clock_timestamp()
                WHERE id=%s''',(state,code,msg,jid))
        else:
            from psycopg.types.json import Jsonb
            state=payload['processing_status']
            digest=hashlib.sha256(canonical(payload)).hexdigest()
            saved=conn.execute('''INSERT INTO source_processing_outputs
                (project_id,source_id,source_version_id,extraction_job_id,output_type,schema_version,output,output_sha256)
                VALUES (%s,%s,%s,%s,'transcript','transcript/v1',%s,%s)
                ON CONFLICT DO NOTHING RETURNING id''',
                (pid,sid,job['video_source_version_id'],jid,Jsonb(payload),digest)).fetchone()
            output=saved[0] if saved else conn.execute('''SELECT id FROM source_processing_outputs
                 WHERE project_id=%s AND extraction_job_id=%s AND output_type='transcript' ''',(pid,jid)).fetchone()[0]
            conn.execute('''UPDATE project_transcript_runs SET result_output_id=%s,
                finished_at=clock_timestamp() WHERE project_id=%s AND job_id=%s''',(output,pid,jid))
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,output_ref=%s,
                 lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s''',(state,str(output),jid))
        if error:
            conn.execute('''UPDATE project_transcript_runs SET finished_at=clock_timestamp()
                 WHERE project_id=%s AND job_id=%s''',(pid,jid))
    return {'job_id':str(jid),'state':state,'error_code':error[0] if error else None}


def main():
    p=argparse.ArgumentParser(description='PSI stage 07: local transcript normalization worker')
    p.add_argument('--once',action='store_true');p.add_argument('--poll',type=float,default=2)
    args=p.parse_args()
    if not 0.2<=args.poll<=60:p.error('Polling interval 0.2..60s')
    while True:
        result=run_one()
        if result:print(json.dumps(result),flush=True)
        if args.once:break
        if result is None:time.sleep(args.poll)

if __name__=='__main__':main()
