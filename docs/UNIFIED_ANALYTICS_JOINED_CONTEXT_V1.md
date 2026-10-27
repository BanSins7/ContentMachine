# UA-MOD-12 — Joined input context and exact version selection

**Branch:** feature/unified-analytics-full-module. **Code:** intelligence/unified_analytics/joined_context.py. **Tests:** tests/test_unified_analytics_joined_context.py. **No new SQL schema:** reads existing canonical D1 and independently owned SRE/QDP/Group A/B/C tables. No changes to main or peer feature branches, no job/HTTP/Studio/publisher activation.

## Contract and deterministic selection

The caller provides an explicit UTC as_of, one authorized fresh transaction-consistent dict-row PostgreSQL connection, exactly one canonical InputScope and a bounded tuple of 1..48 JoinedCandidatePins. Candidate pins contain the already-existing CanonicalLineagePins, SourceRightsPins, SREApprovedRelevancePins, QDPQualifiedPins, GroupAVerifiedPins, GroupBExactPins and GroupCPersistedPins. They are **IDs and digests, not trusted approvals**. Producer-specific pinned candidate enumeration is the responsibility of the next orchestration stage; do not trust user-supplied candidate fields as authority or independently select 'latest Group A, latest Group B and latest Group C'.

Before any peer read the selector structurally excludes candidates whose project/video/source/policy or exact recorded versions differ:

- Canonical D1 metadata ID agrees with QDP and Group C and SourceRightsPins; the lineage current raw metric ID agrees with Group C current snapshot and SourceRightsPins must list **exactly both previous+current C0 IDs**. This prohibits unrelated C observations.
- SRE run UUID, taxonomy version/sha, content version/sha, target category keys agree with QDP's immutable selection; QDP SRE result IDs and complete SRE leaf matrix are independently checked in the source-backed QDP/SRE adapters.
- A, B, C analysis IDs agree with canonical lineage. A/B/C have three **distinct per-group preparation SHA namespaces**, which must match the corresponding QDP-prepared A/B/C fingerprints. Global QDP run SHA is not interchangeable with any of them.
- Group B's exact parent A analysis ID, immutable published A result SHA and ordered media-manifest SHA match the selected A; B taxonomy version matches pinned canonical D1 lineage. An old or wrong A cannot be attached to a newer B analysis.
- Group C's calculation config ID, ordered C0 snapshot pair and selected QDP C preparation agree exactly with QDP and with the D1 current snapshot. Group C source metric version must equal canonical D1; no arithmetic is redone.
- The exact set and count of Group A's actual ga_media_refs IDs are read from its run and must match in-order SourceRightsPins.media_asset_ids; the actual Group A reader independently checks content/hash, per-slide evidence and rights. A missing or extra slide is never reconciled heuristically.

Candidate ordering is **deterministic**, regardless of caller input order: descending pinned Group C current metric observation ID, SRE content/taxonomy version IDs, Group A and Group B analysis IDs, and calculated metric ID; equal-ranked candidates are ordered by the deterministic canonical candidate SHA. This is merely priority among *whole compatible bundles*, not proof that the greatest numeric ID is freshest. Each candidate still rechecks live current QDP membership, actual latest raw D1 rules in the Group C adapter, source authorization and every peer's exact source-backed execution. A mismatch is recorded as CandidateExclusion(fingerprint, reason_code), with no partial source payload. Duplicates and lower-ranked uninspected candidates are recorded distinctly, never misrepresented as factually invalid.

## Source-backed verification

For the first structurally plausible bundle, JoinedContextAssembler constructs the real source-backed readers with exactly the same injected DB connection and UTC as_of: SourcePolicyCurrentReader, SREApprovedRelevanceReader, QDPQualifiedSetReader, GroupAVerifiedFactsReader, GroupBExactReader and GroupCPersistedScoreReader. It also calls resolve_canonical_lineage for pinned D1/project/source/taxonomy/A/B/C row identity. Then GuardedUAEReads checks the current rights before and after EACH peer, and the selector performs one last source-rights recheck. Missing/partial/revoked, schema UNAVAILABLE, stale model/taxonomy, wrong GA media IDs or any blocked C score yields only a structured exclusion, never an accepted partial context.

The only positive selection response is JoinedInputContext with seven exact EvidenceRefs in order source_policy/d1/qdp/sre/group_a/group_b/group_c, all for one InputScope. The D1 ref is local versioned identity of the independently DB-verified CanonicalLineage, NOT a signed original D1 event. The binding SHA covers exact scope, candidate fingerprint, canonical lineage hash, live source rights hash, seven evidence ref digests and evaluation time. JoinedInputContext.qualified and JoinedSelection.qualified are **always False**. Even all seven hypothetically LOCATED claims are not UAE approved, signed or published.

If all candidates are incompatible, rejected, revoked or unapproved, the return is JoinedSelection(status=blocked, selected=None) with ordered CandidateExclusion entries (only deterministic hashes and typed safe reason codes). Any candidate evidence read during rejected attempts is discarded, and no raw scored/media/source payload appears in the selection response. An external HTTP/UI endpoint must NOT expose it unfiltered before full authenticated project read and UA-MOD-13/19 production gating.

## Critical production gaps

- Current upstream SRE LIVE settlement is not yet authenticated for approved terminal leaf decisions.
- Current QDP A/B carousel readiness is video-only; photo/mixed carousel integration remains unavailable.
- Group B has no observed reviewed effective B1–B8 approval wire; a human review event still denies.
- Group C GC057 freezes are **frozen_unreviewed** only; its current source-backed GroupCPersistedScoreReader.read_persisted_metric never returns LOCATED, including if its decimal facts are internally consistent. Hence **NO real candidate can currently yield a positive JoinedInputContext on those peer contracts**; a mocked positive test is hypothetical and does not show production qualification.
- The assembler is given preselected candidate bundles; it does not attempt to enumerate untrusted cross-products or reconstruct missing peer SHA from row timestamps. The upstream caller must produce authoritative candidate sets under its own project-scoped DB permissions (next orchestration stage).
- The caller must open a *new* transaction for each externally visible evaluation with fresh UTC as_of: a long-lived PostgreSQL REPEATABLE READ snapshot cannot observe revocations committed after the snapshot began. This module performs no publication, no side effects and no implicit retry on bad rights.

## Verification and next

tests/test_unified_analytics_joined_context.py contains 16 authored unittest methods using explicit protocol mocks to simulate future eligible peer output. Cases include positive seven-reference hypothetical context; no qualified flag; structural cross-project/source/category/version mismatch; all three independent QDP A/B/C preparation SHAs; wrong B→A parent and media manifest; wrong C current/previous pair/config or raw observation ID; Group A media asset mismatch; project rights expired/revoked before/during read; D1 canonical data version supersession; deterministic stable order and tie breakers; all unapproved GC candidates blocked; duplicate and uninspected candidates; bounded candidate count and UTC validation. Positive mock data is **not** live end-to-end evidence.

Executable QA commands:
- python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
- python -m compileall -q intelligence/unified_analytics

Tests are only authored until an actual Python/PG17/CI log is observed. No additional PG17 DDL is needed this stage because the source-backed SQL calls existing tables and the prior UAE rights migration 083, tested separately. No upgrade to a release-ready assertion is permitted from static checks.

**Next UA-MOD-13:** qualification orchestration, fully authoritative adapter read to versioned approval decision, preserving all current fail-closed peer gaps and current source-policy read controls.
