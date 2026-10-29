"""UA-MOD-12: exact-version joined SRE/QDP/A/B/C candidate selection.

Only self-consistent producer PIN BUNDLES are tried; no independent latest
queries. Complete located refs are NOT an approved or qualified publication.
Current Group C cannot pass until its actual reviewed approval is available.
A caller must provide an authorized fresh transaction and pinned candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import EvidenceRef, InputScope, ReadState
from .lineage import CanonicalLineage, CanonicalLineagePins, resolve_canonical_lineage
from .sre_adapter import SREApprovedRelevancePins, SREApprovedRelevanceReader
from .qdp_adapter import QDPQualifiedPins, QDPQualifiedSetReader
from .group_a_adapter import GroupAVerifiedPins, GroupAVerifiedFactsReader
from .group_b_adapter import GroupBExactPins, GroupBExactReader
from .group_c_adapter import GroupCPersistedPins, GroupCPersistedScoreReader
from .source_policy_adapter import SourceRightsPins, SourcePolicyCurrentReader, GuardedUAEReads
from .ports import UAEPorts

JOIN_SCHEMA = "uae-joined-context/v1"
REQUIRED_PORTS = ("source_policy","d1","qdp","sre","group_a","group_b","group_c")
MAX_CANDIDATES = 48
GA_MEDIA_IDS_SQL = """
SELECT slide_index,media_asset_id FROM ga_media_refs
WHERE run_id=%s::uuid AND project_id=%s AND video_id=%s AND source_id=%s
ORDER BY slide_index
"""


def _fail(code: str) -> None:
    raise AnalyticsBoundaryError(code, "Exact UAE join refused: " + code)


def _utc(x: Any) -> bool:
    return (type(x) is datetime and x.tzinfo is not None
            and x.utcoffset() is not None
            and x.utcoffset().total_seconds() == 0)


@dataclass(frozen=True,slots=True)
class JoinedCandidatePins:
    lineage: CanonicalLineagePins
    rights: SourceRightsPins
    sre: SREApprovedRelevancePins
    qdp: QDPQualifiedPins
    group_a: GroupAVerifiedPins
    group_b: GroupBExactPins
    group_c: GroupCPersistedPins

    def __post_init__(self) -> None:
        for value,kind in (
            (self.lineage,CanonicalLineagePins),
            (self.rights,SourceRightsPins),
            (self.sre,SREApprovedRelevancePins),
            (self.qdp,QDPQualifiedPins),
            (self.group_a,GroupAVerifiedPins),
            (self.group_b,GroupBExactPins),
            (self.group_c,GroupCPersistedPins),
        ):
            if not isinstance(value,kind):
                _fail("join_invalid_pins")

    @property
    def scope(self) -> InputScope:
        return self.lineage.scope

    @property
    def fingerprint(self) -> str:
        p=self
        return canonical_hash({
            "wire":JOIN_SCHEMA,"scope":p.scope.scope_sha256,
            "lineage":p.lineage.identity_sha256,
            "rights":{
                "metadata":p.rights.metadata_id,
                "metric_ids":list(p.rights.metric_snapshot_ids),
                "media_ids":list(p.rights.media_asset_ids)},
            "sre":{
                "content_sha":p.sre.content_sha256,
                "tax_sha":p.sre.taxonomy_sha256,
                "contexts_sha":p.sre.contexts_sha256,
                "categories":list(p.sre.target_category_keys)},
            "qdp":p.qdp.binding_sha256,
            "A":{
                "run":p.group_a.ga_run_id,
                "analysis":p.group_a.analysis_id,
                "result":p.group_a.ga_result_id,
                "result_sha":p.group_a.result_sha256,
                "manifest":p.group_a.media_manifest_sha256,
                "method":p.group_a.method_version,
                "version":p.group_a.analysis_version},
            "B":{
                "run":p.group_b.b_run_id,
                "analysis":p.group_b.b_analysis_id,
                "blocks":list(p.group_b.block_evidence_sha256s),
                "config":p.group_b.config_sha256,
                "taxonomy":p.group_b.taxonomy_version,
                "version":p.group_b.analysis_version,
                "model":p.group_b.model_version,
                "prompt":p.group_b.prompt_version},
            "C":{
                "run":p.group_c.c_run_id,
                "metric":p.group_c.calculated_metric_id,
                "snapshots":[p.group_c.previous_snapshot_id,p.group_c.current_snapshot_id],
                "config":p.group_c.config_id,
                "config_version":p.group_c.config_version,
                "creator":p.group_c.creator_freeze_sha256,
                "prior":p.group_c.prior_freeze_sha256,
                "niche":p.group_c.niche_freeze_sha256},
        })

    @property
    def priority(self) -> tuple[int, ...]:
        return (
            self.group_c.current_snapshot_id,
            self.lineage.sre_content_version_id,
            self.lineage.sre_taxonomy_version_id,
            self.group_a.analysis_id,
            self.group_b.b_analysis_id,
            self.group_c.calculated_metric_id,
        )

    def binding_error(self, scope:InputScope) -> str | None:
        p=self
        if (any(x.scope!=scope for x in (
            p.lineage,p.rights,p.qdp,p.group_a,p.group_b,p.group_c))
            or p.sre.lineage!=p.lineage):
            return "join_scope_or_sre_lineage_mismatch"
        if not (p.lineage.metadata_id==p.rights.metadata_id==
                p.qdp.metadata_id==p.group_c.metadata_id):
            return "join_metadata_version_mismatch"
        if (p.lineage.metric_snapshot_id!=p.group_c.current_snapshot_id
            or set(p.rights.metric_snapshot_ids)!={
                p.group_c.previous_snapshot_id,p.group_c.current_snapshot_id}):
            return "join_metric_snapshot_pair_mismatch"
        if (p.lineage.sre_run_id!=p.qdp.sre_run_id
            or p.lineage.sre_taxonomy_version_id!=p.qdp.sre_taxonomy_version_id
            or p.lineage.sre_content_version_id!=p.qdp.sre_content_version_id
            or p.sre.taxonomy_sha256!=p.qdp.taxonomy_sha256
            or p.sre.content_sha256!=p.qdp.sre_content_sha256
            or p.sre.target_category_keys!=p.qdp.approved_category_keys):
            return "join_sre_qdp_version_mismatch"
        if (p.lineage.group_a_analysis_id!=p.group_a.analysis_id
            or p.lineage.group_b_analysis_id!=p.group_b.b_analysis_id
            or p.lineage.calculated_metric_id!=p.group_c.calculated_metric_id):
            return "join_abc_analysis_id_mismatch"
        if (p.group_a.qdp_a_preparation_sha256!=p.qdp.preparation_shas[0]
            or p.group_b.qdp_b_preparation_sha256!=p.qdp.preparation_shas[1]
            or p.group_c.qdp_c_preparation_sha256!=p.qdp.preparation_shas[2]):
            return "join_abc_preparation_sha_mismatch"
        if (p.group_b.group_a_analysis_id!=p.group_a.analysis_id
            or p.group_b.group_a_result_sha256!=p.group_a.result_sha256
            or p.group_b.group_a_media_manifest_sha256!=p.group_a.media_manifest_sha256):
            return "join_b_wrong_group_a_parent"
        if (p.group_c.config_id!=p.qdp.group_c_config_id or
            p.qdp.group_c_snapshot_ids!=(
                p.group_c.previous_snapshot_id,p.group_c.current_snapshot_id)):
            return "join_c_config_or_snapshot_changed"
        if p.lineage.b_taxonomy_version!=p.group_b.taxonomy_version:
            return "join_b_taxonomy_version_mismatch"
        if len(p.rights.media_asset_ids)!=p.group_a.expected_media_items:
            return "join_group_a_media_count_mismatch"
        return None


@dataclass(frozen=True,slots=True)
class CandidateExclusion:
    fingerprint: str
    reason_code: str

    def __post_init__(self) -> None:
        if (type(self.fingerprint) is not str or len(self.fingerprint)!=64
            or type(self.reason_code) is not str or not 1<=len(self.reason_code)<=80
            or not self.reason_code.replace("_","").isalnum()):
            _fail("join_exclusion_invalid")


@dataclass(frozen=True,slots=True)
class JoinedInputContext:
    scope: InputScope
    as_of: datetime
    fingerprint: str
    lineage_sha256: str
    source_rights_sha256: str
    evidence: tuple[EvidenceRef, ...]
    binding_sha256: str
    schema_version: str = JOIN_SCHEMA

    @property
    def qualified(self) -> bool:
        return False

    def __post_init__(self) -> None:
        if (not isinstance(self.scope,InputScope) or not _utc(self.as_of)
            or len(self.evidence)!=len(REQUIRED_PORTS)
            or tuple(r.port for r in self.evidence)!=REQUIRED_PORTS
            or any(r.scope!=self.scope for r in self.evidence)):
            _fail("join_context_incomplete")


@dataclass(frozen=True,slots=True)
class JoinedSelection:
    status: str
    selected: JoinedInputContext | None
    excluded: tuple[CandidateExclusion, ...]
    attempted: int
    schema_version: str = JOIN_SCHEMA

    @property
    def qualified(self) -> bool:
        return False

    def __post_init__(self) -> None:
        if (self.status not in ("complete_claims_unapproved","blocked")
            or (self.selected is None)!=(self.status=="blocked")
            or type(self.attempted) is not int
            or not 0<=self.attempted<=MAX_CANDIDATES):
            _fail("join_selection_invalid")


class JoinedContextAssembler:
    """Exact producer adapters under caller-owned fresh DB snapshot.

    Protected methods can be mocked in tests only. Production uses real
    source-backed readers, not user-supplied EvidenceRead or unchecked flags.
    """

    def __init__(self,conn:Any,scope:InputScope,
                 candidates:tuple[JoinedCandidatePins,...],*,as_of:datetime,
                 group_c_source_ports=None):
        if (conn is None or not callable(getattr(conn,"execute",None))
            or not isinstance(scope,InputScope) or not _utc(as_of)
            or type(candidates) is not tuple or not candidates
            or len(candidates)>MAX_CANDIDATES
            or any(not isinstance(p,JoinedCandidatePins) for p in candidates)):
            _fail("join_invalid_request")
        self._conn,self._scope,self._candidates,self._as_of=(
            conn,scope,candidates,as_of
        )
        # Integration is explicitly opted into with current Group C D1/rights
        # ports. Default remains the original fail-closed UA-MOD-10 reader.
        if group_c_source_ports is not None:
            from intelligence.group_c.ports import GroupCPorts
            if not isinstance(group_c_source_ports, GroupCPorts):
                _fail("join_group_c_ports_invalid")
        self._group_c_source_ports=group_c_source_ports

    def _canonical(self,p:JoinedCandidatePins) -> CanonicalLineage:
        return resolve_canonical_lineage(self._conn,p.lineage,as_of=self._as_of)

    def _readers(self,p:JoinedCandidatePins) -> tuple[SourcePolicyCurrentReader,UAEPorts]:
        db=self._conn
        when=self._as_of
        rights=SourcePolicyCurrentReader(db,p.rights,as_of=when)
        qdp=QDPQualifiedSetReader(db,p.qdp,as_of=when)
        sre=SREApprovedRelevanceReader(db,p.sre,as_of=when)
        ga=GroupAVerifiedFactsReader(db,p.group_a,qdp,as_of=when)
        gb=GroupBExactReader(db,p.group_b,ga,qdp,as_of=when)
        if self._group_c_source_ports is None:
            gc=GroupCPersistedScoreReader(db,p.group_c,qdp,as_of=when)
        else:
            from intelligence.group_c_uae_pipeline import UAEReceivedGroupCReader
            gc=UAEReceivedGroupCReader(
                db,p.group_c,qdp,self._group_c_source_ports,
            )
        return rights,UAEPorts(
            source_policy=rights,qdp=qdp,sre=sre,
            group_a=ga,group_b=gb,group_c=gc,
        )

    def _ga_media_ids(self,p:JoinedCandidatePins) -> None:
        try:
            rows=self._conn.execute(GA_MEDIA_IDS_SQL,(
                p.group_a.ga_run_id,p.scope.project_id,p.scope.video_id,
                p.scope.source_id,
            )).fetchall()
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "join_media_dependency_unavailable",
                "Group A media lookup failed",
            ) from exc
        if type(rows) is not list or any(not isinstance(x,Mapping) for x in rows):
            _fail("join_media_rows_invalid")
        if (len(rows)!=p.group_a.expected_media_items
            or any(row.get("slide_index")!=i
                or row.get("media_asset_id")!=p.rights.media_asset_ids[i-1]
                for i,row in enumerate(rows,1))):
            _fail("join_ga_source_media_mismatch")

    def _check(self,p:JoinedCandidatePins) -> JoinedInputContext:
        scope=self._scope
        rights,ports=self._readers(p)
        guard=GuardedUAEReads(ports,rights)
        original=rights.check_current_rights(scope)
        lineage=self._canonical(p)
        if (not isinstance(lineage,CanonicalLineage)
            or lineage.pins!=p.lineage or lineage.as_of!=self._as_of
            or lineage.score_config_id!=p.group_c.config_id
            or lineage.metric_snapshot_id!=p.group_c.current_snapshot_id
            or lineage.calculated_metric_id!=p.group_c.calculated_metric_id
            or lineage.a_analysis_version!=p.group_a.analysis_version
            or lineage.b_analysis_version!=p.group_b.analysis_version
            or lineage.b_taxonomy_version!=p.group_b.taxonomy_version
            or lineage.sre_taxonomy_checksum!=p.qdp.taxonomy_sha256
            or lineage.metric_source_version!=p.group_c.source_metric_version):
            _fail("join_canonical_version_changed")
        self._ga_media_ids(p)
        # D1 is already source-backed by resolve_canonical_lineage().
        # Construct its exact local identity reference explicitly: it is
        # NOT a signed D1 event and must not be interpreted as approval.
        refs:list[EvidenceRef]=[]
        first=guard.read("source_policy",scope)
        if first.state is not ReadState.LOCATED:
            _fail("join_source_policy_not_current")
        refs.append(first.reference)
        d1_ref=EvidenceRef(
            scope=scope,port="d1",
            record_key=("d1:"+str(scope.video_id)+":"+
                        str(p.lineage.metadata_id)+":"+
                        str(p.lineage.metric_snapshot_id)),
            producer_schema="uae-canonical-lineage/v1",
            producer_version="pinned-v1",
            payload_sha256=lineage.lineage_sha256,
        )
        refs.append(d1_ref)
        for port in REQUIRED_PORTS[2:]:
            found=guard.read(port,scope)
            if found.state is not ReadState.LOCATED:
                _fail("join_"+port+"_not_current")
            refs.append(found.reference)
        current=rights.check_current_rights(scope)
        if original.evidence_sha256!=current.evidence_sha256:
            _fail("join_current_rights_changed")
        digest=canonical_hash({
            "wire":JOIN_SCHEMA,"scope":scope.scope_sha256,
            "candidate":p.fingerprint,"canonical":lineage.lineage_sha256,
            "rights":current.evidence_sha256,
            "references":[x.lineage_sha256 for x in refs],
            "as_of":self._as_of.isoformat(),
        })
        return JoinedInputContext(
            scope,self._as_of,p.fingerprint,lineage.lineage_sha256,
            current.evidence_sha256,tuple(refs),digest,
        )

    def select(self) -> JoinedSelection:
        ranked=sorted(self._candidates,key=lambda p:(
            tuple(-v for v in p.priority),p.fingerprint
        ))
        seen:set[str]=set()
        excluded:list[CandidateExclusion]=[]
        for index,p in enumerate(ranked):
            fp=p.fingerprint
            if fp in seen:
                excluded.append(CandidateExclusion(fp,"join_duplicate_candidate"))
                continue
            seen.add(fp)
            reason=p.binding_error(self._scope)
            if reason is not None:
                excluded.append(CandidateExclusion(fp,reason))
                continue
            try:
                context=self._check(p)
            except AnalyticsBoundaryError as exc:
                code=exc.code
                if (type(code) is not str or len(code)>80
                    or not code.replace("_","").isalnum()):
                    code="join_dependency_failed"
                excluded.append(CandidateExclusion(fp,code))
                continue
            # Every remaining supplied candidate is accounted for, but an
            # uninspected lower-ranked bundle must NEVER be called invalid.
            # Repeats remain explicitly marked as duplicates.
            for later in ranked[index+1:]:
                later_hash=later.fingerprint
                if later_hash in seen:
                    code="join_duplicate_candidate"
                else:
                    code="join_not_inspected_after_selection"
                    seen.add(later_hash)
                excluded.append(CandidateExclusion(later_hash,code))
            return JoinedSelection("complete_claims_unapproved",
                                   context,tuple(excluded),index+1)
        return JoinedSelection("blocked",None,tuple(excluded),len(seen))


__all__=(
    "JOIN_SCHEMA","REQUIRED_PORTS","MAX_CANDIDATES",
    "JoinedCandidatePins","CandidateExclusion","JoinedInputContext",
    "JoinedSelection","JoinedContextAssembler","GA_MEDIA_IDS_SQL",
)
