"""Actual Group A → Group B certified delivery, backed by existing durable tables.

There is no synthetic B run: QDP independently creates B's accepted draft.
GA and B are joined by the canonical QDP RUN, video, source and active
source-policy version. Their QDP group preparation digests are deliberately
independent. All operational trust comes from live QDP, SQL and GA/B adapters.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.delivery import deliver_once
from intelligence.group_a.group_b_exact_transport import (
    _event, _proof, GroupBBindDenied, GroupBExactBindTransport,
)
from intelligence.group_b.adapters.group_a import read_exact_a
from intelligence.group_b.lineage import bind_exact_group_a
from intelligence.qualified_data.preparation import read_current_preparation


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _row(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip((c.name for c in cursor.description), row))


def require_scope(project_id: str | None):
    if project_id is not None and (
        type(project_id) is not str or len(project_id)!=32 or
        any(x not in "0123456789abcdef" for x in project_id)
    ):
        raise GroupBBindDenied("project_scope_invalid")


class GroupABTransport:
    """Real DB-backed implementation of GA's native exact B commit transport.

    Each method owns a separate connection; GA20 calls this OUTSIDE its outbox
    claim transaction. Rechecking pinned bytes/rights on a new connection
    also protects unknown-outcome/retried deliveries.
    """
    def __init__(self, connect):
        if not callable(connect):
            raise GroupBBindDenied("database_connection_required")
        self.connect=connect

    def select_b_run(self, event):
        """Reject ambiguity: never bind a guessed latest B or other project."""
        with self.connect() as conn:
            with conn.transaction():
                rows=conn.execute(
                    """SELECT br.id::text
                         FROM group_b_runs br
                         JOIN group_b_inbox bi ON bi.id=br.inbox_id
                           AND bi.project_id=br.project_id
                           AND bi.video_id=br.video_id
                           AND bi.source_id=br.source_id
                         JOIN group_b_inbox_receipts bir
                           ON bir.project_id=br.project_id
                           AND bir.inbox_id=bi.id AND bir.run_id=br.id
                         JOIN qdp_group_current_preparations pb
                           ON pb.project_id=br.project_id
                           AND pb.run_id=bi.qdp_run_id
                           AND pb.video_id=br.video_id
                           AND pb.analysis_group='B' AND pb.state='ready'
                           AND pb.input_sha256=bi.preparation_sha256
                           AND pb.source_id=br.source_id
                           AND pb.source_policy_version_id=%s
                         JOIN qdp_group_current_preparations pa
                           ON pa.project_id=br.project_id
                           AND pa.run_id=bi.qdp_run_id
                           AND pa.video_id=br.video_id
                           AND pa.analysis_group='A' AND pa.state='ready'
                           AND pa.source_id=br.source_id
                           AND pa.source_policy_version_id=%s
                         JOIN ga_runs gr
                           ON gr.project_id=br.project_id
                           AND gr.qdp_run_id=bi.qdp_run_id
                           AND gr.video_id=br.video_id AND gr.source_id=br.source_id
                           AND gr.source_policy_version_id=%s
                           AND gr.qdp_input_sha256=pa.input_sha256
                           AND gr.id=%s::uuid
                           AND gr.state='completed' AND gr.invalidated_at IS NULL
                         JOIN ga_results ga
                           ON ga.run_id=gr.id AND ga.id=%s
                           AND ga.project_id=br.project_id
                           AND ga.result_sha256=%s AND ga.state='completed'
                        WHERE br.project_id=%s AND br.video_id=%s
                          AND br.source_id=%s AND bi.qdp_run_id=%s::uuid
                          AND br.content_type IN ('video','carousel')
                          AND br.state IN ('draft','quoted','queued','running',
                                           'partial','needs_review','completed')
                          AND bi.status='accepted'
                        ORDER BY br.id LIMIT 2""",
                    (event["source_policy_version_id"],)*3 +
                    (event["ga_run_id"],event["ga_result_id"],
                     event["result_sha256"],event["project_id"],event["video_id"],
                     event["source_id"],event["qdp_run_id"]),
                ).fetchall()
        if len(rows)!=1:
            raise GroupBBindDenied(
                "b_run_unavailable" if not rows else "b_run_ambiguous"
            )
        return str(rows[0][0])

    def read_b_committed_pin(self,b_run_id,event):
        if not _uuid(b_run_id):
            raise GroupBBindDenied("invalid_b_run")
        with self.connect() as conn:
            with conn.transaction():
                row=_row(conn.execute(
                    """SELECT b.project_id,b.video_id,b.source_id,
                              i.qdp_run_id::text AS qdp_run_id,
                              b.video_a_analysis_id,b.carousel_a_analysis_id,
                              b.a_snapshot_sha256,b.media_manifest_sha256,
                              b.source_policy_version_id,b.state
                         FROM group_b_runs b
                         JOIN group_b_inbox i ON i.project_id=b.project_id
                           AND i.id=b.inbox_id
                        WHERE b.id=%s::uuid AND b.project_id=%s
                          AND i.status='accepted' FOR SHARE OF b,i""",
                    (b_run_id,event["project_id"]),
                ))
                if row is None:
                    raise GroupBBindDenied("b_run_disappeared")
                if (row["project_id"]!=event["project_id"] or
                    row["video_id"]!=event["video_id"] or
                    row["source_id"]!=event["source_id"] or
                    row["qdp_run_id"]!=event["qdp_run_id"]):
                    raise GroupBBindDenied("b_run_lineage_mismatch")
                attrs=("video_a_analysis_id","carousel_a_analysis_id",
                       "a_snapshot_sha256","media_manifest_sha256",
                       "source_policy_version_id")
                if all(row[x] is None for x in attrs):
                    if row["state"]!="draft":
                        raise GroupBBindDenied("b_unpinned_not_draft")
                    return None
                if (row["video_a_analysis_id"]!=event["video_a_analysis_id"] or
                    row["carousel_a_analysis_id"]!=event["carousel_a_analysis_id"] or
                    row["a_snapshot_sha256"]!=event["result_sha256"] or
                    row["media_manifest_sha256"]!=event["manifest_sha256"] or
                    row["source_policy_version_id"]!=event["source_policy_version_id"]):
                    raise GroupBBindDenied("b_a_pin_conflict")
                # Never treat SQL column equality alone as authentication.
                # Revalidate source-current QDP A+B, exact GA JSON,
                # real native A003/A004, evidence coverage and media rights.
                checked=read_exact_a(
                    conn,b_run_id=b_run_id,ga_result_id=event["ga_result_id"],
                    expected_sha256=event["result_sha256"],
                )
                if (checked.ga_run_id!=event["ga_run_id"] or
                    checked.project_id!=event["project_id"] or
                    checked.source_policy_version_id!=event["source_policy_version_id"] or
                    checked.manifest_sha256!=event["manifest_sha256"]):
                    raise GroupBBindDenied("b_commit_origin_mismatch")
                return {
                    "pinned":True,
                    "b_run_id":b_run_id,
                    "project_id":row["project_id"],
                    "qdp_run_id":row["qdp_run_id"],
                    "video_id":row["video_id"],
                    "source_id":row["source_id"],
                    "result_sha256":row["a_snapshot_sha256"],
                    "manifest_sha256":row["media_manifest_sha256"],
                    "source_policy_version_id":row["source_policy_version_id"],
                    "video_a_analysis_id":row["video_a_analysis_id"],
                    "carousel_a_analysis_id":row["carousel_a_analysis_id"],
                }

    def bind(self, *, b_run_id, ga_result_id, expected_sha256):
        return bind_exact_group_a(
            self.connect,b_run_id=b_run_id,ga_result_id=ga_result_id,
            expected_sha256=expected_sha256,
        )

    def __call__(self,payload,*,idempotency_key):
        event=_event(payload,idempotency_key)
        b_run_id=self.select_b_run(event)
        before=self.read_b_committed_pin(b_run_id,event)
        if before is None:
            self.bind(b_run_id=b_run_id,ga_result_id=event["ga_result_id"],
                      expected_sha256=event["result_sha256"])
        after=self.read_b_committed_pin(b_run_id,event)
        attested=_proof(event,b_run_id,after)
        return {
            "ga_result_id":event["ga_result_id"],
            "result_sha256":event["result_sha256"],
            "consumer_receipt_sha256":canonical_sha256({
                "schema_version":"ga24-b-committed-exact-pin/v1",
                **attested,
            }),
        }

    def verify_receipt(self, envelope):
        """GA20 ACK requires independent readback of the real committed B pin."""
        if type(envelope) is not dict or set(envelope)!={"scope","claim","receipt"}:
            return False
        scope,claim,received=(envelope[x] for x in ("scope","claim","receipt"))
        if (not isinstance(scope,Mapping) or type(received) is not dict or
            not hasattr(claim,"payload") or
            not _uuid(scope.get("qdp_run_id"))):
            return False
        try:
            event=_event(
                dict(claim.payload),
                claim.run_id+":"+str(claim.ga_result_id)+":"+claim.result_sha256,
            )
            if any(scope.get(k)!=event.get(k) for k in (
                "project_id","qdp_run_id","video_id","source_id",
                "source_policy_version_id","manifest_sha256"
            )) or (scope.get("qdp_input_sha256") is None):
                return False
            b_run_id=self.select_b_run(event)
            pin=self.read_b_committed_pin(b_run_id,event)
            attested=_proof(event,b_run_id,pin)
            expected=canonical_sha256({
                "schema_version":"ga24-b-committed-exact-pin/v1",**attested,
            })
            return (received=={
                "ga_result_id":claim.ga_result_id,
                "result_sha256":claim.result_sha256,
                "consumer_receipt_sha256":expected,
            })
        except Exception:
            return False


class GroupABPipeline:
    """One bounded GA outbox -> actual B run commit/ACK step."""
    def __init__(self, connect, qdp_connect):
        if not callable(connect) or not callable(qdp_connect):
            raise GroupBBindDenied("trusted_connection_factories_required")
        self.connect=connect
        self.qdp_connect=qdp_connect
        self.transport=GroupABTransport(connect)

    def verify_current(self, scope):
        """Actual QDP SRE/D1/media current reads, independently for A and B."""
        if not isinstance(scope,Mapping):
            return False
        try:
            project,run,video=(scope[x] for x in
                               ("project_id","qdp_run_id","video_id"))
            a=read_current_preparation(
                project,run,video,"A",connect=self.qdp_connect,
            )
            b=read_current_preparation(
                project,run,video,"B",connect=self.qdp_connect,
            )
            if a.get("state")!="ready" or b.get("state")!="ready":
                return False
            aa,bb=a.get("inputs"),b.get("inputs")
            if type(aa) is not dict or type(bb) is not dict:
                return False
            return (
                a.get("input_sha256")==scope["qdp_input_sha256"] and
                aa.get("group")=="A" and bb.get("group")=="B" and
                aa.get("result_schema")=="qdp-preparation/v2" and
                bb.get("result_schema")=="qdp-preparation/v2" and
                all(x.get("enrichment_source_id")==scope["source_id"] and
                    x.get("source_policy_version_id")==scope["source_policy_version_id"]
                    for x in (aa,bb)) and
                aa.get("media_asset_id")==bb.get("media_asset_id") and
                aa.get("media_sha256")==bb.get("media_sha256") and
                aa.get("media_kind")==bb.get("media_kind")=="video"
            )
        except Exception:
            return False

    def deliver_once(self, *, worker_id: str, project_id: str | None = None):
        require_scope(project_id)
        with self.connect() as conn:
            return deliver_once(
                conn,worker_id=worker_id,project_id=project_id,
                verify_current=self.verify_current,transport=self.transport,
                verify_receipt=self.transport.verify_receipt,
            )
