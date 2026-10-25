"""Stage12 B8: authorized, explicit, auditable review of B7-approved handoff.

The API is an internal trusted-server service, not a public HTTP endpoint.
Review decisions are immutable hash-chained events with revision-based CAS.
No model calls, mutation of B4/B5/B6, auto approvals, or B9 publication.
"""
from __future__ import annotations

from collections import Counter

from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from .intake import IntakeError, _project, _uuid
from .quality_gate import FactQualityGateRepository

REVIEW_VERSION = "knowledge-human-review/v1"
REVIEW_ENGINE = "b8-explicit-human/1.0.0"
GENESIS = "0" * 64
CONFLICT_ACTIONS = frozenset((
    "select_a", "select_b", "reject_both", "not_conflicting", "defer",
))
MAX_REVISIONS = 50_000


def _reject(code):
    raise IntakeError(code)


def _reviewer(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 160
            or value != value.strip() or "\x00" in value):
        _reject("b8_reviewer_id_invalid")
    return value


def _text(value, *, required=False, limit=800):
    if value is None and not required:
        return None
    if (not isinstance(value,str) or not 1 <= len(value.strip()) <= limit
            or "\x00" in value or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)):
        _reject("b8_text_invalid")
    return value.strip()


def _auth(conn, project, reviewer):
    row=conn.execute(
        """SELECT enabled FROM knowledge_fact_review_authorizations
           WHERE project_id=%s AND reviewer_id=%s""",
        (project,reviewer),
    ).fetchone()
    if row is None or row[0] is not True:
        _reject("b8_reviewer_not_authorized")


def _events(conn,project,session):
    rows=conn.execute(
        """SELECT revision,reviewer_id,target_kind,target_id,decision,
                  reviewed_text,note,source_support_affirmed,subject_scope_affirmed,
                  prev_sha256,event_sha256
             FROM knowledge_fact_review_events
            WHERE project_id=%s AND session_id=%s ORDER BY revision""",
        (project,session),
    ).fetchall()
    prior=GENESIS
    result=[]
    for idx,row in enumerate(rows,1):
        (revision,actor,kind,target,action,edited,note,affirmed,scope,prev,digest)=row
        event={
            "schema_version":"knowledge-review-event/v1",
            "engine_version":REVIEW_ENGINE,
            "session_id":session,"revision":revision,
            "reviewer_id":actor,"target_kind":kind,"target_id":target,
            "decision":action,"reviewed_text":edited,"note":note,
            "source_support_affirmed":affirmed,
            "subject_scope_affirmed":scope,
            "prev_sha256":prev,
        }
        if revision!=idx or prev!=prior or canonical_sha(event)!=digest:
            _reject("b8_event_chain_integrity")
        result.append({**event,"event_sha256":digest})
        prior=digest
    return result


def _latest(events):
    groups,conflicts={},{}
    warnings=None
    for e in events:
        if e["target_kind"]=="group":
            groups[e["target_id"]]=e
        elif e["target_kind"]=="conflict":
            conflicts[e["target_id"]]=e
        else:
            warnings=e
    return groups,conflicts,warnings


def _approved_review(packet, group_events, conflict_events, warnings):
    """Verify no undecided group/conflict and no unresolved incompatible vote."""
    groups=packet["groups"]
    conflicts=packet["conflicts"]
    if set(group_events)!={g["group_id"] for g in groups}:
        _reject("b8_undecided_groups")
    if set(conflict_events)!={c["conflict_id"] for c in conflicts}:
        _reject("b8_undecided_conflicts")
    if packet["warnings"] and warnings is None:
        _reject("b8_warnings_not_acknowledged")
    if warnings is not None and warnings["decision"]!="acknowledge":
        _reject("b8_warnings_not_acknowledged")
    for c in conflicts:
        choice=conflict_events[c["conflict_id"]]["decision"]
        if choice=="defer":
            _reject("b8_conflict_deferred")
        left=group_events[c["group_a"]]["decision"]
        right=group_events[c["group_b"]]["decision"]
        if (choice=="select_a" and (left,right)!=("approve","reject")
                or choice=="select_b" and (left,right)!=("reject","approve")
                or choice=="reject_both" and (left,right)!=("reject","reject")):
            _reject("b8_conflict_vote_inconsistent")
    approved=[]
    rejected=[]
    for g in groups:
        action=group_events[g["group_id"]]
        member_ids=sorted(x["proposal_id"] for x in g["members"])
        if action["decision"]=="approve":
            if (not action["source_support_affirmed"]
                    or not action["subject_scope_affirmed"]):
                _reject("b8_approval_assertions_missing")
            approved.append({
                "group_id":g["group_id"],
                "kind":g["fact_kind"],"source_role":g["source_role"],
                "subject_scope":g["subject_scope"],"subject_id":g["subject_id"],
                "original_text":g["original_text"],
                "reviewed_text":action["reviewed_text"],
                "human_edited":action["reviewed_text"]!=g["original_text"],
                "proposal_ids":member_ids,
                "citations":[{
                    "proposal_id":m["proposal_id"],
                    "source_quote":m["source_quote"],
                    "source_chunk_id":m["source_chunk_id"],
                    "original_provenance":m["provenance"],
                    "b5_receipt_sha256":m["b5_receipt_sha256"],
                } for m in g["members"]],
                "affirmed_source_support_by_reviewer":True,
                "affirmed_subject_scope_by_reviewer":True,
                "source_text_match_is_not_objective_truth":True,
                "review_event_sha256":action["event_sha256"],
            })
        elif action["decision"]=="reject":
            rejected.append({
                "group_id":g["group_id"],
                "proposal_ids":member_ids,
                "review_event_sha256":action["event_sha256"],
            })
        else:
            _reject("b8_invalid_group_vote")
    return approved,rejected


