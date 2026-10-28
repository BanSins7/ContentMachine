# UA-MOD-13 — Source-backed qualification orchestration

**Scope:** feature/unified-analytics-full-module only. New: intelligence/unified_analytics/qualification_orchestrator.py; tests/test_unified_analytics_qualification_orchestrator.py; exported API in intelligence/unified_analytics/__init__.py. No migrations, model calls, DB writes, HTTP exposure or peer-branch changes. Complete machine-readable STATUS and blockers; production grants still intentionally unavailable.

## What qualifies and what does not

UA-MOD-03 qualification.py contains evaluate_qualification, a **pure offline three-state semantic checker** for hypothetical canonical facts. QualificationObservation(checked=True,current=True) can be independently fabricated by arbitrary Python callers and has NO trusted backend identity. The v1 pure function remains useful for golden legacy fixtures but MUST NEVER be exposed as an online UAE authority accepting caller observations.

UA-MOD-12 joined_context.py now supplies source-backed exact D1/SRE/QDP/A/B/C evidence claims and current source-policy checks through one project-authorized DB transaction, with deterministic candidate selection. However the current Group C peer (GC057) freezes are frozen_unreviewed, and GroupCPersistedScoreReader.read_persisted_metric explicitly returns MISSING/c_effective_approval_unavailable; Group B has no effective reviewed approval wire, QDP carousel and SRE LIVE also have outstanding peer blocks. **A seven-reference JoinedInputContext, if simulated by a mock, is still an UNAPPROVED assertion**, not a trusted producer-signature, config/cohort human approval or qualified user-facing analytics.

This stage introduces QualificationOrchestrator(JoinedContextAssembler). No public mapping of status flags, approval=True, checked=True, QualificationInput or QualificationObservation is accepted. The orchestrator invokes assembler.select() itself, validates that any selected context matches exact InputScope, UTC evaluation time, the seven ordered ports, SHA format, and the original candidate pool. Exclusions must also originate from that same pool; no foreign candidate may inject a reason. No source payload, score or media bytes enter QualificationDecision.

## Decision statuses

| status | Source-backed semantics | Current availability |
|---|---|---|
| qualified | reserved for all seven exact sources with actual current reviewed Group C config/cohort/score and effective Group B/SRE/QDP approval attestations | **UNREACHABLE** in currently observed production contracts, deliberately rejected by QualificationDecision constructor |
| partial | no hard denial: A/B/C processing incomplete, Group C effective approval not available, or a simulated fully located seven-port context without reviewed authority | Can be returned; never publishable |
| blocked | hard denial: missing/invalid current source rights, expired/withdrawn evidence, project mismatch, QDP unqualified, absent SRE LIVE relevance, incompatible A→B or C snapshot/config versions, unknown failures | Can be returned; never publishable |

Blockers are immutable QualificationBlocker(code,stage,severity,candidate_sha256) entries. Machine codes (e.g. source_retention_expired, join_group_c_not_current, join_b_wrong_group_a_parent) retain actual reason and stage. Unknown reason defaults HARD: no arbitrary "warning" downgrade. Only authorized categories of missing/incomplete Group A/B/C count as INCOMPLETE. If there are both a hard and an incomplete rejection, BLOCKED wins. Candidate duplicates and lower-ranked candidates uninspected after a separate selected result are audit information only, never fabricated evidence of failure.

Readiness is seven ordered PortQualification records with state unverified / blocked / incomplete / located_unapproved. A missing port is not silently set FALSE or 0; if no joined context is selected, unexamined ports remain unverified. Even hypothetical all-located readiness is explicitly located_unapproved, never ready for publishing. Offline UA03 may still return the string qualified for a fake legacy vector but is NOT connected to this production result.

QualificationDecision and as_dict include scope_sha256, as_of UTC, attempted count, selected candidate fingerprint if any, deterministic blockers, readiness, input_sha256, decision_sha256 and explicit qualified=False. The decision hash binds exact sorted candidate fingerprints, selected joined-context evidence binding hash, evaluation time, result and reason codes. Equal inputs produce identical hashes; a changed candidate version, source policy or as_of produces a changed fingerprint. Hashes bind information but DO NOT authenticate its source.

## Trusted dependency and threat model

Production call path (future UA-MOD-14+) must create JoinedContextAssembler from authorized, source-backed exact candidate identities in a **fresh transaction per externally visible operation**. JoinedContextAssembler re-reads canonical D1 rows, GA media refs, Group A/B/C exact lineage and source-policy TTL before/after every peer read; this qualification stage never reopens raw media files or trusts an external EvidenceRead. The injected-assembler API is internal and must not be exposed as a user-selectable plugin, JSON parameter or webhook handler. Test subclasses and 100% fake positive selections are *hypothetical mocks* and confer no production rights.

Since a signed effective Group C reviewed approval contract does not yet exist, there is intentionally **no positive production promotion branch**. Qualified is still part of the v1 three-state vocabulary and UA03 offline golden semantics, but a fabricated Qualified QualificationDecision raises qualification_effective_approval_unavailable. A later peer-approved, source-backed attestation validator must be implemented and security-reviewed before adding a positive promotion path; never insert caller-set checked=True or fake approved JSON to do so.

Currently even a hypothetically fully joined selection becomes PARTIAL with qualification_effective_approval_unavailable. Missing C data results in PARTIAL/join_group_c_not_current. Mismatched source, source expiry, invalid project, incompatible QDP/SRE and wrong upstream analysis parent yield BLOCKED. No scoring formula is recomputed, no result persisted, no Patterns handoff performed.

## QA and handoff

New tests/test_unified_analytics_qualification_orchestrator.py contains 22 authored unittest methods: seven missing peer ports, source-rights and source/project revocation, wrong A/B/C lineage, incompatible versions, missing reviews, unknown reason hard-deny, partial vs blocked priority, duplicate/uninspected exclusions, manually forged qualified decision, forged checked=True QualificationInput, selection not from original candidate pool, stable decision hashes, as_of version sensitivity, no fabricated zeros and conditional disposable PostgreSQL 17 empty-baseline check. The conditional PG17 case alone is not a LIVE inter-peer qualification test.

Required real execution:
- python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
- python -m compileall -q intelligence/unified_analytics

Implementation/static checks are separate from executable tests. No executable tests were run in this implementation environment (no cloned repository, network DNS from container, psycopg driver or PG17 server). GitHub Actions may be queued; DO NOT claim green CI without a completed successful run.

**Next UA-MOD-14:** immutable per-video evidence snapshot formation, with an explicit current source-rights check and cryptographic digest of its exact verified source keys, without publishing or qualifying unsupported upstream approvals.
