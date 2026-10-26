"""Stage 8: explicit release and quota reservation for DTK Collector runs.

A held plan is NEVER a license to search: only a trusted SQG query set,
reviewed source rights, verified local DTK read scope, operator archive
attestation, bounded zero-spend consent and separately enabled worker can
turn held jobs into *eligible* jobs. No HTTP search runs in this module.
"""
from __future__ import annotations

import re
from intelligence import config
from intelligence.connectors import dtk
from intelligence.taxonomy_builder.pipeline_contracts import canonical
from .contracts import CollectorError, PROJECT_RE, require_id, require_int
from .policy_gate import inspect_source_policy, require_policy, zero_spend_cap
from .repository import _connection, _run, _trusted_query_set

_KEY = re.compile(r"[A-Za-z0-9_.-]{12,100}\Z")
_REQUIRED = {
    "project_id", "run_id", "confirm", "idempotency_key",
    "max_spend_usd", "max_provider_requests", "acknowledge_archive_configuration"
}
_PERMISSIONS = (
    "automated_collection_authorized",
    "provider_usage_no_charge_verified",
    "upstream_archive_disabled_confirmed",
)


def worker_enabled() -> bool:
    return config.setting("CM_COLLECTOR_LIVE_ENABLED", "0") == "1"


def archive_attested() -> bool:
    # This is an operator assertion, NOT proof of actual upstream configuration.
    return config.setting("CM_DTK_ARCHIVE_DISABLED_CONFIRMED", "0") == "1"


def _validate_release(raw: dict) -> tuple[str,int,str,int]:
    if type(raw) is not dict or set(raw) != _REQUIRED:
        raise CollectorError("invalid_release", "Invalid or unexpected release fields")
    pid = require_id(raw["project_id"], "project_id", PROJECT_RE)
    run = require_int(raw["run_id"], "run_id", 1, 2**63-1)
    count = require_int(raw["max_provider_requests"], "max_provider_requests", 1, 50)
    key = raw["idempotency_key"]
    if type(key) is not str or not _KEY.fullmatch(key):
        raise CollectorError("invalid_idempotency_key", "Invalid consent key")
    if raw["confirm"] is not True or raw["acknowledge_archive_configuration"] is not True:
        raise CollectorError("consent_required", "Explicit provider execution approval required", 409)
    zero_spend_cap(raw["max_spend_usd"])
    return pid, run, key, count


