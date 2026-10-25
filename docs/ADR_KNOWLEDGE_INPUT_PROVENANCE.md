# ADR-KE-001: A→B provenance, completeness, rights conflict and C routing

**Status:** Architecture decision for B Stage01, implementation deferred as noted.
**Baseline:** A branch `1970cd0`, original B1–B9 plan dated 2026-10-24,
14-stage implementation plan. No change to `main` or the A Draft PR.

## Context: same schema label, two incompatible objects

Stage01 A `contracts.ProjectContextSnapshot` is a theoretical DTO:
it embeds `context_id`, `version`, `immutable_context_hash`,
`pinned_source_versions`, `approved_fact_ids`. The actually
persisted Stage11 `context_builder.build` payload has `profile`,
`source_manifest`, `evidence_manifest`, `analysis_refs` and SHA
stored on the containing record. Approval is stored in SQL separately;
the immutable payload still has `approval_status="pending"`.

**Decision:** B consumes exactly one explicit `_snapshot` **envelope** and
enforces `context_envelope_v1.json` plus additional hash/graph checks.
No silently repackaged imaginary DTO, no cyclic A→B dependency and no
mandatory `approved_fact_ids` within A. Stage02 DB resolver verifies
actual records; Stage01's in-memory validator is not proof of SQL integrity.

## Context: A Evidence limit vs exhaustive B Fact Extraction

A Stage10's Evidence normalization caps candidates and A Stage11 caps
payload size. A's Evidence manifest therefore cannot be advertised as a
complete inventory of all document text. A12's compact selector also skips
and truncates oversized Evidence for its own RTB prompt.

**Decision:** Stage03 B1 inventories **technical fragments** of pinned A
document and web outputs and questionnaire source versions. Separate
`fragment_id` from optional `evidence_id`; never synthesize A Evidence IDs.
B2 accounts for every *required* fragment in exactly one or more packs,
or explicitly records noncoverage/blocks. We do not claim semantics are
complete just because pack counts match. If immutable source text cannot be
read, the run is not silently `ready`.

## Context: source roles and subject identity

Source type and A `knowledge_scope` are not subject ownership; competitor
facts, market research, questionnaire fields and own-product features
must remain distinguishable.

**Decision:** Roles are `own_product`, `audience_research`,
`competitor`, `creative_reference`, `other`. Conservative preliminary
defaults allowed for website/account types only. `research_document`,
`questionnaire_field`, `research_link`, unknown types default to
`other` until verified routing and subject mapping in Stage03. The
`subject_id` must be stable when known; no silent competitor→product
promotion and no automatic semantic dedup across subjects.

## Context: video version and no secondary inference

A's real fusion output is `video-intelligence/v1`, different from
`contracts.VideoAnalysis`'s illustrative `video-analysis/v1`.

**Decision:** Stage01 defines `VideoAnalysisPin`, retaining real
`source_id/source_version_id/output_id/output_sha256/schema_version`,
not media/transcript/vision payload. Stage02 verifies it, Stage09 B5
does not ground video content into B3 Facts, and Stage13 B9 passes
video refs into C separately. No video or transcript is sent to MiMo.

## Context: duplicate paid pipelines

Stage12 A already runs optional source-aware RTB fact extraction with
`facts-context-v1` and human fact review. B introduces a separate
evidence-complete Knowledge Extraction path, and must not restart RTB
MiMo on B9 approved facts.

**Decision:** Preserve legacy `legacy_questionnaire_v1` and
`approved_project_context_v1`. Stage13 adds distinct
`knowledge_snapshot_v1` input to C, behind new cost quote and consent;
no automatic paid follow-up from B9 publication. Keep existing historical
runs and approvals unchanged.

## Explicit unresolved rights/copyright requirements conflict

B source plan §2.9, B1.4 and release-gate checklist request verification of
reading/processing/external-LLM rights, retention, and revocation before
each operation. The same user previously explicitly asked that A remove
backend copyright/rights-processing gates from their pipeline.

**Decision (scope-limited):** Do not introduce *new* copyright ownership
or rights-processing gate as part of B Stage01, and do not assert that
revocation enforcement exists. **Preserve existing access boundaries**,
project isolation, technical integrity checks, secrets protection,
network safety, provenance/retention metadata, and explicit consent for
paid provider dispatch. Later requirements involving legally scoped
rights/revocation are **UNRESOLVED pending explicit user decision**.
Do not quietly reinterpret historical permission as a reusable paid
provider consent; do not remove existing A security controls either.
Rights metadata can be retained without adding an enforcement bypass or
a fabricated `rights_verified` claim.

## Rejected alternatives

1. Using theoretical A `ProjectContextSnapshot` DTO as the B wire
   schema — rejected: mismatch with actual A SQL and immutable hashes.
2. Reusing A12 `choose_evidence` as exhaustive B2 input — rejected:
   bounded selector may skip/truncate content.
3. Promoting `approved_evidence_ids` into `approved_fact_ids` — rejected:
   source evidence approval ≠ knowledge fact review.
4. Resending Video Intelligence through MiMo — rejected: additional
   paid interpretation and deviation from the original A observation.
5. Reusing already-spent A12 consent in B or C — rejected: one-run budget
   and independent provider attempts.

## Follow-up / verification gates

Stage02: real read-only A resolver, foreign-project, SHA, outdated/missing
source, and SQL tamper tests. Stage03: exhaustive inventory coverage, B1
bundle persistence and stable source roles. Stage04–05: Full/Multi-Pack
and budget limits. Stage13: complete B9→C compatibility and real
KnowledgeSnapshot replay. Stage14: E2E security/CI/immutable SQL audits,
explicit human PR review. No more stages should be conflated with Stage01.
