"""Explicit, durable, no-cost document extraction jobs (PSI stage 04).

HTTP only enqueues; a separate local worker claims PostgreSQL jobs and executes
parsers in a timeout-bounded subprocess. No network, model, or paid work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from uuid import UUID

from . import file_library as lib
from .document_parser import (PARSER_VERSION, ParseFailure, canonical,
                              config_hash, configuration, extract)

TIMEOUT_SECONDS = 60
LEASE_SECONDS = 100
MAX_PREVIEW = 100


class DocumentJobProblem(Exception):
    def __init__(self, message, http_status=422):
        super().__init__(message)
        self.http_status = http_status


def _project(pid):
    try:
        lib._project(pid)
    except lib.FileProblem as exc:
        raise DocumentJobProblem(str(exc), exc.http_status) from exc


def _connect():
    try:
        return lib._connect()
    except lib.FileProblem as exc:
        raise DocumentJobProblem(str(exc), exc.http_status) from exc


def _uuid(value, label='id'):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise DocumentJobProblem('Некорректный '+label,404) from exc


def _digest_settings(settings, ext):
    # The same bytes with .md and .csv extensions are *not* equivalent parses.
    return hashlib.sha256(canonical({'config':settings,'extension':ext})).hexdigest()


def enqueue(project_id, file_id, data):
    _project(project_id)
    file_id = _uuid(file_id,'file_id')
    if not isinstance(data,dict) or set(data)-{'version','config'}:
        raise DocumentJobProblem('Недопустимые параметры обработки.')
    number = data.get('version')
    if number is not None and (type(number) is not int or not 1<=number<=10000):
        raise DocumentJobProblem('Некорректный номер версии.')
    try:
        config = configuration(data.get('config'))
    except ParseFailure as exc:
        raise DocumentJobProblem(str(exc)) from exc
    from psycopg.types.json import Jsonb
    with _connect() as conn:
        file = lib._file_row(conn,project_id,file_id,locked=True)
        if file['archived_at'] is not None:
            raise DocumentJobProblem('Сначала восстанови документ.',409)
        if number is None:
            row=conn.execute('''SELECT * FROM project_research_file_versions
                WHERE project_id=%s AND id=%s''',(project_id,file['active_version_id'])).fetchone()
            # SELECT * row_factory may be tuple, so use a separate explicit query.
            row=conn.execute('''SELECT id,source_version_id,version_no,original_name,content_sha256
                FROM project_research_file_versions WHERE project_id=%s AND id=%s''',
                (project_id,file['active_version_id'])).fetchone()
        else:
            row=conn.execute('''SELECT id,source_version_id,version_no,original_name,content_sha256
                FROM project_research_file_versions WHERE project_id=%s AND file_id=%s AND version_no=%s''',
                (project_id,file_id,number)).fetchone()
        if row is None:
            raise DocumentJobProblem('Версия файла отсутствует.',404)
        file_version,source_version,version_no,name,sha=row
        ext=Path(name).suffix.lower()
        settings_hash=_digest_settings(config,ext)
        key=f'doc:{source_version}:{PARSER_VERSION}:{settings_hash[:32]}'
        created=conn.execute('''INSERT INTO source_extraction_jobs
          (project_id,source_id,source_version_id,stage,state,idempotency_key,max_attempts)
          VALUES (%s,%s,%s,'document_parse','queued',%s,3)
          ON CONFLICT(project_id,idempotency_key) DO NOTHING RETURNING id''',
          (project_id,file_id,source_version,key)).fetchone()
        if created is not None:
            job=created[0]
            conn.execute('''INSERT INTO project_document_extract_requests
              (project_id,job_id,file_id,file_version_id,parser_version,config,config_sha256)
              VALUES (%s,%s,%s,%s,%s,%s,%s)''',
              (project_id,job,file_id,file_version,PARSER_VERSION,Jsonb(config),settings_hash))
        else:
            found=conn.execute('''SELECT id FROM source_extraction_jobs
              WHERE project_id=%s AND idempotency_key=%s''',(project_id,key)).fetchone()
            if not found:
                raise DocumentJobProblem('Конфликт идентификатора задачи.',409)
            job=found[0]
        status=conn.execute('SELECT state FROM source_extraction_jobs WHERE id=%s',(job,)).fetchone()[0]
    return {'job_id':str(job),'file_id':file_id,'file_version':version_no,
            'parser_version':PARSER_VERSION,'state':status,'created':created is not None}


def retry(project_id, file_id, job_id):
    _project(project_id)
    file_id=_uuid(file_id,'file_id');job_id=_uuid(job_id,'job_id')
    with _connect() as conn:
        row=conn.execute('''SELECT j.state,j.attempts,j.max_attempts
        FROM source_extraction_jobs j JOIN project_document_extract_requests r
         ON r.project_id=j.project_id AND r.job_id=j.id
        WHERE j.project_id=%s AND r.file_id=%s AND j.id=%s FOR UPDATE OF j''',
        (project_id,file_id,job_id)).fetchone()
        if not row:raise DocumentJobProblem('Задача отсутствует.',404)
        state,attempts,max_attempts=row
        if state not in ('failed','unavailable') or attempts>=max_attempts:
            raise DocumentJobProblem('Повторное выполнение сейчас недоступно.',409)
        conn.execute('''UPDATE source_extraction_jobs SET state='queued',error_code=NULL,
          error_message=NULL,lease_until=NULL,updated_at=clock_timestamp()
          WHERE id=%s''',(job_id,))
    return {'job_id':job_id,'state':'queued'}


def list_jobs(project_id,file_id):
    _project(project_id)
    fid=_uuid(file_id,'file_id')
    with _connect() as conn:
        rows=conn.execute('''SELECT j.id,j.state,j.attempts,j.error_code,r.parser_version,
           v.version_no,j.created_at,j.updated_at
          FROM project_document_extract_requests r
          JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          JOIN project_research_file_versions v ON v.project_id=r.project_id AND v.id=r.file_version_id
          WHERE r.project_id=%s AND r.file_id=%s ORDER BY j.created_at DESC LIMIT 100''',
          (project_id,fid)).fetchall()
    return {'jobs':[{'job_id':str(a),'state':b,'attempts':c,'error_code':d,
            'parser_version':e,'file_version':f,'created_at':g.isoformat(),
            'updated_at':h.isoformat()} for a,b,c,d,e,f,g,h in rows]}


def get_result(project_id,file_id,job_id,*,offset=0,limit=60):
    _project(project_id)
    fid=_uuid(file_id,'file_id');jid=_uuid(job_id,'job_id')
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=10000 or not 1<=limit<=MAX_PREVIEW:
        raise DocumentJobProblem('Некорректная пагинация.',400)
    with _connect() as conn:
        row=conn.execute('''SELECT j.state,j.attempts,j.error_code,r.parser_version,
                  r.config_sha256,v.version_no,v.content_sha256,o.output,o.output_sha256
           FROM project_document_extract_requests r
           JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
           JOIN project_research_file_versions v ON v.project_id=r.project_id AND v.id=r.file_version_id
           LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.extraction_job_id=j.id
                  AND o.output_type='document_segments'
           WHERE r.project_id=%s AND r.file_id=%s AND j.id=%s''',
           (project_id,fid,jid)).fetchone()
    if not row:raise DocumentJobProblem('Результат отсутствует.',404)
    state,attempts,error,parser_ver,cfg,version,sha,payload,result_sha=row
    base={'job_id':jid,'state':state,'attempts':attempts,'error_code':error,
          'parser_version':parser_ver,'config_sha256':cfg,'file_version':version,
          'content_sha256':sha,'result_sha256':result_sha}
    if payload:
        segments=payload.get('segments',[])
        base['result']={**payload,'segments':segments[offset:offset+limit],
                        'segment_count':len(segments),'offset':offset,
                        'has_more':offset+limit<len(segments)}
    return base


def _child(send,blob,filename,config):
    try:
        if os.name != 'nt':
            import resource
            # Virtual-address-space cap; a separate wall-clock bound is applied
            # by the parent. No external process or network is started here,
            # except optional local Tesseract invoked by the OCR adapter.
            resource.setrlimit(resource.RLIMIT_AS,(1536*1024*1024,1536*1024*1024))
        output=extract(blob,filename,config=config)
        send.send(('ok',output))
    except ParseFailure as exc:
        send.send(('error',{'code':exc.code,'message':str(exc)[:250]}))
    except BaseException:
        send.send(('error',{'code':'parser_crash','message':'Parser execution failed.'}))
    finally:
        send.close()


def isolated_extract(blob,filename,config,timeout=TIMEOUT_SECONDS):
    ctx=mp.get_context('spawn')
    recv,send=ctx.Pipe(duplex=False)
    proc=ctx.Process(target=_child,args=(send,blob,filename,config),daemon=True)
    proc.start();send.close()
    try:
        if not recv.poll(timeout):
            raise ParseFailure('timeout','Превышено время безопасного парсинга.')
        try:
            kind,payload=recv.recv()
        except EOFError as exc:
            raise ParseFailure('parser_crash','Парсер завершился без результата.') from exc
        if kind=='error':
            raise ParseFailure(payload['code'],payload['message'])
        return payload
    finally:
        recv.close()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.terminate();proc.join(timeout=3)
            if proc.is_alive():proc.kill();proc.join()


def _claim(conn):
    row=conn.execute('''SELECT j.id,j.project_id,j.source_id,j.source_version_id,
          j.attempts,r.file_id,r.file_version_id,r.config,r.config_sha256,
          v.original_name,v.content_sha256,v.byte_size,v.storage_ref
        FROM source_extraction_jobs j
        JOIN project_document_extract_requests r ON r.project_id=j.project_id AND r.job_id=j.id
        JOIN project_research_file_versions v ON v.project_id=r.project_id AND v.id=r.file_version_id
        WHERE j.stage='document_parse' AND j.attempts<j.max_attempts
          AND (j.state='queued' OR (j.state='processing' AND j.lease_until<clock_timestamp()))
        ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''').fetchone()
    if not row:return None
    (job,pid,sid,source_version,old_attempts,file_id,file_version,cfg,cfg_sha,
     filename,sha,size,ref)=row
    attempt=old_attempts+1
    conn.execute('''UPDATE source_extraction_jobs SET state='processing', attempts=%s,
       lease_until=clock_timestamp()+make_interval(secs=>%s),
       updated_at=clock_timestamp(), error_code=NULL,error_message=NULL WHERE id=%s''',
       (attempt,LEASE_SECONDS,job))
    return {'job_id':job,'project_id':pid,'source_id':sid,'source_version_id':source_version,
            'attempt':attempt,'file_id':file_id,'file_version_id':file_version,
            'config':cfg,'config_sha256':cfg_sha,'filename':filename,
            'sha256':sha,'byte_size':size,'storage_ref':ref}


def run_one():
    """Process at most one queued job; returns None when idle, or a status dict."""
    with _connect() as conn:
        job=_claim(conn)
        conn.commit()
    if job is None:return None
    pid=job['project_id'];sid=job['source_id'];jid=job['job_id'];attempt=job['attempt']
    parsed=None;failure=None
    try:
        with _connect() as conn:
            cached=conn.execute('''SELECT result,result_sha256 FROM project_document_extract_cache
                 WHERE project_id=%s AND content_sha256=%s AND parser_version=%s AND config_sha256=%s''',
                (pid,job['sha256'],PARSER_VERSION,job['config_sha256'])).fetchone()
        if cached is not None:
            parsed=cached[0]
            if hashlib.sha256(canonical(parsed)).hexdigest()!=cached[1]:
                raise ParseFailure('cache_integrity','Cached extract checksum mismatch.')
        else:
            folder=lib._project_folder(pid)
            path=lib._safe_path(folder,job['storage_ref'],must_exist=True)
            # The same immutable physical bytes used at upload are rechecked.
            blob=path.read_bytes()
            if len(blob)!=job['byte_size'] or hashlib.sha256(blob).hexdigest()!=job['sha256']:
                raise ParseFailure('source_integrity','Исходный файл отличается от сохранённой версии.')
            parsed=isolated_extract(blob,job['filename'],job['config'])
    except (ParseFailure,lib.FileProblem,OSError) as exc:
        failure = (exc.code if isinstance(exc,ParseFailure) else 'asset_unavailable',str(exc)[:400])
    except BaseException:
        failure=('worker_crash','Не удалось завершить парсинг файла.')
    with _connect() as conn:
        state=conn.execute('''SELECT state,attempts FROM source_extraction_jobs
                       WHERE project_id=%s AND id=%s FOR UPDATE''',(pid,jid)).fetchone()
        # Fencing against a worker whose lease expired and was reclaimed.
        if not state or state!=('processing',attempt):
            return {'job_id':str(jid),'state':'stale'}
        if failure:
            state='failed' if failure[0] not in ('unsupported_format','asset_unavailable') else 'unavailable'
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,error_code=%s,error_message=%s,
                lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s''',
                (state,failure[0],failure[1],jid))
        else:
            state=parsed['status']
            digest=hashlib.sha256(canonical(parsed)).hexdigest()
            from psycopg.types.json import Jsonb
            conn.execute('''INSERT INTO project_document_extract_cache
                 (project_id,content_sha256,parser_version,config_sha256,result_sha256,result_state,result)
                 VALUES (%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (project_id,content_sha256,parser_version,config_sha256) DO NOTHING''',
                 (pid,job['sha256'],PARSER_VERSION,job['config_sha256'],digest,state,Jsonb(parsed)))
            output=conn.execute('''INSERT INTO source_processing_outputs
                (project_id,source_id,source_version_id,extraction_job_id,
                 output_type,schema_version,output,output_sha256)
                VALUES (%s,%s,%s,%s,'document_segments','document-segments/v1',%s,%s)
                ON CONFLICT DO NOTHING RETURNING id''',
                (pid,sid,job['source_version_id'],jid,Jsonb(parsed),digest)).fetchone()
            output_id=output[0] if output else conn.execute('''SELECT id FROM source_processing_outputs
                WHERE project_id=%s AND extraction_job_id=%s AND output_type='document_segments' ''',(pid,jid)).fetchone()[0]
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,output_ref=%s,
                lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s''',
                (state,str(output_id),jid))
    return {'job_id':str(jid),'state':state,'cached':cached is not None if not failure else False}


def main():
    parser=argparse.ArgumentParser(description='Local PSI document extraction worker, no external APIs')
    parser.add_argument('--once',action='store_true',help='Process a single queued extraction')
    parser.add_argument('--poll',type=float,default=2,help='Polling interval for continuous worker')
    args=parser.parse_args()
    if not 0.2<=args.poll<=60:parser.error('--poll should be between 0.2 and 60 seconds')
    while True:
        result=run_one()
        if result:print(json.dumps(result),flush=True)
        if args.once:break
        if result is None:time.sleep(args.poll)

if __name__=='__main__':main()