def authorize_execution(raw: dict, *, connect=None, readiness=None) -> dict:
    """Separate opt-in release; one durable quota reservation, no submit.

    No untrusted QuerySet JSON or browser-supplied policy flags can bypass
    the database-verified query set and current operator configuration.
    """
    pid, run_id, consent_key, count = _validate_release(raw)
    if not worker_enabled() or not archive_attested():
        raise CollectorError("execution_disabled",
                             "Collector worker/archive approval must be configured server-side", 409)
    # A single harmless DTK auth/me GET (not TikTok search). Do not perform
    # external operations while holding a PostgreSQL transaction.
    ready = (readiness or dtk.status)()
    if not (ready.get("available") is True and ready.get("read_scope") is True):
        raise CollectorError("dtk_not_ready", "Local DTK read scope not verified", 409)
    with _connection(connect) as conn:
        with conn.transaction():
            row = _run(conn, pid, run_id, lock=True)
            grant = conn.execute(
                """SELECT consent_key,request_cap,policy_version_id
                   FROM collector_execution_grants WHERE run_id=%s""",
                (run_id,),
            ).fetchone()
            if grant is not None:
                if grant[0] != consent_key or grant[1] != count:
                    raise CollectorError("execution_consent_conflict",
                                         "Run was authorized with a different consent", 409)
                return {"run_id":run_id,"project_id":pid,
                        "status":row[5],"replayed":True,
                        "requests_authorized":count,"external_calls":0}
            if row[1] != "created" or row[5] != "planned_held" or row[2]:
                raise CollectorError("run_not_held", "Only non-cancelled held runs may be released",409)
            qid, query_set = _trusted_query_set(conn, str(conn.execute(
                "SELECT query_set_id FROM collector_runs WHERE run_id=%s", (run_id,)
            ).fetchone()[0]))
            if query_set.project_id != pid:
                raise CollectorError("project_scope_mismatch", "Project mismatch",409)
            snapshot = row[8]
            tasks = snapshot.get("tasks",[]) if isinstance(snapshot,dict) else []
            if (not isinstance(tasks,list) or len(tasks) != count or
                count != len({task["task_fingerprint"] for task in tasks}) or
                snapshot.get("project_id") != pid or
                snapshot.get("query_set_sha256") != query_set.fingerprint or
                snapshot.get("plan_hash") != row[7] or
                snapshot.get("source_id") != "dtk_tiktok"):
                raise CollectorError("plan_integrity", "Held plan does not match trusted query set",409)
            from .adapters.dtk_tiktok import prepare_keyword_task
            for task in tasks:
                prepare_keyword_task(task)
            actual = conn.execute(
                """SELECT task_sha256 FROM collector_provider_tasks
                   WHERE run_id=%s AND state='not_submitted' ORDER BY task_sha256""",
                (run_id,),
            ).fetchall()
            if sorted(v[0] for v in actual) != sorted(v["task_fingerprint"] for v in tasks):
                raise CollectorError("plan_integrity", "Held job manifest has changed",409)
            source_id = "dtk_tiktok"
            policy=inspect_source_policy(conn,source_id,max_requests=count,max_pages=1)
            require_policy(policy)
            if policy["policy_version_id"] != row[9]:
                raise CollectorError("stale_source_policy", "Rebuild plan after source policy change",409)
            perm_row=conn.execute(
                """SELECT permissions FROM source_policy_versions
                   WHERE id=%s AND source_id=%s AND is_active=TRUE FOR SHARE""",
                (row[9],source_id),
            ).fetchone()
            permissions=perm_row[0] if perm_row else None
            if not isinstance(permissions,dict) or any(
                permissions.get(k) is not True for k in _PERMISSIONS
            ):
                raise CollectorError("execution_rights_missing",
                                     "Reviewed automated usage, upstream archive and zero-charge claims required",409)
            # Reserve quota atomically with run release.
            quota=conn.execute(
                """SELECT window_start_at,limit_units,reserved_units,consumed_units
                   FROM source_quota_ledger
                   WHERE source_id=%s AND bucket_name='collector.requests'
                   AND window_start_at<=now() AND window_end_at>now()
                   ORDER BY window_start_at DESC LIMIT 1 FOR UPDATE""",
                (source_id,),
            ).fetchone()
            if (not quota or quota[1] is None
                or quota[1]-quota[2]-quota[3]<count):
                raise CollectorError("quota_insufficient", "Insufficient current provider request quota",409)
            conn.execute(
                """UPDATE source_quota_ledger SET reserved_units=reserved_units+%s,
                       updated_at=now() WHERE source_id=%s AND bucket_name='collector.requests'
                       AND window_start_at=%s""",
                (count,source_id,quota[0]),
            )
            consent={"kind":"collector_dtk_stage8/v1",
                     "zero_provider_spend_claimed_by_operator":True,
                     "upstream_archive_disabled_attested_by_operator":True,
                     "rights_checked_policy_version_id":row[9],
                     "allowed_operation":"search_posts","max_pages":1,
                     "max_provider_requests":count}
            conn.execute(
                """INSERT INTO collector_execution_grants
                   (run_id,source_id,policy_version_id,quota_window_start,
                    request_cap,reserved_remaining,consent_key,consent_snapshot)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (run_id,source_id,row[9],quota[0],count,count,
                 consent_key,canonical(consent)),
            )
            conn.execute("UPDATE collector_runs SET stage='released' WHERE run_id=%s",
                         (run_id,))
            conn.execute(
                """UPDATE search_runs SET state='queued' WHERE id=%s AND state='created'""",
                (run_id,),
            )
            conn.execute(
                """INSERT INTO audit_events(action,source_id,object_type,object_id,details)
                   VALUES ('collector.worker_authorized',%s,'collector_run',%s,%s::jsonb)""",
                (source_id,str(run_id),canonical({
                    "requests":count, "policy_version_id":row[9],
                    "quota_reserved":count,"provider_spend_cap_usd":"0.00",
                })),
            )
            return {"run_id":run_id,"project_id":pid,"status":"released",
                    "replayed":False,"requests_authorized":count,
                    "quota_reserved":count,"external_calls":0}


def cancel_released(conn, project_id: str, run_id: int) -> dict:
    """Called under existing run FOR UPDATE lock from repository.cancel_run."""
    row = _run(conn,project_id,run_id,lock=True)
    if row[5] not in ("released","cancelling"):
        raise CollectorError("run_not_cancellable", "Run is not running or held",409)
    tasks = conn.execute(
        """SELECT t.id,t.job_id,t.source_request_id
           FROM collector_provider_tasks t WHERE t.run_id=%s
           AND t.state='not_submitted' FOR UPDATE""", (run_id,),
    ).fetchall()
    unsubmitted=len(tasks)
    grant=conn.execute(
        """SELECT source_id,quota_window_start,reserved_remaining FROM collector_execution_grants
           WHERE run_id=%s FOR UPDATE""",(run_id,),
    ).fetchone()
    if grant and unsubmitted:
        if unsubmitted>grant[2]:
            raise CollectorError("quota_integrity", "Reservation mismatch",409)
        quota_return=conn.execute(
            """UPDATE source_quota_ledger SET reserved_units=reserved_units-%s,
               updated_at=now()
               WHERE source_id=%s AND bucket_name='collector.requests'
                     AND window_start_at=%s AND reserved_units>=%s
               RETURNING source_id""",
            (unsubmitted,grant[0],grant[1],unsubmitted),
        ).fetchone()
        grant_return=conn.execute(
            """UPDATE collector_execution_grants
               SET reserved_remaining=reserved_remaining-%s
               WHERE run_id=%s AND reserved_remaining>=%s
               RETURNING run_id""",
            (unsubmitted,run_id,unsubmitted),
        ).fetchone()
        if quota_return is None or grant_return is None:
            raise CollectorError("quota_release_race",
                                 "Cancellation quota release lost; transaction rolled back",409)
    conn.execute(
        """UPDATE collector_provider_tasks SET state='cancelled'
           WHERE run_id=%s AND state='not_submitted'""", (run_id,),
    )
    conn.execute(
        """UPDATE jobs SET state='cancelled',locked_by=NULL,lease_until=NULL,
           updated_at=now() WHERE id IN
           (SELECT job_id FROM collector_provider_tasks
            WHERE run_id=%s AND state='cancelled')
            AND state='queued'""", (run_id,),
    )
    conn.execute(
        """UPDATE source_requests SET state='failed',error_code='cancelled_before_submit',
             finished_at=now() WHERE id IN
             (SELECT source_request_id FROM collector_provider_tasks
              WHERE run_id=%s AND state='cancelled')
              AND state='planned'""",(run_id,),
    )
    active=conn.execute(
        """SELECT count(*) FROM collector_provider_tasks
           WHERE run_id=%s AND state IN ('submit_intent','submitted')""",
        (run_id,),
    ).fetchone()[0]
    conn.execute(
        """UPDATE search_runs SET cancel_requested=TRUE,
           state=CASE WHEN %s>0 THEN 'running' ELSE 'cancelled' END,
           finished_at=CASE WHEN %s>0 THEN NULL ELSE now() END WHERE id=%s""",
        (active,active,run_id),
    )
    conn.execute(
        "UPDATE collector_runs SET stage=%s WHERE run_id=%s",
        ("cancelling" if active else "cancelled",run_id),
    )
    return {"run_id":run_id,"project_id":project_id,
            "status":"cancelling" if active else "cancelled",
            "cancelled_unsubmitted":unsubmitted,"outstanding_upstream":active}
