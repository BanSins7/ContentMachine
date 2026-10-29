"""GB-MOD-29 conservative release audit and external trust gate.

A clean diff does NOT imply a merge-ready implementation. CI status JSON,
synthetic gold, test fixtures, and raw unsigned booleans cannot authorize
production B1–B8 or UAE qualification. Nothing here merges, bills or writes
to any other module branch.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re

VERSION="group-b-release-gate/v1"
SHA=re.compile(r"[a-f0-9]{40}\Z")
CORE_MAIN_SHA="218f5f3163ccc7179a4809ba4386a1d1b6ff80f5"
EXACT_INTEGRATION_PATHS=frozenset({
    "app.py","site/index.html","site/projects.js",
    ".github/workflows/group-b-module.yml",
})


class ReleaseState(str,Enum):
    BLOCKED="blocked"
    AUDITED="audited"
    READY="ready"


@dataclass(frozen=True,slots=True)
class ReleaseDecision:
    state:ReleaseState
    blocking:tuple[str,...]
    main_sha:str
    group_b_sha:str
    scope_changes:int
    production_eligible:bool=False

    @property
    def can_merge(self):
        return (self.state is ReleaseState.READY and
                not self.blocking and self.production_eligible is True)


def _allowed(path):
    if (type(path) is not str or not path or
        path.startswith("/") or "\\" in path or
        ".." in PurePosixPath(path).parts):
        return False
    if path in EXACT_INTEGRATION_PATHS:
        return True
    if path.startswith("intelligence/group_b/"):
        return path.endswith((".py",".txt",".json"))
    if path.startswith("intelligence/migrations/"):
        return re.fullmatch(
            r"05[5-9]_group_b_[a-z0-9_]+\.sql",path
        ) is not None
    if path.startswith("tests/"):
        return (
            re.fullmatch(r"test_group_b_[a-z0-9_]+\.py",path[6:]) is not None or
            path=="tests/test_group_b_ui.cjs" or
            re.fullmatch(r"tests/fixtures/group_b_[a-z0-9_]+\.json",path) is not None
        )
    if path.startswith("docs/"):
        return re.fullmatch(r"docs/GROUP_B_[A-Z0-9_]+\.(md|txt)",path) is not None
    if path.startswith("scripts/"):
        return re.fullmatch(r"scripts/group_b_[a-z0-9_]+\.py",path) is not None
    return path in ("site/group-b.js","site/group-b.css")


def audit_scope(changes,*,main_sha,group_b_sha,
                deleted_paths=(),verified_git_diff=False):
    """Return structural scope audit, not release authorization."""
    issues=[]
    if (type(main_sha) is not str or not SHA.fullmatch(main_sha) or
        type(group_b_sha) is not str or not SHA.fullmatch(group_b_sha) or
        main_sha==group_b_sha):
        issues.append("git_commit_identity_invalid")
    if verified_git_diff is not True:
        issues.append("git_diff_not_verified")
    if type(changes) not in (tuple,list) or not changes:
        issues.append("git_diff_empty_or_missing")
        changes=()
    if type(deleted_paths) not in (tuple,list):
        issues.append("deleted_paths_invalid")
        deleted_paths=()
    if any(not _allowed(p) for p in changes):
        issues.append("foreign_module_or_unlisted_path_changed")
    if len(set(changes))!=len(changes):
        issues.append("duplicate_diff_path")
    if deleted_paths:
        issues.append("deleted_file_in_standalone_release")
    if "intelligence/migrations/005_content_features_b.sql" in changes:
        issues.append("sql005_authoritative_source_modified")
    return ReleaseDecision(
        ReleaseState.BLOCKED if issues else ReleaseState.AUDITED,
        tuple(sorted(set(issues))),main_sha,group_b_sha,len(changes),
        False,
    )


@dataclass(frozen=True,slots=True)
class ExternalReleaseEvidence:
    """Versioned refs, *not* independently trustworthy by mere existence."""
    group_b_head_sha:str
    base_main_sha:str
    linux_qa_run_id:str
    windows_qa_run_id:str
    pg17_qa_run_id:str
    qa_artifact_sha256s:tuple[str,str,str]
    peer_group_a_sha:str
    peer_qdp_sha:str
    peer_group_c_sha:str
    peer_unified_analytics_sha:str
    source_rights_gold_evidence_sha256:str
    actual_model_quality_receipt_sha256:str
    full_seven_port_uae_e2e_receipt_sha256:str


def gate_release(scope:ReleaseDecision, evidence:ExternalReleaseEvidence|None,
                 *,trusted_attestor=None)->ReleaseDecision:
    """Trusted attestor MUST verify external immutable CI runs at exact SHA,
    real-source gold/model F1, QDP/A/UAE/C e2e and source media rights.

    A caller-created SHA checksum of fixtures is never a signed receipt.
    No attestor installed => ALWAYS blocked, even if all fields are provided.
    """
    reasons=set(scope.blocking)
    if scope.state is not ReleaseState.AUDITED or scope.production_eligible:
        reasons.add("structural_diff_not_proven")
    if not isinstance(evidence,ExternalReleaseEvidence):
        reasons.add("real_release_evidence_missing")
    else:
        if (evidence.group_b_head_sha!=scope.group_b_sha or
            evidence.base_main_sha!=scope.main_sha):
            reasons.add("evidence_not_pinned_to_current_branch_and_main")
        for x in (evidence.peer_group_a_sha,evidence.peer_qdp_sha,
                  evidence.peer_group_c_sha,evidence.peer_unified_analytics_sha):
            if type(x) is not str or not SHA.fullmatch(x):
                reasons.add("peer_commit_evidence_invalid")
    if not callable(getattr(trusted_attestor,"verify_real_release",None)):
        reasons.add("trusted_release_attestor_not_installed")
    else:
        # External attestor must verify CI job conclusion/artifact and real
        # gold/e2e/permissions. Never trust self-reported dict statuses.
        try:
            verified=(trusted_attestor.verify_real_release(
                scope.main_sha,scope.group_b_sha,evidence) is True
                if isinstance(evidence,ExternalReleaseEvidence) else False)
        except Exception:
            verified=False
        if not verified:
            reasons.add("real_release_attestation_not_verified")
    if reasons:
        return ReleaseDecision(ReleaseState.BLOCKED,tuple(sorted(reasons)),
                               scope.main_sha,scope.group_b_sha,
                               scope.scope_changes,False)
    return ReleaseDecision(ReleaseState.READY,(),scope.main_sha,
                           scope.group_b_sha,scope.scope_changes,True)
