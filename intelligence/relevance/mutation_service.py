"""M39 trusted-host mutation dispatcher, not a public authentication service.

A mutation request first commits a durable project+actor+operation+key+SHA
pending intent. Domain service then runs on a *fresh idle autocommit* PG
connection (or same one after claim commit); the final minimal receipt is
committed separately. If the process crashes between domain commit and final
receipt the claim stays pending: NEVER retry blindly. An operator must audit
the domain fact and remediate safely; a different key is a new action.

The API never reserves money, dispatches, retries, settles a provider POST,
grants ownership from a client token or treats a header as actor identity.
"""
from __future__ import annotations

from .v1_contracts import (
    SREContractError,canonical_uuid,fingerprint,positive,project_id,record,
    reject,sha256_digest,
)
from .opus_consent import _actor,_authorized
from .opus_dispatch_intent import _committable
from .live_planner import _key
from .intake import attach_discovery
from .live_planner import create_live_run
from .live_recovery import cancel_live_run
from .opus_final_quote import issue_live_opus_quote
from .opus_consent import grant_live_sre_opus_consent,revoke_live_sre_opus_consent
from .live_human_review import record_live_sre_human_review
from .opus_reconciliation import reconcile_live_sre_opus_outcome

VERSION="sre-mutation-host/v1"
MAX_BIGINT=9223372036854775807
MAX_ATTACH=50
ALLOWED_OPERATIONS=frozenset((
    "attach","create_run","issue_quote","grant_consent",
    "revoke_consent","cancel_run","human_review","reconcile_unknown",
))
BODY_KEYS={
 "attach":frozenset(("discovery_ids",)),
 "create_run":frozenset(("publication_id","content_version_ids",
                        "pipeline_config_id","idempotency_key")),
 "issue_quote":frozenset(("run_id","item_id")),
 "grant_consent":frozenset(("run_id","item_id","quote_sha256",
                           "explicit_confirmation")),
 "revoke_consent":frozenset(("run_id","item_id","quote_sha256","reason")),
 "cancel_run":frozenset(("run_id",)),
 "human_review":frozenset(("run_id","item_id","category_key","human_decision",
                          "reason_code","expected_prior_review_id",
                          "explicit_confirmation")),
 "reconcile_unknown":frozenset(("run_id","item_id","intent_id")),
}
REQUIRED_KEYS={
    key:keys-frozenset(("reason","expected_prior_review_id"))
    for key,keys in BODY_KEYS.items()
}
SQL_CLAIM="""INSERT INTO sre_m39_mutation_receipts
(project_id,actor_id,operation,request_key,request_sha256)
VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING state"""
SQL_EXIST="""SELECT request_sha256,state,result
 FROM sre_m39_mutation_receipts
 WHERE project_id=%s AND actor_id=%s AND operation=%s
 AND request_key=%s FOR UPDATE"""
SQL_COMMIT="""UPDATE sre_m39_mutation_receipts
 SET state='committed',result=%s::jsonb,committed_at=clock_timestamp()
 WHERE project_id=%s AND actor_id=%s AND operation=%s
 AND request_key=%s AND request_sha256=%s AND state='pending'
 RETURNING state"""


def _id(value,path):
    return positive(value,path,maximum=MAX_BIGINT)


def _body(operation,payload):
    if operation not in ALLOWED_OPERATIONS:
        reject("operation","unsupported mutation")
    record(payload,"body",required=REQUIRED_KEYS[operation],
           optional=BODY_KEYS[operation]-REQUIRED_KEYS[operation])
    if operation=="attach":
        ids=payload["discovery_ids"]
        if type(ids) is not list or not 1<=len(ids)<=MAX_ATTACH:
            reject("discovery_ids","expected 1..50 explicitly selected D1 IDs")
        validated=[_id(x,"discovery_ids[]") for x in ids]
        if validated!=sorted(set(validated)):
            reject("discovery_ids","must be sorted unique D1 BIGINTs")
    elif operation=="create_run":
        canonical_uuid(payload["publication_id"],"publication_id")
        ids=payload["content_version_ids"]
        if type(ids) is not list or not 1<=len(ids)<=1000:
            reject("content_version_ids","expected 1..1000 D1 content versions")
        if ids!=sorted(set(_id(x,"content_version_id") for x in ids)):
            reject("content_version_ids","expected unique sorted BIGINTs")
        _id(payload["pipeline_config_id"],"pipeline_config_id")
        _key(payload["idempotency_key"])
    else:
        canonical_uuid(payload["run_id"],"run_id")
        if operation in ("issue_quote","grant_consent","revoke_consent",
                         "human_review","reconcile_unknown"):
            _id(payload["item_id"],"item_id")
        if operation in ("grant_consent","revoke_consent"):
            sha256_digest(payload["quote_sha256"],"quote_sha256")
        if operation=="revoke_consent":
            if payload.get("reason","user_revoked") not in (
                "user_revoked","operator_revoked","security_revoked"):
                reject("reason","unknown immutable revocation reason")
        if operation=="human_review":
            canonical_uuid(payload["category_key"],"category_key")
            if payload.get("expected_prior_review_id") is not None:
                canonical_uuid(payload["expected_prior_review_id"],
                               "expected_prior_review_id")
            if payload["human_decision"] not in ("match","no_match","uncertain"):
                reject("human_decision","not a recognized C9 decision")
            if type(payload["reason_code"]) is not str:
                reject("reason_code","explicit structured reason needed")
        if operation=="reconcile_unknown":
            canonical_uuid(payload["intent_id"],"intent_id")
    if operation in ("grant_consent","human_review"):
        if type(payload["explicit_confirmation"]) is not dict:
            reject("explicit_confirmation","exact human confirmation required")
    return dict(payload)


