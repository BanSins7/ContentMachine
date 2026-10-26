"""Durable explicitly authorized paid website AI extraction, PSI Stage 05.

Workers ONLY send an HTTPS request to OpenRouter API. The target URL is data
inside that request; fetching the page is the OpenRouter server tool's job.
Unknown outcomes are NEVER automatically re-leased/re-billed.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from . import repository
from .web_extractor import (MODEL, EXTRACTOR_VERSION, ESTIMATE_USD, QUOTE_MINUTES,
    WebProblem, canonical, digest, openrouter_fetch, provider_request, public_hostname)


def _uuid(value, label='ID'):
    try:return str(UUID(str(value)))
    except (ValueError, TypeError) as exc:raise WebProblem('invalid_id','Неверный '+label,404) from exc


def _project(pid):
    try:repository._project(pid)
    except repository.RegistryProblem as exc:raise WebProblem('project_error',str(exc),exc.http_status) from exc


def _conn():
    try:return repository._connection()
    except repository.RegistryProblem as exc:raise WebProblem('db_unavailable',str(exc),exc.http_status) from exc


def _source(conn,pid,sid,locked=False):
    sql='''SELECT source_type,canonical_url,link_kind,ingestion_mode,active_version_id,tombstoned_at
      FROM project_sources WHERE project_id=%s AND id=%s'''+(' FOR UPDATE' if locked else '')
    row=conn.execute(sql,(pid,sid)).fetchone()
    if not row:raise WebProblem('not_found','Источник отсутствует в этом проекте.',404)
    typ,url,kind,mode,version,archived=row
    if archived is not None:raise WebProblem('archived','Сначала восстанови ссылку.',409)
    if typ not in ('product_website','competitor_website','research_link') or kind!='website' or mode!='web_ai' or not version:
        raise WebProblem('unsupported_source','Web Intelligence доступен только для ссылок на сайты.',422)
    public_hostname(url)
    return {'source_type':typ,'url':url,'source_version_id':str(version)}


def cost_quote(pid,sid):
    _project(pid);sid=_uuid(sid,'source_id')
    with _conn() as conn:
        src=_source(conn,pid,sid)
        # Cap concurrency + stale-lease detection. Other requests are not charged.
        conflict=conn.execute("""SELECT 1 FROM source_extraction_jobs WHERE project_id=%s AND source_id=%s
           AND stage='web_fetch' AND state IN ('queued','processing') LIMIT 1""",(pid,sid)).fetchone()
        if conflict:raise WebProblem('already_queued','Обработка этой ссылки уже поставлена в очередь.',409)
        cfg=digest(provider_request(src['url']))
        quote=conn.execute("""INSERT INTO project_web_fetch_quotes
          (project_id,source_id,source_version_id,url,model,engine,config_sha256,estimated_cost_usd,expires_at)
          VALUES (%s,%s,%s,%s,%s,'openrouter',%s,%s,clock_timestamp()+interval '15 minutes')
          RETURNING id,expires_at""",(pid,sid,src['source_version_id'],src['url'],MODEL,cfg,ESTIMATE_USD)).fetchone()
    return {'quote_id':str(quote[0]),'expires_at':quote[1].isoformat(),
            'source_id':sid,'model':MODEL,'web_tool':'openrouter:web_fetch','engine':'openrouter',
            'estimated_usd':str(ESTIMATE_USD),'note':'Оценка, не жёсткий лимит провайдера. Действие платное и запускается отдельно.',
            'max_fetch_uses':1,'max_content_tokens':12000,'max_output_tokens':2600}


def enqueue(pid,sid,data):
    _project(pid);sid=_uuid(sid,'source_id')
    if not isinstance(data,dict) or set(data)!={'quote_id','approve_estimate_usd'}:
        raise WebProblem('invalid_payload','Нужны quote_id и approve_estimate_usd.')
    quote_id=_uuid(data.get('quote_id'),'quote_id')
    try:approved=Decimal(str(data['approve_estimate_usd']))
    except (ValueError,TypeError):raise WebProblem('cost_not_approved','Некорректное подтверждение суммы.') from None
    if not approved.is_finite() or approved < ESTIMATE_USD or approved > Decimal('1'):
        raise WebProblem('cost_not_approved','Подтверди указанную смету, не более $1.')
    with _conn() as conn:
        src=_source(conn,pid,sid,locked=True)
        q=conn.execute("""SELECT source_version_id,url,config_sha256,estimated_cost_usd,expires_at
          FROM project_web_fetch_quotes WHERE project_id=%s AND source_id=%s AND id=%s""",
          (pid,sid,quote_id)).fetchone()
        if not q:raise WebProblem('quote_not_found','Смета недоступна.',404)
        sv,url,cfg,estimate,expires=q
        # A completed call may have changed active_version_id. HTTP retries of
        # the *same* quote must still resolve to its original charged job.
        old=conn.execute('SELECT job_id FROM project_web_fetch_runs WHERE project_id=%s AND quote_id=%s',
                         (pid,quote_id)).fetchone()
        if old:
            state=conn.execute('SELECT state FROM source_extraction_jobs WHERE project_id=%s AND id=%s',
                               (pid,old[0])).fetchone()[0]
            return {'job_id':str(old[0]),'state':state,'created':False}
        if expires<=datetime.now(expires.tzinfo) or src['source_version_id']!=str(sv) or src['url']!=url:
            raise WebProblem('quote_expired','Смета устарела. Получи новую.',409)
        if approved<estimate:raise WebProblem('cost_not_approved','Подтверди полную смету.',409)
        if conn.execute("""SELECT 1 FROM source_extraction_jobs
             WHERE project_id=%s AND source_id=%s AND stage='web_fetch'
               AND state IN ('queued','processing') LIMIT 1""",(pid,sid)).fetchone():
            raise WebProblem('already_queued','Этот сайт уже обрабатывается.',409)
        job=conn.execute("""INSERT INTO source_extraction_jobs
          (project_id,source_id,source_version_id,stage,state,idempotency_key,
           attempts,max_attempts,cost_estimate_usd,charge_state,consent_ref)
          VALUES (%s,%s,%s,'web_fetch','queued',%s,0,1,%s,'reserved',%s)
          RETURNING id""",(pid,sid,sv,'web:'+quote_id,estimate,quote_id)).fetchone()[0]
        conn.execute("""INSERT INTO project_web_fetch_runs
          (project_id,job_id,source_id,quote_id,input_source_version_id,requested_url,model,config_sha256)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",(pid,job,sid,quote_id,sv,url,MODEL,cfg))
    return {'job_id':str(job),'state':'queued','created':True}


