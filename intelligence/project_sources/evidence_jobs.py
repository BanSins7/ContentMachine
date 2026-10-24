"""PSI Stage 10: explicit, source-pinned, free normalization and evidence ledger.

All imports/materialization are local PostgreSQL transactions. Nothing is
published into Context/RTB; a review decision never modifies source claims.
"""
from __future__ import annotations
import argparse
import hashlib
import math
import re
import time
from uuid import UUID,uuid4
from psycopg.types.json import Jsonb
from .evidence_normalizer import (EvidenceProblem,canonical,sha,fingerprint,extract,
                                   profile_candidate,scope_for,lexical_similarity,VERSION)

MAX_PROFILE_FIELDS=120
LEASE_SEC=90


def _pid(pid):
    from .repository import _project
    try:return _project(pid)
    except Exception as e:raise EvidenceProblem('Проект недоступен.','project_missing',404) from e


def _uuid(value,name='ID'):
    try:
        if not isinstance(value,str):raise ValueError()
        value=str(UUID(value))
    except (ValueError,AttributeError):raise EvidenceProblem('Некорректный '+name+'.','invalid_id',400)
    return value


def _conn():
    from .repository import _connection
    return _connection()


def _source(conn,pid,sid,lock=False):
    sql='''SELECT source_type,active_version_id,tombstoned_at FROM project_sources
           WHERE project_id=%s AND id=%s'''+(' FOR UPDATE' if lock else '')
    row=conn.execute(sql,(pid,sid)).fetchone()
    if not row:raise EvidenceProblem('Источник не найден.','not_found',404)
    if row[2] is not None:raise EvidenceProblem('Источник архивирован.','archived_source',409)
    return row


def _output(conn,pid,sid,output_id=None):
    if output_id:
        row=conn.execute('''SELECT id,source_version_id,schema_version,output,output_sha256
            FROM source_processing_outputs WHERE project_id=%s AND source_id=%s AND id=%s''',
            (pid,sid,_uuid(output_id,'output_id'))).fetchone()
    else:
        row=conn.execute('''SELECT id,source_version_id,schema_version,output,output_sha256
            FROM source_processing_outputs WHERE project_id=%s AND source_id=%s
              AND schema_version IN ('document-segments/v1','web-source-extract/v1',
                  'transcript/v1','vision-prepare/v1','visual-observations/v1','video-intelligence/v1')
            ORDER BY created_at DESC,id DESC LIMIT 1''',(pid,sid)).fetchone()
    if not row or row[2] not in ('document-segments/v1','web-source-extract/v1','transcript/v1',
                                'vision-prepare/v1','visual-observations/v1','video-intelligence/v1'):
        raise EvidenceProblem('Поддерживаемый завершённый output отсутствует.','output_not_found',404)
    if not isinstance(row[3],dict) or sha(row[3])!=row[4]:
        raise EvidenceProblem('SHA-256 сохранённого output не совпадает.','output_integrity',409)
    # The provided schema type must exactly match its stored JSON.
    if row[3].get('schema_version')!=row[2]:
        raise EvidenceProblem('Schema закреплённого output не совпадает.','schema_mismatch',409)
    return row