def _safe_result(operation,value):
    if type(value) is not dict:
        reject("result","domain service did not return a receipt","invalid_evidence")
    fields={
      "attach":("discovery_id","video_id","content_version_id","replayed"),
      "create_run":("run_id","status","replayed","manifest_sha256"),
      "issue_quote":("quote_sha256","expires_at","max_total_usd"),
      "grant_consent":("consent_id","quote_sha256","expires_at","dispatch_allowed"),
      "revoke_consent":("consent_id","status","dispatch_allowed"),
      "cancel_run":("run_id","status","unknown_items"),
      "human_review":("review_id","revision","human_decision","base_result_id"),
      "reconcile_unknown":("event_id","status","reason_code","intent_id"),
    }[operation]
    clean={k:value[k] for k in fields if k in value}
    # The ledger stores receipt identifiers/flags only, never source,
    # expensive provider responses, quote bodies or human notes.
    for key,val in clean.items():
        if type(val) not in (str,int,bool,type(None)):
            reject("result."+key,"unserializable receipt value","invalid_evidence")
        if isinstance(val,str) and len(val)>256:
            reject("result."+key,"oversized receipt field","invalid_evidence")
    return clean


class TrustedSREMutationService:
    """Inject host-only DB, actor permissions, C9/catalog/interactive approvals.

    catalog_resolver(pid,actor,operation) returns trusted exact pinned Opus
    catalog AND operator price/account caps. Never reads JSON body credentials.
    human_presence_verifier(pid,actor,body) must prove interactive human review
    separately from the echoed confirmation body; not a bot/job actor.
    """

    def __init__(self,*,connect,actor_authorizer,
                 catalog_resolver=None,human_presence_verifier=None):
        if not callable(connect) or not callable(actor_authorizer):
            raise ValueError("trusted connection and authorization required")
        self.connect=connect
        self.authorizer=actor_authorizer
        self.catalog=catalog_resolver
        self.presence=human_presence_verifier

    def _pricing(self,pid,actor,operation):
        if not callable(self.catalog):
            reject("operator_catalog","trusted exact Opus catalog not installed",
                   "rights_denied")
        config=self.catalog(pid,actor,operation)
        keys=("catalog_snapshot","operator_expected_sha256",
              "operator_endpoint_tag","operator_model_revision",
              "operator_maximum_usd")
        if type(config) is not dict or set(config)!=set(keys):
            reject("operator_catalog","trusted catalog pins unavailable",
                   "rights_denied")
        if type(config["catalog_snapshot"]) is not dict:
            reject("operator_catalog","catalog source invalid","rights_denied")
        return config

    def _execute(self,conn,pid,actor,operation,body):
        authorizer=self.authorizer
        if operation=="attach":
            out=[]
            for discovery in body["discovery_ids"]:
                result=attach_discovery(conn,pid,discovery)
                out.append({
                    "discovery_id":discovery,"video_id":result["video_id"],
                    "content_version_id":result["content_version_id"],
                    "replayed":result["replayed"],
                })
            return {"attached":out,"count":len(out)}
        if operation=="create_run":
            return create_live_run(conn,pid,body["publication_id"],
                body["content_version_ids"],body["pipeline_config_id"],
                body["idempotency_key"])
        if operation=="cancel_run":
            return cancel_live_run(conn,pid,body["run_id"])
        if operation=="revoke_consent":
            return revoke_live_sre_opus_consent(
                conn,pid,body["run_id"],body["item_id"],body["quote_sha256"],
                actor_id=actor,actor_authorizer=authorizer,
                reason=body.get("reason","user_revoked"))
        if operation=="human_review":
            if not callable(self.presence) or self.presence(pid,actor,body) is not True:
                reject("human_review","trusted interactive human presence required",
                       "actor_not_authorized")
            return record_live_sre_human_review(
                conn,pid,body["run_id"],body["item_id"],body["category_key"],
                reviewer_actor=actor,actor_authorizer=authorizer,
                human_decision=body["human_decision"],reason_code=body["reason_code"],
                explicit_confirmation=body["explicit_confirmation"],
                expected_prior_review_id=body.get("expected_prior_review_id"))
        if operation=="reconcile_unknown":
            # Safe UNKNOWN audit only. Caller cannot supply generation ID,
            # provider response, credentials or any paid-dispatch switch.
            return reconcile_live_sre_opus_outcome(
                conn,pid,body["run_id"],body["item_id"],body["intent_id"],
                actor_id=actor,actor_authorizer=authorizer,
                generation_id=None,api_key=None,provider_response=None)
        cfg=self._pricing(pid,actor,operation)
        if operation=="issue_quote":
            return issue_live_opus_quote(conn,pid,body["run_id"],body["item_id"],
                cfg["catalog_snapshot"],
                operator_expected_sha256=cfg["operator_expected_sha256"],
                operator_endpoint_tag=cfg["operator_endpoint_tag"],
                operator_model_revision=cfg["operator_model_revision"],
                operator_maximum_usd=cfg["operator_maximum_usd"])
        if operation=="grant_consent":
            return grant_live_sre_opus_consent(
                conn,pid,body["run_id"],body["item_id"],body["quote_sha256"],
                cfg["catalog_snapshot"],actor_id=actor,
                actor_authorizer=authorizer,
                explicit_confirmation=body["explicit_confirmation"],
                operator_expected_sha256=cfg["operator_expected_sha256"],
                operator_endpoint_tag=cfg["operator_endpoint_tag"],
                operator_model_revision=cfg["operator_model_revision"],
                operator_maximum_usd=cfg["operator_maximum_usd"])
        reject("operation","no allowed mutation backend")

    def perform(self,project,actor,operation,request_key,payload):
        pid=project_id(project)
        who=_actor(actor)
        if operation not in ALLOWED_OPERATIONS:
            reject("operation","unsupported action")
        _authorized(self.authorizer,pid,who,"api_mutate_sre_"+operation)
        key=_key(request_key)
        body=_body(operation,payload)
        digest=fingerprint({
            "schema_version":VERSION,"project_id":pid,"actor_id":who,
            "operation":operation,"body":body,
        })
        with self.connect() as conn:
            _committable(conn)
            # CLAIM is committed BEFORE first domain side effect. A crashed
            # service is intentionally not automatically retried/reclaimed.
            with conn.transaction():
                inserted=conn.execute(
                    SQL_CLAIM,(pid,who,operation,key,digest)).fetchone()
                if inserted is None:
                    saved=conn.execute(
                        SQL_EXIST,(pid,who,operation,key)).fetchone()
                    if saved is None or saved[0]!=digest:
                        reject("idempotency_key","same key with changed actor/request",
                               "idempotency_conflict")
                    if saved[1]=="committed" and type(saved[2]) is dict:
                        return {
                            "schema_version":VERSION,"operation":operation,
                            "replayed":True,
                            "state":"previously_committed_not_current_authorization",
                            "receipt":dict(saved[2]),
                            "paid_calls":0,
                        }
                    reject("idempotency_key",
                           "in-doubt original attempt; audited operator recovery required",
                           "mutation_in_doubt")
            # The existing domain function owns its own atomic transaction;
            # M32/M29 require the connection to be IDLE before starting.
            # In particular, none of these routes reserves or dispatches paid.
            value=self._execute(conn,pid,who,operation,body)
            if operation=="attach":
                rows=value["attached"]
                receipt={"count":len(rows),
                         "ids":[x["content_version_id"] for x in rows]}
            else:
                receipt=_safe_result(operation,value)
            import json
            serialized=json.dumps(receipt,sort_keys=True,separators=(",",":"))
            if len(serialized.encode("utf-8"))>4096:
                reject("mutation_receipt","receipt exceeds storage bound")
            with conn.transaction():
                row=conn.execute(SQL_COMMIT,(
                    serialized,pid,who,operation,key,digest)).fetchone()
                if row is None:
                    reject("mutation_receipt","lost replay claim",
                           "mutation_in_doubt")
            return {
                "schema_version":VERSION,"operation":operation,
                "replayed":False,"state":"committed",
                "receipt":receipt,"paid_calls":0,
            }


__all__=["VERSION","ALLOWED_OPERATIONS","BODY_KEYS","REQUIRED_KEYS",
         "SQL_CLAIM","SQL_EXIST","SQL_COMMIT",
         "TrustedSREMutationService"]
