"""PIE-MOD-35: a *strict* standalone release gate, never a pass-by-declaration.

CI, QA, a reviewer's assertion or a model digest alone cannot prove external
rights, actual independent science, or paid transaction reconciliation.
The gate consumes bounded, machine-generated test receipts and explicit
independent human attestations, and FAILS CLOSED on any missing gate.
This module has no permission to merge, publish, bill or open any network.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

SCHEMA="pie-standalone-release-audit/v1"
_SHA=re.compile(r"[0-9a-f]{64}\Z")
_GATE_NAMES=(
    "python_full_suite",
    "js_studio_suite",
    "compileall",
    "pg17_fresh",
    "pg17_upgrade",
    "pg17_isolation",
    "pg17_immutability",
    "pg17_two_worker_fencing",
    "pg17_crash_recovery",
    "synthetic_gold_math",
    "synthetic_holdout_drift",
    "performance_10000",
    "windows_compatibility",
    "linux_compatibility",
    "uae_current_rights",
    "peer_wire_versions",
    "independent_source_holdout",
    "human_reviewer_independence",
    "cost_billing_reconciliation",
    "source_license_release",
    "publisher_permission",
    "migration_203_deployed_checksum",
)
MACHINE_GATES=frozenset((
    "python_full_suite","js_studio_suite","compileall","pg17_fresh",
    "pg17_upgrade","pg17_isolation","pg17_immutability",
    "pg17_two_worker_fencing","pg17_crash_recovery","synthetic_gold_math",
    "synthetic_holdout_drift","performance_10000",
    "windows_compatibility","linux_compatibility",
    "migration_203_deployed_checksum",
))
EXTERNAL_GATES=frozenset(_GATE_NAMES)-MACHINE_GATES


class ReleaseGateError(ValueError):
    """Metadata invalid; no domain source payload is ever embedded."""


def _json(value:dict)->str:
    return json.dumps(value,sort_keys=True,separators=(",",":"),
                      ensure_ascii=False,allow_nan=False)


@dataclass(frozen=True,slots=True)
class GateReceipt:
    name:str
    state:str
    source:str
    evidence_sha256:str|None=None
    note_code:str="not_measured"

    def __post_init__(self):
        if (type(self.name) is not str or self.name not in _GATE_NAMES
            or type(self.state) is not str
            or self.state not in ("not_run","passed","failed","blocked")
            or type(self.source) is not str
            or self.source not in ("local_test_runner","independent_qualified_attestation","unavailable")
            or type(self.note_code) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}",self.note_code)
            or (self.evidence_sha256 is not None and
                (type(self.evidence_sha256) is not str or not _SHA.fullmatch(self.evidence_sha256)))
            or (self.state=="passed" and
                (self.evidence_sha256 is None or
                 (self.name in MACHINE_GATES and self.source!="local_test_runner")
                 or (self.name in EXTERNAL_GATES and self.source!="independent_qualified_attestation")))
            or (self.state=="not_run" and self.source!="unavailable")):
            raise ReleaseGateError("Invalid, ungrounded or out-of-scope release receipt")


@dataclass(frozen=True,slots=True)
class StandaloneReadiness:
    artifact_sha256:str
    branch_sha256:str
    status:str
    gates_passed:int
    gates_total:int
    failed_gates:tuple[str,...]
    missing_gates:tuple[str,...]
    _json:str

    @property
    def may_merge(self)->bool:return False

    @property
    def may_publish(self)->bool:return False

    @property
    def can_run_paid_model(self)->bool:return False

    def as_dict(self)->dict:return json.loads(self._json)
    def to_json(self)->str:return self._json


def assess_standalone_readiness(
    branch_sha256:str, receipts:tuple[GateReceipt,...]
)->StandaloneReadiness:
    if (type(branch_sha256) is not str or not _SHA.fullmatch(branch_sha256)
        or type(receipts) is not tuple
        or any(type(x) is not GateReceipt for x in receipts)
        or len(set(x.name for x in receipts))!=len(receipts)):
        raise ReleaseGateError("Exact code commit and unique typed evidence required")
    supplied={x.name:x for x in receipts}
    statuses={name:supplied[name].state if name in supplied else "not_run"
              for name in _GATE_NAMES}
    failed=tuple(k for k,v in statuses.items() if v=="failed")
    missing=tuple(k for k,v in statuses.items() if v!="passed" and v!="failed")
    passed=sum(v=="passed" for v in statuses.values())
    status=("failed_validation" if failed else
            "blocked_unverified" if missing else
            "candidate_verified_pending_explicit_merge_authorization")
    # Even full test pass is not approval to merge a code branch, publish an
    # Insight or send a paid request. User must separately approve merge.
    evidence={k:{"state":statuses[k],
                  "source":supplied[k].source if k in supplied else "unavailable",
                  "evidence_sha256":supplied[k].evidence_sha256 if k in supplied else None,
                  "note_code":supplied[k].note_code if k in supplied else "not_measured"}
              for k in _GATE_NAMES}
    body={
        "schema_version":SCHEMA,"branch_sha256":branch_sha256,
        "status":status,"gates":evidence,
        "gates_passed":passed,"gates_total":len(_GATE_NAMES),
        "failed_gates":list(failed),"missing_gates":list(missing),
        "independently_verified_scientific_claim":False,
        "human_release_approved":False,
        "may_merge":False,"may_publish":False,
        "can_run_paid_model":False,
        "report_scope":"candidate_only_not_live_release",
    }
    raw=_json(body)
    return StandaloneReadiness(sha256(raw.encode("utf-8")).hexdigest(),
                               branch_sha256,status,passed,len(_GATE_NAMES),
                               failed,missing,raw)
