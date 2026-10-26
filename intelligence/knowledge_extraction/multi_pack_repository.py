"""B Stage05: transactional immutable multi-pack persistence and exact replay.

Only verified A/B1 source text, model snapshot and catalog pricing may enter.
No OpenRouter completion, model dispatch, hidden retry, or spending consent.
"""
from __future__ import annotations
from decimal import Decimal
from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .full_pack_repository import _load, _rebuild, _verified_catalog
from .repository import KnowledgeInputRepository, _source_inputs
from .intake import IntakeError, _project, _uuid
from .multi_pack import (
    plan_multi_pack, normalize_multi_config, verify_multi_pack_plan,
    verify_multi_pack_payload, MODEL_ID,
)


def _summary(decision):
    return {k:v for k,v in decision.items() if k!="plan"}


def _verify_database_parts(conn, project_id, plan_id, bundle_id, plan):
    parts=conn.execute(
        """SELECT pack_index,payload_sha256,manifest_sha256,payload_bytes,
                  span_count,input_token_upper_estimate,estimated_usd,reserved_usd,manifest
           FROM knowledge_multi_pack_parts
          WHERE project_id=%s AND plan_id=%s AND bundle_id=%s ORDER BY pack_index""",
        (project_id,plan_id,bundle_id),
    ).fetchall()
    if len(parts)!=plan["pack_count"]:
        raise IntakeError("multi_pack_part_count_mismatch")
    for index, row in enumerate(parts):
        idx,payload_hash,mhash,size,count,tokens,expected,high,manifest=row
        expected_manifest=plan["packs"][index]
        if (idx!=index or manifest!=expected_manifest or
            mhash!=canonical_sha(manifest) or payload_hash!=manifest["payload_sha256"]
            or size!=manifest["payload_bytes"] or count!=manifest["span_count"]
            or tokens!=manifest["input_token_upper_estimate"]
            or expected!=Decimal(manifest["estimated_usd"])
            or high!=Decimal(manifest["reserved_usd"])):
            raise IntakeError("multi_pack_part_integrity")
    rows=conn.execute(
        """SELECT pack_index,chunk_id,fragment_id,char_start,char_end,
                  text_sha256,original_text_sha256,byte_start,byte_end
             FROM knowledge_multi_pack_spans
            WHERE project_id=%s AND plan_id=%s AND bundle_id=%s""",
        (project_id,plan_id,bundle_id),
    ).fetchall()
    expected={
        (part["pack_index"],span["chunk_id"]):(
            span["fragment_id"],span["char_start"],span["char_end"],
            span["text_sha256"],span["original_text_sha256"],
            span["byte_start"],span["byte_end"]
        )
        for part in plan["packs"] for span in part["spans"]
    }
    if len(rows)!=len(expected):
        raise IntakeError("multi_pack_span_count_mismatch")
    for idx,chunk,fid,start,end,digest,original,begin,finish in rows:
        if (idx,chunk) not in expected or (
            fid,start,end,digest,original,begin,finish
        )!=expected[(idx,chunk)]:
            raise IntakeError("multi_pack_span_integrity")


