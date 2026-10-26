"""RTB-FIX-02-B: durable immutable B9/A8 Research Input binding.

No payable quote, consent, C-run, model dispatch or mutation of B8/B9/A8.
A caller cannot persist arbitrary JSON: the service always reads verified B9/A.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _reviewer, _auth
from .deep_v2_lifecycle import validate_frozen_input
from .pipeline_contracts import PipelineError
from .pipeline_repository import _connect
from .research_contracts_v2 import _project, _uuid
from .research_input import RTBResearchInputService


def _verify_payload(payload: dict, project_id: str, snapshot_id: str) -> dict:
    frozen = validate_frozen_input(payload)
    if frozen["project_id"] != project_id or frozen["b9_snapshot_id"] != snapshot_id:
        raise PipelineError("rtb_v2_binding_project_snapshot_mismatch", 409)
    return frozen


class RTBDeepV2BindingRepository:
    """Idempotently freeze one exact B9-approved Research Input for future runs."""

    def __init__(self, connection_factory=None, *, research=None):
        self.connection_factory = connection_factory or _connect
        self.research = research or RTBResearchInputService(connection_factory)

    @staticmethod
    def _row_to_record(row):
        return {
            "binding_id": str(row[0]),
            "snapshot_id": str(row[1]),
            "research_input_sha256": row[2],
            "research_input": row[3],
        }

    def pin(self, project_id: str, snapshot_id: str, reviewer_id: str) -> dict:
        """Persist only after B9/A8 replay and inside project-scoped transaction."""
        pid = _project(project_id)
        sid = _uuid(snapshot_id, "rtb_v2_binding_snapshot_id")
        actor = _reviewer(reviewer_id)
        frozen = _verify_payload(self.research.preview(pid, sid, actor), pid, sid)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                # The SQL insert trigger also verifies B9/handoff/A identities
                # and enabled reviewer status, not only the Python caller.
                inserted = conn.execute(
                    """INSERT INTO tb_deep_v2_research_bindings(
                      project_id,knowledge_snapshot_id,knowledge_snapshot_sha256,
                      handoff_id,handoff_sha256,context_id,context_sha256,
                      research_input_sha256,research_input,created_by)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                      ON CONFLICT(project_id,knowledge_snapshot_id) DO NOTHING
                      RETURNING id""",
                    (pid, sid, frozen["b9_snapshot_sha256"],
                     frozen["b9_handoff_id"], frozen["b9_handoff_sha256"],
                     frozen["a_context_id"], frozen["a_context_sha256"],
                     frozen["research_input_sha256"], Jsonb(frozen), actor),
                ).fetchone()
                row = conn.execute(
                    """SELECT id,knowledge_snapshot_id,research_input_sha256,
                              research_input,knowledge_snapshot_sha256,
                              handoff_id,handoff_sha256,context_id,context_sha256
                         FROM tb_deep_v2_research_bindings
                        WHERE project_id=%s AND knowledge_snapshot_id=%s""",
                    (pid, sid),
                ).fetchone()
                if row is None:
                    raise PipelineError("rtb_v2_binding_not_persisted", 503)
                identity = (
                    row[4] == frozen["b9_snapshot_sha256"]
                    and str(row[5]) == frozen["b9_handoff_id"]
                    and row[6] == frozen["b9_handoff_sha256"]
                    and str(row[7]) == frozen["a_context_id"]
                    and row[8] == frozen["a_context_sha256"]
                )
                if not identity or row[2] != frozen["research_input_sha256"] or row[3] != frozen:
                    raise PipelineError("rtb_v2_binding_immutable_replay_mismatch", 409)
                result = self._row_to_record(row)
                result["created"] = bool(inserted)
                result["provider_calls"] = 0
                result["paid_execution_authorized"] = False
                return result

    def get(self, project_id: str, binding_id: str, reviewer_id: str) -> dict:
        """Read exact pin and independently replay B9/A8 approval before return."""
        pid = _project(project_id)
        bid = _uuid(binding_id, "rtb_v2_binding_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            _auth(conn, pid, actor)
            row = conn.execute(
                """SELECT id,knowledge_snapshot_id,research_input_sha256,research_input
                   FROM tb_deep_v2_research_bindings
                  WHERE project_id=%s AND id=%s""",
                (pid, bid),
            ).fetchone()
        if row is None:
            raise PipelineError("rtb_v2_binding_not_found", 404)
        record = self._row_to_record(row)
        stored = validate_frozen_input(record["research_input"])
        if stored["research_input_sha256"] != record["research_input_sha256"]:
            raise PipelineError("rtb_v2_binding_integrity_failed", 409)
        current = _verify_payload(self.research.preview(
            pid, record["snapshot_id"], actor
        ), pid, record["snapshot_id"])
        if current != stored:
            raise PipelineError("rtb_v2_binding_source_changed", 409)
        return {**record, "provider_calls": 0, "paid_execution_authorized": False}
