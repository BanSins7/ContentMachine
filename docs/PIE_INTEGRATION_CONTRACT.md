# PIE — Versioned Integration Boundary / Bootstrap v0
**Owner:** `intelligence.patterns_insights`. **Purpose:** design an independent Pattern Discovery + Insight Generator; no real live UAE provider connection at PIE-MOD-00.

## Input (UAE -> PIE)
Canonical proposed contract `uae-to-pie/v1` to be negotiated at PIE-MOD-01/03, NOT a claim that UAE already emits it.
- dataset_pointer.project_id: lowercase 32 hex project id; source ID and reviewed policy ID; UAE engine version; input dataset/manifest SHA256; outcome C configuration ID and metric definition/version; as_of observed-time coordinate.
- Actual UAE adapter must verify each row is currently `qualified`, visible, same project/source/versions, `score_status=complete` and pinned to exact A/B/C/SRE source. A summary group row is not automatically a row-level dataset.
- Canonical post identity = `videos.id`. Dataset selection and exclusions must be frozen, comparable stratum boundaries and row-level evidence hashes retained; score cannot be inferred from a CTA or estimated based on role.
- Contract can use row-level research manifest only when authorized; avoid transmitting raw media, transcripts or creator private data unnecessarily.

## Research design before exploration
Specify primary outcome, time window/as_of, platform/content-kind/metric-definition strata, creator clustering, comparison group, missingness rules, hypotheses family, confidence/FDR levels and holdout partition before inference. No arbitrary global defaults for min_n, p_threshold, 'high performer' cutoff or effect magnitude.
An observed association is not proof of a causal effect. Raw Breakout Score from C is unnormalized and not restricted to 0–100, and C2 niche_outlier is outside C7. If another outcome chosen, label explicitly.

## Patterns
Candidates store feature operator, group count, N (unique videos within group), baseline, effect size, appropriate uncertainty, adjusted multiplicity, preregistration and independent confirmation status.
`exploratory`, `supported`, `rejected`, `needs_review`, `invalidated` (candidate target statuses) must be justified with persisted versioned evidence and human review as defined later. Runtime not implemented by this bootstrap.
Never assert n=2000 from one descriptive UAE summary JSON without actual sample membership and coverage.

## Insights
Only validated Pattern versions are given to an LLM, with exact numerical evidence and caveats. Output requires pattern version ID, factual evidence manifest, explicit scope, effect and uncertainty, proposed future experiment and no misleading causal claim. Model output must be checked against numeric inputs, then human reviewed. Paid API is opt-in, isolated from deterministic statistics and CI.

## Access, versioning and retention
All reads/exports recheck source rights, policy versions, retention/TTL, upstream UAE evidence availability, project permissions and invalidation. Historical pattern remains immutable and may be hidden or erased according to current policy. No silent reuse of stale source dataset after revocation.

## Output (PIE -> future Strategy)
Publish only approved, versioned, traceable pattern/insight packages to the future Content Strategy & Ideation module. PIE does not draft scripts, generate images, or publish content.

## Dependency status as of 2026-10-26
UAE PR #38 is independent and bootstrap-only; the data provider/handoff is unavailable until separately implemented and tested. For now only dependency-free local normalized pointer and pure test fixtures are supported.
## PIE-MOD-03 proposed research wire (2026-10-26)

PIE now defines [strict versioned `uae-to-pie/v1` contract](PIE_DATASET_CONTRACT_V1.md), [local parser](../intelligence/patterns_insights/dataset_contract.py), and synthetic positive/negative fixtures. The wrapper binds canonical row evidence, versions, included/excluded IDs and two independent SHA-256 checks to `PIEDatasetPointer`. The output has `verified_live=False` by design: validation is structural and checksum-only. UAE has **not** provided or authenticated this wire; the old descriptive `uae-pattern-handoff/v1` does not satisfy it. Future PIE-MOD-05/06 must negotiate source-backed UAE reader, source policy, expiry and receipt verification. No PIE statistical inference or downstream Insight release is enabled by the contract alone.

## PIE-MOD-05 receiver implementation / current limitation

[Signed research export handshake](PIE_UAE_HANDSHAKE_V1.md) is available as a **PIE-owned receiving adapter**, NOT a claim that UAE exports it today. `UAEReadBridge` requires explicit row-level `uae-to-pie/v1` transport, separately provisioned receipt verification secret/verifier and independently injected per-request current-rights authority. Missing source or a descriptive-only handoff produces typed failure. Mock-authenticated outputs remain `verified_live=False` and `ready_for_analysis=False` until PIE-MOD-06 verifies current row-level QDP/SRE/A/B/C and source eligibility. Do not call an LLM, publish, or run significance tests over this intermediate object.