def enqueue(pid,sid,body):
    _pid(pid);sid=_uuid(sid,'source_id')
    if not isinstance(body,dict) or set(body)-{'output_id','allow_historical'}:
        raise EvidenceProblem('Ожидаются только output_id и allow_historical.','invalid_request',400)
    if body.get('allow_historical',False) is not False and body.get('allow_historical') is not True:
        raise EvidenceProblem('allow_historical должен быть bool.','invalid_request',400)
    with _conn() as conn:
        source=_source(conn,pid,sid,lock=True)
        selected=_output(conn,pid,sid,body.get('output_id'))
        oid,vid,schema,output,digest=selected
        if str(vid)!=str(source[1]) and not body.get('allow_historical',False):
            raise EvidenceProblem('Output относится к старой версии. Явно разрешите historical.','stale_source',409)
        if schema=='visual-observations/v1':
            _verify_vision_frame_pins(conn,pid,sid,vid,output)
        input_key='ev10:'+hashlib.sha256(f'{pid}:{sid}:{vid}:{oid}:{digest}:{VERSION}'.encode()).hexdigest()
        existing=conn.execute('''SELECT r.job_id,j.state,r.evidence_count FROM project_evidence_normalization_runs r
          JOIN source_extraction_jobs j ON j.project_id=r.project_id AND j.id=r.job_id
          WHERE r.project_id=%s AND r.source_id=%s AND r.source_version_id=%s AND r.input_output_id=%s''',
          (pid,sid,vid,oid)).fetchone()
        if existing:return {'job_id':str(existing[0]),'state':existing[1],'count':existing[2],'reused':True}
        jid=uuid4()
        conn.execute('''INSERT INTO source_extraction_jobs(project_id,id,source_id,source_version_id,stage,idempotency_key,
          cost_estimate_usd,max_attempts) VALUES(%s,%s,%s,%s,'evidence_normalization',%s,0,3)''',
          (pid,jid,sid,vid,input_key))
        conn.execute('''INSERT INTO project_evidence_normalization_runs
          (project_id,job_id,source_id,source_version_id,input_output_id,input_sha256)
          VALUES(%s,%s,%s,%s,%s,%s)''',(pid,jid,sid,vid,oid,digest))
    return {'job_id':str(jid),'state':'queued','reused':False}


def _verify_vision_frame_pins(conn,pid,sid,ver,model):
    """A scene frame_id cannot stand alone: confirm its stored preparation ID."""
    prepare_job_id=model.get('prepare_job_id')
    if not prepare_job_id:
        raise EvidenceProblem('Vision lacks pinned preparation.','missing_frame_provenance',409)
    row=conn.execute('''SELECT o.output,o.output_sha256 FROM project_vision_runs r
      JOIN source_processing_outputs o ON o.project_id=r.project_id AND o.id=r.result_output_id
      WHERE r.project_id=%s AND r.source_id=%s AND r.video_source_version_id=%s
        AND r.job_id=%s AND r.mode='prepare' ''',(pid,sid,ver,_uuid(prepare_job_id))).fetchone()
    if not row or not isinstance(row[0],dict) or sha(row[0])!=row[1]:
        raise EvidenceProblem('Нельзя проверить исходные кадры.','missing_frame_provenance',409)
    prep=row[0]
    if model.get('video_sha256')!=prep.get('video_sha256') or model.get('video_source_version_id')!=str(ver):
        raise EvidenceProblem('Vision принадлежит другому MP4.','video_version_mismatch',409)
    scenes={s['scene_id']:s for s in prep.get('scenes',[]) if isinstance(s,dict) and isinstance(s.get('scene_id'),str)}
    for scene in model.get('scenes',[]):
        base=scenes.get(scene.get('scene_id')) if isinstance(scene,dict) else None
        if not base or (scene.get('start_ms'),scene.get('end_ms'))!=(base.get('start_ms'),base.get('end_ms')):
            raise EvidenceProblem('Неизвестная сцена Vision.','invalid_frame_provenance',409)
        fset=set(base.get('frame_ids',[]))
        refs=scene.get('frame_ids')
        if not isinstance(refs,list) or not refs or len(set(refs))!=len(refs) or not set(refs).issubset(fset):
            raise EvidenceProblem('Frame ID отсутствует в подготовке.','invalid_frame_provenance',409)
    return prep


def _flatten(profile):
    results=[]
    def walk(obj,path='',depth=0):
        if len(results)>=MAX_PROFILE_FIELDS or depth>6:return
        if isinstance(obj,dict):
            for k,v in sorted(obj.items()):
                if not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',k):continue
                if re.search(r'password|secret|token|api.?key|cookie|credential|authorization',k,re.I):continue
                walk(v,f'{path}.{k}' if path else k,depth+1)
        elif isinstance(obj,list):
            for i,v in enumerate(obj[:40]):walk(v,f'{path}[{i}]',depth+1)
        elif isinstance(obj,str) and obj.strip() and len(obj)<=12000 and path:
            results.append((path,obj))
        elif type(obj) in (int,float) and path and len(str(obj))<=100 and (type(obj) is int or math.isfinite(obj)):
            results.append((path,str(obj)))
    walk(profile)
    return results