class FactHumanReviewRepository:
    """Human-reviewed outcomes use separate, explicit grants and CAS revisions.

    Caller must already have authenticated reviewer identity in its privileged
    server context. The reviewer_id argument is never an authentication proof.
    No user-facing route or bearer-key handling is installed by Stage12.
    """

    def __init__(self,connection_factory=None, *, verify_local_files=True):
        self.gate=FactQualityGateRepository(
            connection_factory,verify_local_files=verify_local_files)
        self.connection_factory=self.gate.connection_factory

    def _packet(self,pid,rid):
        assessment=self.gate.materialize(pid,rid)
        if (assessment["report"]["decision"]=="blocked"
                or assessment["report"]["handoff_allowed"] is not True
                or assessment["report"]["publishing_allowed"] is not False):
            _reject("b8_quality_gate_blocked")
        b6=self.gate.dedup.materialize(pid,rid)
        b4=self.gate.dedup.grounding.normalization.materialize(pid,rid)
        if (assessment["report"]["b6_report_sha256"]!=b6["report_sha256"]
                or assessment["report"]["b4_report_sha256"]!=b4["report_sha256"]
                or assessment["report"]["counts"]["unique_groups"]!=len(b6["groups"])):
            _reject("b8_upstream_report_mismatch")
        rows={r["proposal_id"]:r for r in b4["rows"]}
        indexed={g["group_id"]:{**g,"members":[]} for g in b6["groups"]}
        for m in b6["members"]:
            if m["status"]!="grouped":
                continue
            raw=rows.get(m["proposal_id"])
            if (raw is None or raw.get("normalized") is None
                    or m["group_id"] not in indexed):
                _reject("b8_missing_original_proposal")
            prop=raw["normalized"]
            if prop["proposal_id"]!=m["proposal_id"]:
                _reject("b8_original_proposal_mismatch")
            indexed[m["group_id"]]["members"].append({
                "pack_index":m["pack_index"],
                "candidate_index":m["candidate_index"],
                "proposal_id":m["proposal_id"],
                "original_text":prop["original_text"],
                "source_quote":prop["source_quote"],
                "source_chunk_id":prop["source_chunk_id"],
                "source_locator":prop["source_locator"],
                "provenance":m["provenance"],
                "b5_receipt_sha256":m["b5_receipt_sha256"],
            })
        groups=[indexed[k] for k in sorted(indexed)]
        for g in groups:
            g["members"].sort(key=lambda m:(m["pack_index"],m["candidate_index"]))
            if len(g["members"])!=g["member_count"]:
                _reject("b8_group_citation_count_mismatch")
        return {
            "schema_version":"knowledge-review-packet/v1",
            "run_id":rid,"batch_id":assessment["report"]["batch_id"],
            "gate_id":assessment["quality_gate_id"],
            "gate_report_sha256":assessment["report_sha256"],
            "dedup_id":b6["dedup_id"],
            "decision":assessment["report"]["decision"],
            "warnings":assessment["report"]["findings"],
            "source_audit":assessment["report"]["video_audit"],
            "blocked_member_counts":dict(Counter(
                m["status"] for m in b6["members"] if m["status"]!="grouped"
            )),
            "groups":groups,"conflicts":b6["conflicts"],
            "all_facts_unreviewed":True,"provider_calls":0,
        }

    def _session(self,conn,pid,sid,actor,packet, *, lock=False):
        suffix=" FOR UPDATE" if lock else ""
        row=conn.execute(
            """SELECT reviewer_id,gate_id,dedup_id,run_id,gate_report_sha256,
                      status,revision
               FROM knowledge_fact_review_sessions
              WHERE project_id=%s AND id=%s"""+suffix,
            (pid,sid),
        ).fetchone()
        if row is None:
            _reject("b8_session_not_found")
        if (row[0]!=actor or str(row[1])!=packet["gate_id"]
                or str(row[2])!=packet["dedup_id"]
                or str(row[3])!=packet["run_id"]
                or row[4]!=packet["gate_report_sha256"]):
            _reject("b8_session_identity_mismatch")
        events=_events(conn,pid,sid)
        if row[6]!=len(events):
            _reject("b8_session_revision_integrity")
        return row[5],row[6],events

    def open(self,project_id,run_id,reviewer_id):
        """Create/retrieve a pinned review; never opens if B7 is blocked."""
        pid,rid=_project(project_id),_uuid(run_id,"b8_run_id_invalid")
        actor=_reviewer(reviewer_id)
        packet=self._packet(pid,rid)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            conn.execute(
                """SELECT id FROM knowledge_fact_quality_gates
                   WHERE project_id=%s AND id=%s FOR UPDATE""",
                (pid,packet["gate_id"]),
            )
            prior=conn.execute(
                """SELECT id,reviewer_id FROM knowledge_fact_review_sessions
                   WHERE project_id=%s AND gate_id=%s""",
                (pid,packet["gate_id"]),
            ).fetchone()
            if prior:
                if prior[1]!=actor:
                    _reject("b8_review_session_already_assigned")
                return {"session_id":str(prior[0]),"created":False,
                        "gate_id":packet["gate_id"],"provider_calls":0}
            row=conn.execute(
                """SELECT grounding_id,normalization_id,batch_id
                   FROM knowledge_fact_quality_gates
                   WHERE project_id=%s AND id=%s""",
                (pid,packet["gate_id"]),
            ).fetchone()
            if row is None:
                _reject("b8_gate_not_found")
            sid=conn.execute(
                """INSERT INTO knowledge_fact_review_sessions(
                   project_id,gate_id,dedup_id,grounding_id,normalization_id,
                   run_id,batch_id,reviewer_id,gate_report_sha256)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid,packet["gate_id"],packet["dedup_id"],row[0],row[1],
                 rid,row[2],actor,packet["gate_report_sha256"]),
            ).fetchone()[0]
        return {"session_id":str(sid),"created":True,
                "gate_id":packet["gate_id"],"provider_calls":0}

    def inspect(self,project_id,session_id,reviewer_id):
        pid,sid=_project(project_id),_uuid(session_id,"b8_session_id_invalid")
        actor=_reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            link=conn.execute(
                """SELECT run_id FROM knowledge_fact_review_sessions
                   WHERE project_id=%s AND id=%s""",
                (pid,sid),
            ).fetchone()
        if link is None:
            _reject("b8_session_not_found")
        packet=self._packet(pid,str(link[0]))
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            status,revision,events=self._session(conn,pid,sid,actor,packet)
            groups,conflicts,warnings=_latest(events)
        return {**packet,"session_id":sid,"status":status,"revision":revision,
                "group_decisions":groups,"conflict_decisions":conflicts,
                "warning_acknowledgement":warnings,
                "event_count":len(events),"provider_calls":0}

    def record(self,project_id,session_id,reviewer_id, *,
               expected_revision,target_kind,target_id,decision,
               reviewed_text=None,note=None,
               source_support_affirmed=False,subject_scope_affirmed=False):
        """One immutable human event under a global session CAS fence."""
        pid,sid=_project(project_id),_uuid(session_id,"b8_session_id_invalid")
        actor=_reviewer(reviewer_id)
        if (type(expected_revision) is not int or expected_revision<0
                or expected_revision>=MAX_REVISIONS):
            _reject("b8_revision_invalid")
        if (type(source_support_affirmed) is not bool
                or type(subject_scope_affirmed) is not bool):
            _reject("b8_affirmations_invalid")
        note=_text(note,limit=2000)
        if target_kind not in ("group","conflict","warnings"):
            _reject("b8_target_kind_invalid")
        if not isinstance(target_id,str):
            _reject("b8_target_id_invalid")
        if target_kind=="warnings":
            if (target_id!="gate_warnings" or decision!="acknowledge"
                    or reviewed_text is not None or note is None):
                _reject("b8_warning_ack_invalid")
            edited=None
        elif target_kind=="group":
            if decision not in ("approve","reject"):
                _reject("b8_group_decision_invalid")
            if decision=="reject":
                if reviewed_text is not None or source_support_affirmed or subject_scope_affirmed:
                    _reject("b8_rejection_cannot_edit")
                edited=None
            else:
                if not source_support_affirmed or not subject_scope_affirmed:
                    _reject("b8_human_affirmation_required")
                edited=_text(reviewed_text) if reviewed_text is not None else None
        else:
            if (decision not in CONFLICT_ACTIONS or reviewed_text is not None
                    or source_support_affirmed or subject_scope_affirmed
                    or note is None):
                _reject("b8_conflict_decision_invalid")
            edited=None

        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            link=conn.execute(
                """SELECT run_id FROM knowledge_fact_review_sessions
                   WHERE project_id=%s AND id=%s""",
                (pid,sid),
            ).fetchone()
        if link is None:
            _reject("b8_session_not_found")
        packet=self._packet(pid,str(link[0]))
        gid_map={g["group_id"]:g for g in packet["groups"]}
        if target_kind=="group":
            group=gid_map.get(target_id)
            if group is None:
                _reject("b8_group_not_reviewable")
            if decision=="approve":
                edited=edited if edited is not None else group["original_text"]
                if edited!=group["original_text"] and note is None:
                    _reject("b8_edit_requires_reason")
            else:
                edited=group["original_text"]
        elif target_kind=="conflict":
            if target_id not in {c["conflict_id"] for c in packet["conflicts"]}:
                _reject("b8_conflict_not_reviewable")
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            status,revision,events=self._session(conn,pid,sid,actor,packet,lock=True)
            if status!="open":
                _reject("b8_session_finalized")
            if revision!=expected_revision:
                _reject("b8_cas_revision_mismatch")
            if target_kind=="warnings" and not packet["warnings"]:
                _reject("b8_no_warnings_to_acknowledge")
            last=events[-1]["event_sha256"] if events else GENESIS
            evt={
                "schema_version":"knowledge-review-event/v1",
                "engine_version":REVIEW_ENGINE,
                "session_id":sid,"revision":revision+1,
                "reviewer_id":actor,"target_kind":target_kind,
                "target_id":target_id,"decision":decision,
                "reviewed_text":edited,"note":note,
                "source_support_affirmed":source_support_affirmed,
                "subject_scope_affirmed":subject_scope_affirmed,
                "prev_sha256":last,
            }
            digest=canonical_sha(evt)
            conn.execute(
                """INSERT INTO knowledge_fact_review_events(
                   project_id,session_id,revision,reviewer_id,target_kind,target_id,
                   decision,reviewed_text,note,source_support_affirmed,
                   subject_scope_affirmed,prev_sha256,event_sha256)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (pid,sid,revision+1,actor,target_kind,target_id,decision,
                 edited,note,source_support_affirmed,subject_scope_affirmed,last,digest),
            )
            conn.execute(
                """UPDATE knowledge_fact_review_sessions
                   SET revision=%s,updated_at=clock_timestamp()
                   WHERE project_id=%s AND id=%s AND revision=%s""",
                (revision+1,pid,sid,revision),
            )
        return {"session_id":sid,"revision":revision+1,
                "event_sha256":digest,"provider_calls":0}

    def _submission(self,pid,sid,actor,packet,events):
        group_events,conflict_events,warning=_latest(events)
        approved,rejected=_approved_review(
            packet,group_events,conflict_events,warning)
        decision_log=[{
            "conflict_id":c["conflict_id"],
            "group_a":c["group_a"],"group_b":c["group_b"],
            "rule":c["rule"],"decision":conflict_events[c["conflict_id"]]["decision"],
            "event_sha256":conflict_events[c["conflict_id"]]["event_sha256"],
        } for c in packet["conflicts"]]
        return {
            "schema_version":REVIEW_VERSION,
            "review_engine":REVIEW_ENGINE,
            "project_id":pid,
            "session_id":sid,"gate_id":packet["gate_id"],
            "gate_report_sha256":packet["gate_report_sha256"],
            "dedup_id":packet["dedup_id"],
            "run_id":packet["run_id"],"batch_id":packet["batch_id"],
            "reviewer_id":actor,"terminal_revision":len(events),
            "event_chain_last_sha256":events[-1]["event_sha256"] if events else GENESIS,
            "warning_ack_event_sha256":warning["event_sha256"] if warning else None,
            "approved_groups":approved,"rejected_groups":rejected,
            "conflict_resolutions":decision_log,
            "approved_count":len(approved),"rejected_count":len(rejected),
            "conflict_count":len(decision_log),
            "publication_eligible":bool(approved),
            "publication_performed":False,
            "objective_truth_guaranteed":False,
            "source_provenance_preserved":True,
            "review_status":"human_submitted",
            "provider_calls":0,
        }

    def submit(self,project_id,session_id,reviewer_id,*,expected_revision):
        """Finalize once only after all group and conflict votes and warning ack."""
        pid,sid=_project(project_id),_uuid(session_id,"b8_session_id_invalid")
        actor=_reviewer(reviewer_id)
        if type(expected_revision) is not int or not 0<=expected_revision<=MAX_REVISIONS:
            _reject("b8_revision_invalid")
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            link=conn.execute(
                "SELECT run_id FROM knowledge_fact_review_sessions WHERE project_id=%s AND id=%s",
                (pid,sid),
            ).fetchone()
        if link is None:
            _reject("b8_session_not_found")
        packet=self._packet(pid,str(link[0]))
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            status,revision,events=self._session(conn,pid,sid,actor,packet,lock=True)
            if revision!=expected_revision:
                _reject("b8_cas_revision_mismatch")
            body=self._submission(pid,sid,actor,packet,events)
            hash_value=canonical_sha(body)
            if status=="submitted":
                stored=conn.execute(
                    """SELECT id,submission,submission_sha256,terminal_revision
                       FROM knowledge_fact_review_submissions
                       WHERE project_id=%s AND session_id=%s""",
                    (pid,sid),
                ).fetchone()
                if (not stored or stored[1]!=body or stored[2]!=hash_value
                        or stored[3]!=revision):
                    _reject("b8_submitted_integrity")
                return {"submission_id":str(stored[0]),"created":False,
                        "approved_count":body["approved_count"],
                        "publication_eligible":body["publication_eligible"],
                        "provider_calls":0}
            row=conn.execute(
                """INSERT INTO knowledge_fact_review_submissions(
                   project_id,session_id,gate_id,dedup_id,reviewer_id,
                   terminal_revision,submission_sha256,submission)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid,sid,packet["gate_id"],packet["dedup_id"],actor,
                 revision,hash_value,Jsonb(body)),
            ).fetchone()[0]
            conn.execute(
                """UPDATE knowledge_fact_review_sessions
                   SET status='submitted',updated_at=clock_timestamp()
                   WHERE project_id=%s AND id=%s AND status='open'""",
                (pid,sid),
            )
        return {"submission_id":str(row),"created":True,
                "approved_count":body["approved_count"],
                "publication_eligible":body["publication_eligible"],
                "provider_calls":0}

    def materialize_submission(self,project_id,session_id,reviewer_id):
        """Historical A→B7 replay + immutable human event-chain verification."""
        data=self.inspect(project_id,session_id,reviewer_id)
        pid,sid=_project(project_id),_uuid(session_id,"b8_session_id_invalid")
        actor=_reviewer(reviewer_id)
        if data["status"]!="submitted":
            _reject("b8_not_submitted")
        packet={k:data[k] for k in (
            "schema_version","run_id","batch_id","gate_id","gate_report_sha256",
            "dedup_id","decision","warnings","source_audit",
            "blocked_member_counts","groups","conflicts","all_facts_unreviewed",
            "provider_calls")}
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            events=_events(conn,pid,sid)
            body=self._submission(pid,sid,actor,packet,events)
            stored=conn.execute(
                """SELECT id,submission,submission_sha256,terminal_revision
                   FROM knowledge_fact_review_submissions
                  WHERE project_id=%s AND session_id=%s""",
                (pid,sid),
            ).fetchone()
            if (stored is None or stored[1]!=body
                    or stored[2]!=canonical_sha(body)
                    or stored[3]!=len(events)):
                _reject("b8_submission_replay_mismatch")
        return {"submission_id":str(stored[0]),"submission":body,
                "submission_sha256":stored[2],"provider_calls":0}