def list_runs(pid,sid):
    _project(pid);sid=_uuid(sid,'source_id')
    with _conn() as conn:
        _source(conn,pid,sid)
        rows=conn.execute("""SELECT j.id,j.state,j.error_code,j.cost_estimate_usd,j.cost_actual_usd,
          j.charge_state,r.started_at,r.finished_at,r.created_at
          FROM project_web_fetch_runs r JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          WHERE r.project_id=%s AND r.source_id=%s ORDER BY r.created_at DESC LIMIT 50""",(pid,sid)).fetchall()
    return {'jobs':[{'job_id':str(a),'state':b,'error_code':c,'estimated_cost_usd':str(d),
             'actual_cost_usd':str(e) if e is not None else None,'charge_state':f,
             'started_at':g.isoformat() if g else None,'finished_at':h.isoformat() if h else None,
             'created_at':i.isoformat()} for a,b,c,d,e,f,g,h,i in rows]}


def result(pid,sid,jid):
    _project(pid);sid=_uuid(sid,'source_id');jid=_uuid(jid,'job_id')
    with _conn() as conn:
        row=conn.execute("""SELECT j.state,j.error_code,j.cost_actual_usd,j.charge_state,
          r.requested_url,r.result_source_version_id,o.output,o.output_sha256
          FROM project_web_fetch_runs r
          JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          LEFT JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
          WHERE r.project_id=%s AND r.source_id=%s AND r.job_id=%s""",(pid,sid,jid)).fetchone()
        if not row:raise WebProblem('not_found','Результат не найден.',404)
        state,error,cost,charge,url,sv,payload,sha=row
    return {'job_id':jid,'state':state,'error_code':error,'actual_cost_usd':str(cost) if cost is not None else None,
            'charge_state':charge,'requested_url':url,'result_source_version_id':str(sv) if sv else None,
            'output_sha256':sha,'result':payload}


def _claim(conn):
    row=conn.execute("""SELECT j.id,j.project_id,j.source_id,j.source_version_id,j.attempts,r.requested_url,s.source_type
      FROM source_extraction_jobs j JOIN project_web_fetch_runs r ON r.project_id=j.project_id AND r.job_id=j.id
      JOIN project_sources s ON s.project_id=j.project_id AND s.id=j.source_id
      WHERE j.stage='web_fetch' AND j.state='queued' AND j.attempts=0
      ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1""").fetchone()
    if not row:return None
    jid,pid,sid,sv,old,url,source_type=row
    conn.execute("""UPDATE source_extraction_jobs SET state='processing',attempts=1,
       lease_until=clock_timestamp()+interval '5 minutes',charge_state='unknown',updated_at=clock_timestamp()
       WHERE id=%s""",(jid,))
    conn.execute('''UPDATE project_web_fetch_runs SET started_at=clock_timestamp() WHERE project_id=%s AND job_id=%s''',(pid,jid))
    return {'jid':jid,'pid':pid,'sid':sid,'input_version':sv,'url':url,'source_type':source_type}