def _profile_field(profile,path):
    cursor=profile
    for key,index in re.findall(r'([A-Za-z0-9_-]+)|\[(\d+)\]',path):
        try:
            cursor=cursor[int(index)] if index else cursor[key]
        except (KeyError,IndexError,TypeError):raise EvidenceProblem('Поле больше не существует.','profile_field_missing',409)
    if type(cursor) is str:return cursor
    if type(cursor) in (int,float) and (type(cursor) is int or math.isfinite(cursor)):return str(cursor)
    raise EvidenceProblem('Неверный тип поля.','profile_field_invalid',409)


def import_profile(pid,body):
    """Explicit profile import: create immutable version per revision for each leaf.

    Original questionnaire JSON is never modified; previous revisions remain
    queryable and no new sources are created on a background timer.
    """
    _pid(pid)
    if body not in ({},{'dry_run':True}):raise EvidenceProblem('Только dry_run=true поддерживается.','invalid_request',400)
    with _conn() as conn:
        profile=conn.execute('''SELECT p.revision,p.profile_hash,p.profile FROM project_profile_versions p
         JOIN project_registry r ON r.id=p.project_id AND r.profile_revision=p.revision
         WHERE p.project_id=%s''',(pid,)).fetchone()
        if not profile:raise EvidenceProblem('Анкета не синхронизирована.','profile_missing',404)
        revision,digest,doc=profile
        fields=_flatten(doc)
        if body.get('dry_run'):return {'profile_revision':revision,'fields':len(fields),'dry_run':True}
        created=0;reused=0;queued=0
        for path,value in fields:
            # Stable project scope + immutable origin path. A new profile revision
            # creates a new source version, not a rewrite of user evidence.
            identity=f'profile:{path}'
            s=conn.execute('''SELECT id,active_version_id FROM project_sources
                   WHERE project_id=%s AND identity_key=%s AND tombstoned_at IS NULL FOR UPDATE''',(pid,identity)).fetchone()
            if s:sid,active=s
            else:
                sid=conn.execute('''INSERT INTO project_sources(project_id,source_type,original_input,
                    identity_key,origin_field,label,ingestion_mode)
                    VALUES(%s,'questionnaire_field',%s,%s,NULL,%s,'metadata_only') RETURNING id''',
                    (pid,path,identity,path[:250])).fetchone()[0]
                active=None;created+=1
            if active:
                active_loc=conn.execute('SELECT source_locator FROM project_source_versions WHERE project_id=%s AND id=%s',(pid,active)).fetchone()[0]
                if active_loc.get('profile_revision')==revision and active_loc.get('profile_hash')==digest:
                    reused+=1;continue
            content_hash=hashlib.sha256(value.encode('utf-8')).hexdigest()
            ver=conn.execute('''SELECT coalesce(max(version_no),0)+1 FROM project_source_versions
                WHERE project_id=%s AND source_id=%s''',(pid,sid)).fetchone()[0]
            loc={'kind':'questionnaire','field_path':path,'profile_revision':revision,'profile_hash':digest}
            vid=conn.execute('''INSERT INTO project_source_versions
                (project_id,source_id,version_no,content_sha256,byte_size,media_type,source_locator,initial_extraction_status)
                VALUES(%s,%s,%s,%s,%s,'text/plain; charset=utf-8',%s,'completed') RETURNING id''',
                (pid,sid,ver,content_hash,len(value.encode('utf-8')),Jsonb(loc))).fetchone()[0]
            conn.execute('UPDATE project_sources SET active_version_id=%s,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s',(vid,pid,sid))
            jid=uuid4()
            job_key='profile10:'+hashlib.sha256(f'{pid}:{sid}:{vid}:{revision}:{digest}'.encode()).hexdigest()
            conn.execute('''INSERT INTO source_extraction_jobs(project_id,id,source_id,source_version_id,stage,idempotency_key,
              cost_estimate_usd,max_attempts) VALUES(%s,%s,%s,%s,'evidence_normalization',%s,0,3)''',
              (pid,jid,sid,vid,job_key))
            conn.execute('''INSERT INTO project_evidence_normalization_runs
                (project_id,job_id,source_id,source_version_id,input_profile_revision,input_profile_hash,input_field_path,input_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(pid,jid,sid,vid,revision,digest,path,content_hash))
            queued+=1
    return {'profile_revision':revision,'fields':len(fields),'new_sources':created,'reused':reused,'jobs_queued':queued}


def _claim(conn):
    row=conn.execute('''SELECT j.id,j.project_id,j.source_id,j.source_version_id,j.attempts,
        r.input_output_id,r.input_profile_revision,r.input_profile_hash,r.input_field_path,r.input_sha256,
        s.source_type
      FROM source_extraction_jobs j
      JOIN project_evidence_normalization_runs r ON r.project_id=j.project_id AND r.job_id=j.id
      JOIN project_sources s ON s.project_id=j.project_id AND s.id=j.source_id
      WHERE j.stage='evidence_normalization' AND j.attempts<j.max_attempts
        AND (j.state='queued' OR (j.state='processing' AND j.lease_until<clock_timestamp()))
      ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1''').fetchone()
    if not row:return None
    jid,pid,sid,vid,attempts,oid,rev,ph,path,digest,source_type=row
    attempt=attempts+1
    conn.execute('''UPDATE source_extraction_jobs SET state='processing',attempts=%s,
      lease_until=clock_timestamp()+make_interval(secs=>%s),updated_at=clock_timestamp(),
      error_code=NULL,error_message=NULL WHERE project_id=%s AND id=%s''',(attempt,LEASE_SEC,pid,jid))
    return {'job_id':jid,'project_id':pid,'source_id':sid,'version_id':vid,'attempt':attempt,
            'output_id':oid,'profile_revision':rev,'profile_hash':ph,'field_path':path,
            'input_sha':digest,'source_type':source_type}


def _candidates(conn,job):
    pid=job['project_id'];sid=job['source_id'];vid=job['version_id']
    if job['output_id']:
        row=_output(conn,pid,sid,str(job['output_id']))
        if str(row[1])!=str(vid) or row[4]!=job['input_sha']:
            raise EvidenceProblem('Pinned output изменён или подменён.','stale_input',409)
        prep=_verify_vision_frame_pins(conn,pid,sid,vid,row[3]) if row[2]=='visual-observations/v1' else None
        return extract(row[3],job['source_type'],prepared=prep)
    row=conn.execute('''SELECT profile FROM project_profile_versions
             WHERE project_id=%s AND revision=%s AND profile_hash=%s''',
             (pid,job['profile_revision'],job['profile_hash'])).fetchone()
    if not row:raise EvidenceProblem('Pinned профиль отсутствует.','stale_profile',409)
    value=_profile_field(row[0],job['field_path'])
    if hashlib.sha256(value.encode('utf-8')).hexdigest()!=job['input_sha']:
        raise EvidenceProblem('Поле анкеты изменено.','profile_hash_mismatch',409)
    sv=conn.execute('''SELECT source_locator,content_sha256 FROM project_source_versions
             WHERE project_id=%s AND source_id=%s AND id=%s''',(pid,sid,vid)).fetchone()
    if (not sv or sv[1]!=job['input_sha'] or sv[0].get('profile_revision')!=job['profile_revision']
       or sv[0].get('profile_hash')!=job['profile_hash'] or sv[0].get('field_path')!=job['field_path']):
        raise EvidenceProblem('Pinned версия анкеты не совпадает.','profile_version_mismatch',409)
    return profile_candidate(value,job['field_path'],job['profile_revision'],job['profile_hash']),[]


def _materialize(conn,job,candidates):
    pid,sid,vid,oid=job['project_id'],job['source_id'],job['version_id'],job['output_id']
    total=0;relationships=0
    for c in candidates:
        if not c.get('text'):continue
        quote=c['text']; loc=c['locator']
        quote_hash=hashlib.sha256(quote.encode('utf-8')).hexdigest()
        loc_hash=sha(loc);fp=fingerprint(quote)
        group=conn.execute('''INSERT INTO project_evidence_exact_groups
            (project_id,evidence_kind,knowledge_scope,normalized_quote_sha256)
            VALUES(%s,%s,%s,%s) ON CONFLICT(project_id,evidence_kind,knowledge_scope,normalized_quote_sha256)
            DO NOTHING RETURNING id''',(pid,c['kind'],c['scope'],fp)).fetchone()
        if group:gid=group[0]
        else:gid=conn.execute('''SELECT id FROM project_evidence_exact_groups
                 WHERE project_id=%s AND evidence_kind=%s AND knowledge_scope=%s AND normalized_quote_sha256=%s''',
                 (pid,c['kind'],c['scope'],fp)).fetchone()[0]
        existing=conn.execute('''SELECT evidence_id FROM project_evidence_grounding WHERE project_id=%s
          AND source_id=%s AND source_version_id=%s AND input_output_id IS NOT DISTINCT FROM %s
          AND locator_sha256=%s AND quote_sha256=%s''',(pid,sid,vid,oid,loc_hash,quote_hash)).fetchone()
        if existing:continue
        eid=uuid4()
        conn.execute('''INSERT INTO project_evidence
          (id,project_id,source_id,source_version_id,processing_output_id,evidence_kind,
           exact_text_or_observation,locator,extraction_engine,extraction_version,
           source_quote,confidence,quality_status,review_status)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s)''',
          (eid,pid,sid,vid,oid,c['kind'],quote,Jsonb(loc),c['method'][:250],VERSION,
           quote,c['quality'],c['review_status']))
        citation=f'project-source://{pid}/{sid}/versions/{vid}/evidence/{eid}'
        conn.execute('''INSERT INTO project_evidence_grounding
            (project_id,evidence_id,source_id,source_version_id,input_output_id,profile_revision,profile_hash,
             quote_sha256,normalized_quote_sha256,locator_sha256,input_sha256,exact_group_id,
             knowledge_scope,trust_label,claim_key,typed_value,metadata,citation)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (pid,eid,sid,vid,oid,job['profile_revision'],job['profile_hash'],quote_hash,fp,
             loc_hash,job['input_sha'],gid,c['scope'],c['trust'],c['claim_key'],
             Jsonb(c['typed_value']) if c['typed_value'] is not None else None,
             Jsonb(c.get('metadata') or {}),citation))
        total+=1
        # Conflicts are reviewable only for SAME typed field/source across
        # revisions; wording alone never establishes a business contradiction.
        if c['claim_key']:
            matches=conn.execute('''SELECT g.evidence_id,e.exact_text_or_observation
                 FROM project_evidence_grounding g JOIN project_evidence e
                   ON e.project_id=g.project_id AND e.id=g.evidence_id
                 WHERE g.project_id=%s AND g.source_id=%s AND g.claim_key=%s
                   AND g.evidence_id<>%s AND g.quote_sha256<>%s
                 ORDER BY e.created_at DESC LIMIT 25''',
                 (pid,sid,c['claim_key'],eid,quote_hash)).fetchall()
            for old_id,old_text in matches:
                a,b=sorted([eid,old_id]);_relationship(conn,pid,a,b,'possible_conflict',None,
                                      'different_exact_values_same_user_field');relationships+=1
        # Lexical near-duplicate is NEVER silently collapsed or considered
        # proof of semantic equivalence. Exclude identical normalized quotes.
        choices=conn.execute('''SELECT g.evidence_id,e.exact_text_or_observation
             FROM project_evidence_grounding g JOIN project_evidence e
               ON e.project_id=g.project_id AND e.id=g.evidence_id
             WHERE g.project_id=%s AND g.knowledge_scope=%s AND e.evidence_kind=%s
               AND g.normalized_quote_sha256<>%s AND g.evidence_id<>%s
               AND length(e.exact_text_or_observation) BETWEEN %s AND %s
             ORDER BY e.created_at DESC LIMIT 100''',
             (pid,c['scope'],c['kind'],fp,eid,max(1,len(quote)//2),min(20000,len(quote)*2))).fetchall()
        for old_id,old_text in choices:
            score=lexical_similarity(quote,old_text)
            if score>=0.87:
                a,b=sorted([eid,old_id]);_relationship(conn,pid,a,b,'lexical_duplicate_candidate',round(score,5),
                                         'high_lexical_overlap_only_needs_review');relationships+=1
    return total,relationships


def _relationship(conn,pid,a,b,kind,score,reason):
    conn.execute('''INSERT INTO project_evidence_relationships
      (project_id,evidence_id_a,evidence_id_b,relation,score,reason)
      VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(pid,a,b,kind,score,reason))


def run_one(*,producer=None):
    with _conn() as conn:job=_claim(conn)
    if not job:return None
    error=None;candidates=[];warnings=[]
    try:
        with _conn() as conn:candidates,warnings=(producer(conn,job) if producer else _candidates(conn,job))
        if not isinstance(candidates,list) or len(candidates)>450 or not isinstance(warnings,list):
            raise EvidenceProblem('Неверный нормализованный ответ.','invalid_candidates')
    except EvidenceProblem as exc:error=(exc.code,str(exc)[:400])
    except BaseException:error=('normalization_failed','Не удалось проверить исходное содержимое.')
    with _conn() as conn:
        pid,sid,vid,jid=job['project_id'],job['source_id'],job['version_id'],job['job_id']
        state=conn.execute('''SELECT state,attempts FROM source_extraction_jobs WHERE project_id=%s AND id=%s FOR UPDATE''',(pid,jid)).fetchone()
        if state!=('processing',job['attempt']):return {'job_id':str(jid),'state':'stale'}
        if error:
            conn.execute("""UPDATE source_extraction_jobs SET state='failed',error_code=%s,error_message=%s,
             lease_until=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s""",
             (*error,pid,jid))
            return {'job_id':str(jid),'state':'failed','code':error[0]}
        # Re-read pinned input INSIDE the finishing transaction to prohibit a
        # mutation slipping between validation and evidence persistence.
        checked,_=_candidates(conn,job)
        if sha(checked)!=sha(candidates):
            conn.execute("""UPDATE source_extraction_jobs SET state='failed',error_code='input_changed',
              lease_until=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s""",(pid,jid))
            return {'job_id':str(jid),'state':'failed','code':'input_changed'}
        count,pairs=_materialize(conn,job,candidates)
        summary={'schema_version':'evidence-normalization/v1','normalizer_version':VERSION,
                 'project_id':pid,'source_id':str(sid),'source_version_id':str(vid),
                 'input_output_id':str(job['output_id']) if job['output_id'] else None,
                 'input_profile_revision':job['profile_revision'],'input_sha256':job['input_sha'],
                 'evidence_count':count,'relationship_count':pairs,'warnings':warnings,
                 'provenance':'verified_stored_output','auto_approved':False}
        digest=sha(summary)
        oid=conn.execute('''INSERT INTO source_processing_outputs
           (project_id,source_id,source_version_id,extraction_job_id,output_type,schema_version,output,output_sha256)
           VALUES(%s,%s,%s,%s,'other','evidence-normalization/v1',%s,%s) RETURNING id''',
           (pid,sid,vid,jid,Jsonb(summary),digest)).fetchone()[0]
        conn.execute('''UPDATE project_evidence_normalization_runs
            SET result_output_id=%s,evidence_count=%s,rejected_count=%s,finished_at=clock_timestamp()
            WHERE project_id=%s AND job_id=%s''',(oid,count,len(warnings),pid,jid))
        conn.execute("""UPDATE source_extraction_jobs SET state='completed',output_ref=%s,
          lease_until=NULL,updated_at=clock_timestamp() WHERE project_id=%s AND id=%s""",(str(oid),pid,jid))
    return {'job_id':str(job['job_id']),'state':'completed','count':count,'relationships':pairs}


def list_jobs(pid,sid):
    _pid(pid);sid=_uuid(sid,'source_id')
    with _conn() as conn:
        _source(conn,pid,sid)
        rows=conn.execute('''SELECT r.job_id,j.state,j.attempts,r.source_version_id,r.input_output_id,
              r.input_profile_revision,r.evidence_count,r.finished_at,j.error_code
            FROM project_evidence_normalization_runs r JOIN source_extraction_jobs j
              ON j.project_id=r.project_id AND j.id=r.job_id
            WHERE r.project_id=%s AND r.source_id=%s
            ORDER BY j.created_at DESC LIMIT 80''',(pid,sid)).fetchall()
    return {'jobs':[{'job_id':str(x[0]),'state':x[1],'attempts':x[2],
                     'source_version_id':str(x[3]),'input_output_id':str(x[4]) if x[4] else None,
                     'profile_revision':x[5],'count':x[6],
                     'finished_at':x[7].isoformat() if x[7] else None,'error_code':x[8]} for x in rows]}


def list_evidence(pid,*,sid=None,offset=0,limit=50):
    _pid(pid)
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=100000 or not 1<=limit<=100:
        raise EvidenceProblem('Некорректная пагинация.','invalid_pagination',400)
    if sid:sid=_uuid(sid,'source_id')
    with _conn() as conn:
        rows=conn.execute('''SELECT e.id,e.source_id,e.source_version_id,e.processing_output_id,
             e.evidence_kind,e.exact_text_or_observation,e.locator,e.extraction_engine,
             e.quality_status,e.review_status,e.created_at,g.trust_label,g.knowledge_scope,
             g.citation,g.exact_group_id,g.typed_value,g.claim_key,
             (SELECT count(*) FROM project_evidence_grounding g2 WHERE g2.project_id=g.project_id AND g2.exact_group_id=g.exact_group_id) AS duplicate_count,
             (SELECT count(*) FROM project_evidence_relationships rel WHERE rel.project_id=g.project_id AND (rel.evidence_id_a=e.id OR rel.evidence_id_b=e.id)) AS review_flags,
             (SELECT decision FROM project_evidence_reviews rev WHERE rev.project_id=e.project_id AND rev.evidence_id=e.id) AS human_decision,
             (s.active_version_id=e.source_version_id) AS current_version
           FROM project_evidence e JOIN project_evidence_grounding g ON g.project_id=e.project_id AND g.evidence_id=e.id
           JOIN project_sources s ON s.project_id=e.project_id AND s.id=e.source_id
           WHERE e.project_id=%s AND (%s::uuid IS NULL OR e.source_id=%s::uuid)
           ORDER BY e.created_at DESC,e.id DESC OFFSET %s LIMIT %s''',(pid,sid,sid,offset,limit)).fetchall()
    return {'items':[{'evidence_id':str(x[0]),'source_id':str(x[1]),'source_version_id':str(x[2]),
       'processing_output_id':str(x[3]) if x[3] else None,'evidence_kind':x[4],'exact_quote':x[5],
       'locator':x[6],'method':x[7],'quality_status':x[8],'review_status':x[9],
       'created_at':x[10].isoformat(),'trust_label':x[11],'knowledge_scope':x[12],
       'citation':x[13],'exact_group_id':str(x[14]),'typed_value':x[15],
       'claim_key':x[16],'exact_duplicate_occurrences':x[17],'relationship_flags':x[18],
       'human_decision':x[19],'is_current_version':x[20]} for x in rows],
       'offset':offset,'limit':limit}


def verify(pid,evidence_id):
    _pid(pid);eid=_uuid(evidence_id,'evidence_id')
    with _conn() as conn:
        row=conn.execute('''SELECT e.source_id,e.source_version_id,e.exact_text_or_observation,e.locator,
                 e.evidence_kind,g.input_output_id,g.profile_revision,g.profile_hash,g.input_sha256,
                 g.quote_sha256,g.locator_sha256,g.citation,s.source_type,s.active_version_id
            FROM project_evidence e JOIN project_evidence_grounding g ON g.project_id=e.project_id AND g.evidence_id=e.id
              JOIN project_sources s ON s.project_id=e.project_id AND s.id=e.source_id
            WHERE e.project_id=%s AND e.id=%s''',(pid,eid)).fetchone()
        if not row:raise EvidenceProblem('Evidence не найден.','not_found',404)
        sid,vid,quote,loc,kind,oid,revision,profile_hash,input_sha,quote_sha,loc_sha,citation,stype,active=row
        if hashlib.sha256(quote.encode('utf-8')).hexdigest()!=quote_sha or sha(loc)!=loc_sha:
            raise EvidenceProblem('Evidence изменено.','evidence_integrity',409)
        job={'project_id':pid,'source_id':sid,'version_id':vid,'output_id':oid,
             'profile_revision':revision,'profile_hash':profile_hash,'field_path':loc.get('field_path'),
             'input_sha':input_sha,'source_type':stype}
        candidates,_=_candidates(conn,job)
        is_grounded=any(c['text']==quote and c['locator']==loc and c['kind']==kind for c in candidates)
        if not is_grounded:raise EvidenceProblem('Не удаётся подтвердить точный фрагмент.','evidence_not_grounded',409)
    return {'evidence_id':eid,'valid':True,'citation':citation,'is_current_version':vid==active,
            'source_version_id':str(vid),'quote':quote,'trust_note':
            'Validated against stored model-produced text, NOT against live/raw website' if oid and stype in ('product_website','competitor_website','research_link') else 'Validated against stored pinned source output'}


def review(pid,evidence_id,body):
    _pid(pid);eid=_uuid(evidence_id,'evidence_id')
    if not isinstance(body,dict) or set(body)-{'decision','reason'} or body.get('decision') not in ('approved','rejected'):
        raise EvidenceProblem('Требуется decision=approved/rejected.','invalid_review',400)
    reason=body.get('reason','')
    if not isinstance(reason,str) or len(reason)>1000:raise EvidenceProblem('Некорректный комментарий.','invalid_review',400)
    verified=verify(pid,eid)
    if not verified['is_current_version']:
        raise EvidenceProblem('Нельзя подтвердить устаревшую версию.','stale_review',409)
    with _conn() as conn:
        existing=conn.execute('''SELECT decision FROM project_evidence_reviews
           WHERE project_id=%s AND evidence_id=%s''',(pid,eid)).fetchone()
        if existing:raise EvidenceProblem('Решение уже неизменно записано.','already_reviewed',409)
        conn.execute('''INSERT INTO project_evidence_reviews
           (project_id,evidence_id,decision,reviewed_by,review_reason)
           VALUES(%s,%s,%s,'local_owner',%s)''',(pid,eid,body['decision'],reason))
    return {'evidence_id':eid,'decision':body['decision'],'review_saved':True,
            'note':'Review decision does not convert external/creative evidence into verified product facts.'}


def retry(pid,sid,job_id):
    _pid(pid);sid=_uuid(sid,'source_id');jid=_uuid(job_id,'job_id')
    with _conn() as conn:
        _source(conn,pid,sid,True)
        row=conn.execute('''SELECT j.state,j.attempts,j.max_attempts
          FROM source_extraction_jobs j JOIN project_evidence_normalization_runs r
          ON r.project_id=j.project_id AND r.job_id=j.id
          WHERE j.project_id=%s AND j.source_id=%s AND j.id=%s FOR UPDATE OF j''',(pid,sid,jid)).fetchone()
        if not row:raise EvidenceProblem('Задание не найдено.','not_found',404)
        if row[0] not in ('failed','unavailable') or row[1]>=row[2]:
            raise EvidenceProblem('Повтор недоступен.','retry_conflict',409)
        conn.execute('''UPDATE source_extraction_jobs SET state='queued',lease_until=NULL,
          error_code=NULL,error_message=NULL,updated_at=clock_timestamp()
          WHERE project_id=%s AND id=%s''',(pid,jid))
    return {'job_id':jid,'state':'queued'}


def main():
    p=argparse.ArgumentParser(description='PSI Stage10 offline evidence verification, no model/network')
    p.add_argument('--once',action='store_true');p.add_argument('--poll',type=float,default=2)
    a=p.parse_args()
    if not .2<=a.poll<=60:p.error('poll 0.2..60 seconds')
    while True:
        try:res=run_one()
        except Exception as exc:
            print('evidence worker operation failed:',type(exc).__name__);res=None
        if res:print('evidence:',res['job_id'],res['state'])
        if a.once:break
        if not res:time.sleep(a.poll)

if __name__=='__main__':main()
