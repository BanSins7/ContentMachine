"""PSI Stage 11: synchronous, free PostgreSQL context snapshot operations.

Only explicit build/approve mutates data; no paid LLM, media, retrieval or
background jobs. Every snapshot is append-only with separately-pinned outputs.
"""
from __future__ import annotations
from uuid import UUID
import hashlib
import json
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .context_builder import ContextProblem,build,sha,canonical,choose_evidence,BUILDER_VERSION

MAX_LIST=80

def _pid(pid):
    from .repository import _project
    try:return _project(pid)
    except Exception as exc:raise ContextProblem('Проект не найден.','project_missing',404) from exc

def _conn():
    from .repository import _connection
    return _connection()

def _uuid(raw,name='id'):
    try:
        if not isinstance(raw,str):raise ValueError()
        return str(UUID(raw))
    except (ValueError,TypeError,AttributeError) as e:raise ContextProblem('Неверный '+name+'.','invalid_id',400) from e

def _assemble(conn,pid):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT r.profile_revision AS revision,r.profile_hash AS digest,p.profile
            FROM project_registry r JOIN project_profile_versions p
              ON p.project_id=r.id AND p.revision=r.profile_revision AND p.profile_hash=r.profile_hash
            WHERE r.id=%s''',(pid,))
        profile=cur.fetchone()
        if not profile:raise ContextProblem('Версия анкеты не синхронизирована.','profile_missing',409)
        cur.execute('''SELECT s.id,s.source_type,s.label,s.canonical_url,s.status,
                (s.tombstoned_at IS NOT NULL) AS tombstoned,s.active_version_id AS version_id,
                v.version_no,v.content_sha256,v.byte_size,v.media_type,v.source_locator AS locator,
                v.recorded_at,v.initial_extraction_status AS initial_status
             FROM project_sources s LEFT JOIN project_source_versions v
               ON v.project_id=s.project_id AND v.source_id=s.id AND v.id=s.active_version_id
             WHERE s.project_id=%s ORDER BY s.id''',(pid,))
        ss=[dict(x) for x in cur.fetchall()]
        cur.execute('''SELECT o.id,o.source_id,o.source_version_id AS version_id,o.output_type,
               o.schema_version,o.output_sha256,o.output,o.created_at
             FROM source_processing_outputs o JOIN project_sources s
               ON s.project_id=o.project_id AND s.id=o.source_id AND s.active_version_id=o.source_version_id
             WHERE o.project_id=%s ORDER BY o.id''',(pid,))
        outs=[dict(x) for x in cur.fetchall()]
        cur.execute('''SELECT e.id,e.source_id,e.source_version_id AS version_id,
                e.processing_output_id AS output_id,e.exact_text_or_observation AS quote,e.evidence_kind AS kind,
                e.locator,e.quality_status AS quality,e.review_status,
                g.trust_label AS trust,g.knowledge_scope AS scope,g.citation,g.quote_sha256,g.locator_sha256,
                (SELECT decision FROM project_evidence_reviews r WHERE r.project_id=e.project_id AND r.evidence_id=e.id) AS review_decision,
                e.created_at
            FROM project_evidence e JOIN project_evidence_grounding g
             ON g.project_id=e.project_id AND g.evidence_id=e.id
            JOIN project_sources s ON s.project_id=e.project_id AND s.id=e.source_id
             AND s.active_version_id=e.source_version_id
            WHERE e.project_id=%s ORDER BY e.id''',(pid,))
        ee=[dict(x) for x in cur.fetchall()]
        cur.execute('''SELECT rel.evidence_id_a AS a,rel.evidence_id_b AS b,rel.relation
             FROM project_evidence_relationships rel WHERE rel.project_id=%s ORDER BY rel.evidence_id_a,rel.evidence_id_b''',(pid,))
        rel=[dict(x) for x in cur.fetchall()]
    for e in ee:
        if hashlib.sha256(e['quote'].encode('utf-8')).hexdigest()!=e['quote_sha256'] or sha(e['locator'])!=e['locator_sha256']:
            raise ContextProblem('Evidence цитата или локатор не проходят проверку.','evidence_integrity',409)
    payload=build(project_id=pid,profile_revision=profile['revision'],profile_hash=profile['digest'],
                  profile_data=profile['profile'],source_rows=ss,output_rows=outs,evidence_rows=ee,relationship_rows=rel)
    return payload

def preview(pid):
    _pid(pid)
    with _conn() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        payload=_assemble(conn,pid)
    return {'context':payload,'context_sha256':sha(payload),'input_fingerprint':sha(payload),
            'candidate':True,'requires_explicit_publish':True}

def _verify_pins(conn,pid,ctx,payload):
    """Revalidate pinned immutable source/analysis/evidence identities and hashes."""
    sources=conn.execute('''SELECT source_id,source_version_id FROM project_context_source_pins
         WHERE project_id=%s AND context_id=%s''',(pid,ctx)).fetchall()
    expected={(s['source_id'],s['source_version_id']) for s in payload['source_manifest'] if s['source_version_id']}
    if {(str(a),str(b)) for a,b in sources}!=expected:
        raise ContextProblem('Source pins не соответствуют snapshot.','pin_integrity',409)
    outputs=conn.execute('''SELECT p.output_id,p.output_sha256,p.schema_version,o.output,o.output_sha256
         FROM project_context_analysis_pins p JOIN source_processing_outputs o
            ON o.project_id=p.project_id AND o.id=p.output_id
         WHERE p.project_id=%s AND p.context_id=%s''',(pid,ctx)).fetchall()
    expected_o={r['output_id']:(r['output_sha256'],r['schema_version']) for r in payload['analysis_refs']}
    if set(str(r[0]) for r in outputs)!=set(expected_o):
        raise ContextProblem('Analysis pins не соответствуют snapshot.','pin_integrity',409)
    for oid,digest,schema,data,real in outputs:
        if (digest,schema)!=expected_o[str(oid)] or digest!=real or sha(data)!=digest:
            raise ContextProblem('Analysis output SHA изменён.','output_integrity',409)
    evidence=conn.execute('''SELECT p.evidence_id,g.quote_sha256,g.locator_sha256,e.exact_text_or_observation,e.locator
         FROM project_context_grounded_evidence_pins p
           JOIN project_evidence e ON e.project_id=p.project_id AND e.id=p.evidence_id
           JOIN project_evidence_grounding g ON g.project_id=p.project_id AND g.evidence_id=p.evidence_id
         WHERE p.project_id=%s AND p.context_id=%s''',(pid,ctx)).fetchall()
    expected_e={e['evidence_id']:(e['exact_text_sha256'],e['locator_sha256']) for e in payload['evidence_manifest']}
    if set(str(r[0]) for r in evidence)!=set(expected_e):
        raise ContextProblem('Evidence pins не соответствуют snapshot.','pin_integrity',409)
    for eid,qh,lh,text,loc in evidence:
        if ((qh,lh)!=expected_e[str(eid)] or
            hashlib.sha256(text.encode('utf-8')).hexdigest()!=qh or sha(loc)!=lh):
            raise ContextProblem('Grounded evidence изменено.','evidence_integrity',409)


def _snapshot(conn,pid,ctx,*,include_payload=True):
    row=conn.execute('''SELECT s.id,s.context_version,s.context_sha256,s.context_payload,s.source_manifest,
            s.profile_revision,s.profile_hash,s.created_at,
            a.id,a.decision,a.approved_by,a.approval_reason,a.approved_at,
            b.input_fingerprint
         FROM project_context_snapshots s
         JOIN project_context_build_inputs b ON b.project_id=s.project_id AND b.context_id=s.id
         LEFT JOIN project_context_approvals a ON a.project_id=s.project_id AND a.context_id=s.id
         WHERE s.project_id=%s AND s.id=%s''',(pid,ctx)).fetchone()
    if not row:raise ContextProblem('Контекст не найден.','not_found',404)
    id,version,digest,payload,manifest,pr,ph,created,aid,decision,by,reason,when,fingerprint=row
    if sha(payload)!=digest or payload['source_manifest']!=manifest:
        raise ContextProblem('Хеш опубликованного снимка повреждён.','context_integrity',409)
    _verify_pins(conn,pid,ctx,payload)
    result={'context_id':str(id),'project_id':pid,'context_version':version,'context_sha256':digest,
        'profile_revision':pr,'profile_hash':ph,'created_at':created.isoformat(),
        'input_fingerprint':fingerprint,'approval':{
            'approval_id':str(aid),'decision':decision,'approved_by':by,
            'reason':reason,'approved_at':when.isoformat()} if aid else None,
        'approved':decision in ('approved','approved_partial')}
    if include_payload:result['context']=payload
    return result

def create_snapshot(pid,body):
    _pid(pid)
    if not isinstance(body,dict) or set(body)!={'expected_context_sha256'} or not isinstance(body.get('expected_context_sha256'),str):
        raise ContextProblem('Требуется хеш подтверждённого preview.','expected_hash_required',400)
    expected=body['expected_context_sha256']
    with _conn() as conn:
        # Per-project serialisation of context version allocation.
        row=conn.execute('SELECT id FROM project_registry WHERE id=%s FOR UPDATE',(pid,)).fetchone()
        if not row:raise ContextProblem('Проект не существует.','not_found',404)
        payload=_assemble(conn,pid);digest=sha(payload)
        if expected!=digest:raise ContextProblem('Источники изменились после просмотра preview.','stale_preview',409)
        old=conn.execute('''SELECT id FROM project_context_snapshots WHERE project_id=%s AND context_sha256=%s
                       ORDER BY context_version DESC LIMIT 1''',(pid,digest)).fetchone()
        if old:return {**_snapshot(conn,pid,old[0],include_payload=False),'created':False}
        from psycopg.types.json import Jsonb
        version=conn.execute('SELECT coalesce(max(context_version),0)+1 FROM project_context_snapshots WHERE project_id=%s',(pid,)).fetchone()[0]
        context_id=conn.execute('''INSERT INTO project_context_snapshots
          (project_id,context_version,context_sha256,profile_revision,profile_hash,schema_version,
           context_payload,source_manifest) VALUES(%s,%s,%s,%s,%s,'project-context/v1',%s,%s)
           RETURNING id''',(pid,version,digest,payload['profile']['revision'],payload['profile']['hash'],
                            Jsonb(payload),Jsonb(payload['source_manifest']))).fetchone()[0]
        conn.execute('''INSERT INTO project_context_build_inputs(project_id,context_id,input_fingerprint,builder_version)
            VALUES(%s,%s,%s,%s)''',(pid,context_id,digest,BUILDER_VERSION))
        for s in payload['source_manifest']:
            if s['source_version_id']:
                conn.execute('''INSERT INTO project_context_source_pins(project_id,context_id,source_id,source_version_id)
                    VALUES(%s,%s,%s,%s)''',(pid,context_id,s['source_id'],s['source_version_id']))
        for o in payload['analysis_refs']:
            conn.execute('''INSERT INTO project_context_analysis_pins
               (project_id,context_id,source_id,source_version_id,output_id,output_sha256,schema_version)
               VALUES(%s,%s,%s,%s,%s,%s,%s)''',(pid,context_id,o['source_id'],o['source_version_id'],o['output_id'],
                                                o['output_sha256'],o['schema_version']))
        for e in payload['evidence_manifest']:
            conn.execute('''INSERT INTO project_context_evidence_pins(project_id,context_id,evidence_id)
                VALUES(%s,%s,%s)''',(pid,context_id,e['evidence_id']))
            conn.execute('''INSERT INTO project_context_grounded_evidence_pins
               (project_id,context_id,source_id,source_version_id,evidence_id,quote_sha256,locator_sha256)
               VALUES(%s,%s,%s,%s,%s,%s,%s)''',(pid,context_id,e['source_id'],e['source_version_id'],e['evidence_id'],
                                                  e['exact_text_sha256'],e['locator_sha256']))
        result={**_snapshot(conn,pid,context_id,include_payload=False),'created':True}
    return result

def list_snapshots(pid,*,offset=0,limit=20):
    _pid(pid)
    if type(offset) is not int or type(limit) is not int or not 0<=offset<=100000 or not 1<=limit<=MAX_LIST:
        raise ContextProblem('Неверная пагинация.','invalid_pagination',400)
    with _conn() as conn:
        rows=conn.execute('''SELECT s.id,s.context_version,s.context_sha256,s.created_at,
           a.id,a.decision,s.profile_revision, s.context_payload->'coverage'
           FROM project_context_snapshots s LEFT JOIN project_context_approvals a
             ON a.project_id=s.project_id AND a.context_id=s.id
           WHERE s.project_id=%s ORDER BY s.context_version DESC OFFSET %s LIMIT %s''',(pid,offset,limit)).fetchall()
    return {'snapshots':[{'context_id':str(x[0]),'context_version':x[1],
             'context_sha256':x[2],'created_at':x[3].isoformat(),
             'approval_id':str(x[4]) if x[4] else None,'decision':x[5],
             'profile_revision':x[6],'coverage':x[7]} for x in rows], 'offset':offset,'limit':limit}

def get_snapshot(pid,context_id,*,manifest_only=False):
    _pid(pid);ctx=_uuid(context_id,'context_id')
    with _conn() as conn:result=_snapshot(conn,pid,ctx)
    if manifest_only:
        result['context']={'source_manifest':result['context']['source_manifest'],
             'analysis_refs':result['context']['analysis_refs'],
             'evidence_ids':result['context']['evidence_ids'],
             'profile':{'revision':result['profile_revision'],'hash':result['profile_hash']}}
    return result

def approved(pid):
    _pid(pid)
    with _conn() as conn:
        row=conn.execute('''SELECT s.id FROM project_context_snapshots s
           JOIN project_context_approvals a ON a.project_id=s.project_id AND a.context_id=s.id
           WHERE s.project_id=%s AND a.decision IN ('approved','approved_partial')
           ORDER BY s.context_version DESC LIMIT 1''',(pid,)).fetchone()
        if not row:raise ContextProblem('Утверждённый контекст не найден.','not_found',404)
        return _snapshot(conn,pid,row[0])

def decide(pid,context_id,body):
    _pid(pid);ctx=_uuid(context_id,'context_id')
    required={'decision','expected_context_sha256','acknowledge_partial','excluded_source_ids'}
    if not isinstance(body,dict) or set(body)!=required or body.get('decision') not in ('approved','approved_partial','rejected'):
        raise ContextProblem('Некорректные поля утверждения.','invalid_approval',400)
    if type(body.get('acknowledge_partial')) is not bool or not isinstance(body.get('excluded_source_ids'),list):
        raise ContextProblem('Нужно явное подтверждение исключений.','invalid_approval',400)
    if len(body['excluded_source_ids'])>MAX_LIST*3 or any(not isinstance(x,str) for x in body['excluded_source_ids']):
        raise ContextProblem('Неверный список исключений.','invalid_approval',400)
    with _conn() as conn:
        conn.execute('SELECT id FROM project_registry WHERE id=%s FOR UPDATE',(pid,))
        s=_snapshot(conn,pid,ctx)
        if s['context_sha256']!=body['expected_context_sha256']:
            raise ContextProblem('Неверный хеш контекста.','hash_mismatch',409)
        if s['approval'] is not None:raise ContextProblem('Решение уже записано и неизменно.','already_approved',409)
        data=s['context'];missing=sorted(x['source_id'] for x in data['unavailable_or_excluded_sources'])
        given=sorted(body['excluded_source_ids'])
        if len(given)!=len(set(given)) or given!=missing:
            raise ContextProblem('Нужно перечислить все исключённые источники без подмены.','exclusions_mismatch',409)
        if body['decision']=='approved' and (missing or body['acknowledge_partial']):
            raise ContextProblem('Полное утверждение невозможно при неполных источниках.','partial_context',409)
        if body['decision']=='approved_partial' and (not missing or not body['acknowledge_partial']):
            raise ContextProblem('Для partial нужно явное подтверждение неполноты.','partial_ack_required',409)
        if body['decision']!='rejected':
            current=_assemble(conn,pid)
            if sha(current)!=s['context_sha256']:
                raise ContextProblem('Контекст устарел. Сначала создайте новый snapshot.','stale_snapshot',409)
        reason='Explicitly acknowledged excluded sources: '+','.join(missing) if missing else None
        a=conn.execute('''INSERT INTO project_context_approvals
           (project_id,context_id,decision,approved_by,approval_reason)
           VALUES(%s,%s,%s,'local_owner',%s) RETURNING id''',(pid,ctx,body['decision'],reason)).fetchone()[0]
        result={'context_id':ctx,'approval_id':str(a),'decision':body['decision'],
            'context_sha256':s['context_sha256'],'excluded_source_ids':missing,
            'note':'Context approval is NOT automatic approval of individual external/model claims.'}
    return result

def compare(pid,left,right):
    a=get_snapshot(pid,left);b=get_snapshot(pid,right)
    x,y=a['context'],b['context']
    def diff(key,identity):
        old={str(item[identity]):item for item in x[key]};new={str(item[identity]):item for item in y[key]}
        return {'added':sorted(set(new)-set(old)),'removed':sorted(set(old)-set(new)),
                'changed':sorted(k for k in set(new)&set(old) if new[k]!=old[k])}
    return {'left':{'id':a['context_id'],'version':a['context_version'],'hash':a['context_sha256']},
        'right':{'id':b['context_id'],'version':b['context_version'],'hash':b['context_sha256']},
        'profile_changed':x['profile']!=y['profile'],
        'sources':diff('source_manifest','source_id'),
        'analyses':diff('analysis_refs','output_id'),
        'evidence':diff('evidence_manifest','evidence_id'),
        'warnings_before':x['warnings'],'warnings_after':y['warnings']}

def selection(pid,context_id,*,token_budget=1200):
    _pid(pid);ctx=_uuid(context_id,'context_id')
    with _conn() as conn:
        s=_snapshot(conn,pid,ctx);ids=s['context']['evidence_ids']
        # Bind texts ONLY to evidence listed in this historical snapshot; no
        # current-version join, so old approved snapshots remain reproducible.
        rows=conn.execute('''SELECT e.id,e.exact_text_or_observation FROM project_context_evidence_pins p
          JOIN project_evidence e ON e.project_id=p.project_id AND e.id=p.evidence_id
          WHERE p.project_id=%s AND p.context_id=%s''',(pid,ctx)).fetchall()
        quotes={str(id):quote for id,quote in rows}
        if sorted(quotes)!=ids:raise ContextProblem('Evidence pins повреждены.','pin_integrity',409)
        for e in s['context']['evidence_manifest']:
            if hashlib.sha256(quotes[e['evidence_id']].encode('utf-8')).hexdigest()!=e['exact_text_sha256']:
                raise ContextProblem('Evidence изменено.','evidence_integrity',409)
        result=choose_evidence(s['context'],quotes,token_budget=token_budget)
        result['context_id']=ctx;result['context_version']=s['context_version'];result['approval']=s['approval']
    return result