def run_one(*,send=openrouter_fetch,key_provider=None):
    """One network call at most, never auto-retry ambiguous paid outcomes."""
    with _conn() as conn:
        job=_claim(conn)
    if not job:return None
    jid,pid,sid=job['jid'],job['pid'],job['sid']
    if key_provider is None:
        from worker.openrouter_transcribe import get_key
        from pathlib import Path
        key_provider=lambda:get_key(Path(__file__).resolve().parents[2]/'worker')
    value=None;err=None
    try:
        key=key_provider()
        if not key:
            err=('missing_api_key','Ключ OpenRouter не настроен.')
        else:
            value=send(job['url'],key)
    except WebProblem as exc:
        err=(exc.code,str(exc)[:380])
    except BaseException:
        err=('unknown_provider_outcome','Статус платного вызова неизвестен.')
    with _conn() as conn:
        record=conn.execute("""SELECT state,attempts FROM source_extraction_jobs
             WHERE project_id=%s AND id=%s FOR UPDATE""",(pid,jid)).fetchone()
        if record!=('processing',1):return {'job_id':str(jid),'state':'stale'}
        if err:
            # Key absence means no provider request; other errors may be billed.
            charge='not_applicable' if err[0]=='missing_api_key' else 'unknown'
            conn.execute("""UPDATE source_extraction_jobs SET state='failed',charge_state=%s,
              error_code=%s,error_message=%s,lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s""",
              (charge,err[0],err[1],jid))
            state='failed'
        else:
            state='unavailable' if value['fetch_status']=='unavailable' else value['fetch_status']
            # The website author's claims are not product-verified facts.
            value['source_type']=job['source_type']
            value['claim_trust']='product_owner_site_claim' if job['source_type']=='product_website' else 'third_party_claim'
            value['processed_at']=datetime.now(timezone.utc).isoformat()
            cost=Decimal(value['actual_cost_usd']) if value.get('actual_cost_usd') is not None else None
            charge='charged' if cost is not None else 'unknown'
            output_id=None;final_version=None
            if state!='unavailable':
                # A model transcript hash is NOT a raw HTTP body hash. Metadata says so.
                text=value['page_text'];sha=value['page_text_sha256'];size=len(text.encode('utf-8'))
                from psycopg.types.json import Jsonb
                # Keep prior immutable version if the *model-reported* transcript
                # matches. This is not a validation of true remote page bytes.
                existing=conn.execute('''SELECT v.id,v.content_sha256,v.byte_size FROM project_sources s
                  JOIN project_source_versions v ON v.project_id=s.project_id AND v.id=s.active_version_id
                  WHERE s.project_id=%s AND s.id=%s FOR UPDATE OF s''',(pid,sid)).fetchone()
                if existing and existing[1]==sha and existing[2]==size:
                    final_version=existing[0]
                else:
                    ver=conn.execute('''SELECT coalesce(max(version_no),0)+1 FROM project_source_versions
                          WHERE project_id=%s AND source_id=%s''',(pid,sid)).fetchone()[0]
                    loc={'kind':'web','url':job['url'],'retrieval':'model_reported_web_fetch',
                         'raw_response_captured':False,'model_reported_page_text_sha256':sha,
                         'input_source_version_id':str(job['input_version'])}
                    final_version=conn.execute('''INSERT INTO project_source_versions
                      (project_id,source_id,version_no,content_sha256,byte_size,media_type,
                       fetched_or_uploaded_at,source_locator,initial_extraction_status)
                      VALUES (%s,%s,%s,%s,%s,'text/plain; provenance=model-reported',clock_timestamp(),%s,%s)
                      RETURNING id''',(pid,sid,ver,sha,size,Jsonb(loc),state)).fetchone()[0]
                value['source_version_id']=str(final_version)
                value['job_id']=str(jid)
                output_id=conn.execute('''INSERT INTO source_processing_outputs
                  (project_id,source_id,source_version_id,output_type,schema_version,output,output_sha256)
                  VALUES (%s,%s,%s,'web_segments','web-source-extract/v1',%s,%s) RETURNING id''',
                  (pid,sid,final_version,Jsonb(value),digest(value))).fetchone()[0]
                conn.execute('''UPDATE project_sources SET active_version_id=%s,status=%s,
                    updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                    WHERE project_id=%s AND id=%s''',(final_version,state,pid,sid))
                conn.execute('''UPDATE project_web_fetch_runs SET result_source_version_id=%s,result_output_id=%s,
                  provider_request_id=%s WHERE project_id=%s AND job_id=%s''',
                  (final_version,output_id,value.get('provider_request_id'),pid,jid))
            conn.execute('''UPDATE source_extraction_jobs SET state=%s,charge_state=%s,cost_actual_usd=%s,
                  output_ref=%s,lease_until=NULL,error_code=%s,updated_at=clock_timestamp() WHERE id=%s''',
                  (state,charge,cost,str(output_id) if output_id else None,
                   'page_unavailable' if state=='unavailable' else None,jid))
        conn.execute('''UPDATE project_web_fetch_runs SET finished_at=clock_timestamp()
          WHERE project_id=%s AND job_id=%s''',(pid,jid))
    return {'job_id':str(jid),'state':state}


def main():
    p=argparse.ArgumentParser(description='Paid manual PSI website extractor (OpenRouter Sonnet 4.6)')
    p.add_argument('--once',action='store_true');p.add_argument('--poll',type=float,default=3)
    args=p.parse_args()
    if not 1<=args.poll<=60:p.error('poll must be between 1 and 60')
    while True:
        out=run_one()
        if out:print(json.dumps(out),flush=True)
        if args.once:break
        if out is None:time.sleep(args.poll)

if __name__=='__main__':main()
