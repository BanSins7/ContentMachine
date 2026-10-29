"""One-event local SRE -> QDP dispatcher; explicit operator-scoped env.

Requires an AUTHORIZED project allowlist and actor from the host environment.
This CLI never starts an OpenRouter/DTK provider or auto-creates QDP jobs.
"""
from __future__ import annotations
import argparse
import json
import os
from intelligence.config import dsn,enabled
from intelligence.relevance.v1_contracts import project_id
from .sre_qdp_dispatch import dispatch_one_sre_qdp


def main(argv=None):
    parser=argparse.ArgumentParser(description="SRE LIVE -> QDP durable event delivery")
    parser.add_argument("--project",required=True)
    parser.add_argument("--publish-run",default=None,
                        help="Explicitly publish verified LIVE M36 item events for this run")
    parser.add_argument("--drain",type=int,default=1,
                        help="Maximum bounded SRE→QDP events to deliver (1..100)")
    args=parser.parse_args(argv)
    pid=project_id(args.project)
    if not 1<=args.drain<=100:
        parser.error("--drain must be 1..100")
    allowed=os.environ.get("CM_SRE_QDP_ALLOWED_PROJECTS","")
    approved={p.strip() for p in allowed.split(",") if p.strip()}
    actor=os.environ.get("CM_SRE_QDP_ACTOR","")
    flag=os.environ.get("CM_SRE_QDP_ENABLED","0")
    if (flag!="1" or pid not in approved or
        not actor or len(actor)>128 or not enabled() or not dsn()):
        print(json.dumps({"schema_version":"sre-qdp-cli/v1","state":"unavailable",
                          "reason_code":"operator_authorization_required"}))
        return 2
    def authorizer(project,who,action):
        return (project==pid and who==actor
                and action in {"deliver_sre_result_event","ack_sre_result_event",
                               "publish_sre_live_result","materialize_sre_invalidation"})
    def connect():
        import psycopg
        return psycopg.connect(dsn(),autocommit=True,connect_timeout=5)
    try:
        from intelligence.relevance.live_result_outbox import (
            publish_live_sre_result,materialize_sre_invalidation_intents,
        )
        if args.publish_run is not None:
            from intelligence.relevance.v1_contracts import canonical_uuid
            rid=canonical_uuid(args.publish_run,"run_id")
            with connect() as conn:
                items=conn.execute(
                    """SELECT id FROM sre_live_items
                       WHERE project_id=%s AND run_id=%s::uuid
                       ORDER BY id LIMIT 10001""",(pid,rid),
                ).fetchall()
            if not items or len(items)>10000:
                raise ValueError("Invalid or oversized LIVE run")
            for item in items:
                with connect() as conn:
                    publish_live_sre_result(
                        conn,pid,rid,item[0],actor_id=actor,
                        actor_authorizer=authorizer,
                    )
        with connect() as conn:
            materialize_sre_invalidation_intents(
                conn,pid,actor_id=actor,actor_authorizer=authorizer,limit=100,
            )
        delivered=[]
        for _ in range(args.drain):
            result=dispatch_one_sre_qdp(
                pid,sre_connect=connect,actor_id=actor,actor_authorizer=authorizer,
            )
            if result["state"]=="no_work":
                break
            delivered.append(result)
        result={"schema_version":"sre-qdp-cli/v1","project_id":pid,
                "state":"completed","delivered":delivered,
                "delivered_count":len(delivered),"provider_calls":0}
    except Exception as exc:
        print(json.dumps({"schema_version":"sre-qdp-cli/v1","state":"blocked",
                          "reason_code":getattr(exc,"code","unavailable")}))
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