class MultiPackRepository:
    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.bundles=KnowledgeInputRepository(
            connection_factory,verify_local_files=verify_local_files)
        self.connection_factory=self.bundles.connection_factory
        self.resolver=self.bundles.resolver

    def prepare(self, project_id, bundle_id, config, *, allow_partial=False):
        """Append-only; create only a complete >=2-part plan within budget."""
        pid=_project(project_id)
        bid=_uuid(bundle_id,"invalid_bundle_id")
        normalized=normalize_multi_config(config)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            if conn.execute(
                "SELECT id FROM project_registry WHERE id=%s FOR UPDATE",(pid,),
            ).fetchone() is None:
                raise IntakeError("project_not_found")
            bundle,envelope=_load(conn,pid,bid,self.resolver,require_current=True)
            snap=_verified_catalog(conn)
            # No reuse of lossily selected A Evidence and no fake PDF text.
            texts=_rebuild(envelope,_source_inputs(conn,pid,envelope),bundle)
            decision,parts=plan_multi_pack(
                bundle,texts,snap,normalized,allow_partial=allow_partial)
            if decision["status"]!="fits_multi" or not parts:
                return _summary(decision)
            plan=decision["plan"]
            if not verify_multi_pack_plan(bundle,plan,parts) or (
                canonical_sha(plan)!=decision["plan_sha256"]
            ):
                raise IntakeError("multi_pack_plan_integrity")
            digest=decision["plan_sha256"]
            stored=conn.execute(
                """SELECT id,plan_sha256,plan FROM knowledge_multi_pack_plans
                   WHERE project_id=%s AND bundle_id=%s
                     AND model_snapshot_id=%s AND config_sha256=%s""",
                (pid,bid,snap["catalog_version_id"],plan["config_sha256"]),
            ).fetchone()
            if stored is not None:
                plan_id,sha,saved=stored
                if sha!=digest or saved!=plan or canonical_sha(saved)!=sha:
                    raise IntakeError("multi_pack_idempotency_conflict")
                _verify_database_parts(conn,pid,plan_id,bid,plan)
                return {**_summary(decision),"plan_id":str(plan_id),"created":False}
            plan_id=conn.execute(
                """INSERT INTO knowledge_multi_pack_plans(
                     project_id,bundle_id,bundle_sha256,model_snapshot_id,
                     model_id,catalog_sha256,pricing_sha256,config_sha256,
                     plan_sha256,pack_count,span_count,estimated_usd,
                     reserved_usd,budget_cap_usd,plan)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (pid,bid,plan["bundle_sha256"],snap["catalog_version_id"],
                 MODEL_ID,plan["catalog_sha256"],plan["pricing_sha256"],
                 plan["config_sha256"],digest,len(parts),
                 plan["coverage"]["span_count"],
                 plan["quote"]["estimated_usd"],plan["quote"]["reserved_usd"],
                 plan["quote"]["budget_cap_usd"],Jsonb(plan)),
            ).fetchone()[0]
            for part,payload in parts:
                verify_multi_pack_payload(part,payload)
                conn.execute(
                    """INSERT INTO knowledge_multi_pack_parts(
                        project_id,plan_id,bundle_id,pack_index,payload_sha256,
                        manifest_sha256,payload_bytes,span_count,
                        input_token_upper_estimate,estimated_usd,reserved_usd,manifest)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,plan_id,bid,part["pack_index"],part["payload_sha256"],
                     canonical_sha(part),part["payload_bytes"],part["span_count"],
                     part["input_token_upper_estimate"],part["estimated_usd"],
                     part["reserved_usd"],Jsonb(part)),
                )
                for span in part["spans"]:
                    conn.execute(
                        """INSERT INTO knowledge_multi_pack_spans(
                            project_id,plan_id,bundle_id,pack_index,chunk_id,
                            fragment_id,char_start,char_end,text_sha256,
                            original_text_sha256,byte_start,byte_end)
                          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (pid,plan_id,bid,part["pack_index"],span["chunk_id"],
                         span["fragment_id"],span["char_start"],span["char_end"],
                         span["text_sha256"],span["original_text_sha256"],
                         span["byte_start"],span["byte_end"]),
                    )
            return {**_summary(decision),"plan_id":str(plan_id),"created":True}

    def materialize(self, project_id, plan_id):
        """Historical byte-exact replay of every pack; no newer active profile."""
        pid=_project(project_id)
        ident=_uuid(plan_id,"invalid_plan_id")
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=conn.execute(
                """SELECT bundle_id,bundle_sha256,model_snapshot_id,model_id,
                          catalog_sha256,pricing_sha256,config_sha256,plan_sha256,
                          pack_count,span_count,estimated_usd,reserved_usd,
                          budget_cap_usd,plan
                   FROM knowledge_multi_pack_plans WHERE project_id=%s AND id=%s""",
                (pid,ident),
            ).fetchone()
            if row is None:
                raise IntakeError("multi_pack_not_found")
            (bid,bsha,mid,model_id,catalog_sha,price_sha,config_sha,
             plan_sha,count,spans,expected_cost,high_cost,cap,plan)=row
            bundle,envelope=_load(conn,pid,str(bid),self.resolver,require_current=False)
            if (
                canonical_sha(bundle)!=bsha or canonical_sha(plan)!=plan_sha
                or plan.get("bundle_sha256")!=bsha
                or plan.get("config_sha256")!=config_sha
                or plan.get("catalog_sha256")!=catalog_sha
                or plan.get("pricing_sha256")!=price_sha
                or plan.get("model_id")!=model_id
                or plan.get("catalog_version_id")!=mid
                or plan.get("pack_count")!=count
                or plan.get("coverage",{}).get("span_count")!=spans
                or Decimal(plan["quote"]["estimated_usd"])!=expected_cost
                or Decimal(plan["quote"]["reserved_usd"])!=high_cost
                or Decimal(plan["quote"]["budget_cap_usd"])!=cap
            ):
                raise IntakeError("multi_pack_registry_integrity")
            snap=conn.execute(
                """SELECT model_id,catalog_sha256,capabilities,pricing
                   FROM tb_model_snapshots WHERE id=%s AND model_id=%s""",
                (mid,MODEL_ID),
            ).fetchone()
            if not snap or snap[0]!=model_id or snap[1]!=catalog_sha or (
                canonical_sha(snap[3])!=price_sha
            ):
                raise IntakeError("multi_pack_catalog_pin_mismatch")
            _verify_database_parts(conn,pid,ident,bid,plan)
            config=normalize_multi_config(plan["config"])
            if canonical_sha({**config,"allow_partial":plan["allow_partial"]})!=config_sha:
                raise IntakeError("multi_pack_config_integrity")
            texts=_rebuild(envelope,_source_inputs(conn,pid,envelope),bundle)
            snapshot={"model_id":snap[0],"catalog_version_id":mid,
                      "catalog_sha256":snap[1],"capabilities":snap[2],
                      "pricing":snap[3]}
            decision,replayed=plan_multi_pack(
                bundle,texts,snapshot,config,allow_partial=plan["allow_partial"])
            if (decision["status"]!="fits_multi" or not replayed or
                decision["plan_sha256"]!=plan_sha or decision["plan"]!=plan or
                not verify_multi_pack_plan(bundle,plan,replayed)):
                raise IntakeError("multi_pack_replay_integrity")
            return {"project_id":pid,"plan_id":ident,"bundle_id":str(bid),
                    "plan_sha256":plan_sha,"plan":plan,
                    "parts":replayed,"provider_calls":0,
                    "tokenizer_check":"pending_exact_provider_preflight"}
