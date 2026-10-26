# Content Machine — B Stage11: B7 Quality Gate

**Scope:** deterministic, no-network, no-LLM assessment of the **readiness of B4–B6 proposals for B8 Human Knowledge Review**. It is NOT publication approval, not a substitute for B8, not semantic entailment or independently verified website truth. Implementation is separate from A and legacy RTB, with PostgreSQL migration 050.

## Pipeline

A verified approved Context -> B1 immutable KnowledgeInputBundle -> B2 Full/Multi pack -> B3 Stage06 request + explicit paid Stage07 run -> B4 deterministic normalization -> B5 original-A quotation grounding -> B6 exact duplicate groups and possible conflicts -> **B7 decision** -> B8 review (not implemented by Stage11) -> B9 immutable KnowledgeSnapshot (not implemented) -> C (not implemented).

Quality Gate does not dispatch B3, does not spend money, does not automatically call B8, does not create an approved fact, and does not invoke C. All A/B Stage01–10 history is reverified on assessment and on replay. A site's Sonnet-reported text remains model_reported_unverified despite exact quote grounding.

## Stage11 evaluation contract

The gate uses the immutable historical Stage06 batch and verified original B1 bundle, historical B2 Full/Multi manifests, Stage07 completed/charged per-part attempt ledger, B4 normalized report, B5 source-grounding report, and B6 immutable groups, members and conflicts.

It separately reconciles:
- All B1 fragments, their context bundle SHA, fragment counts, mandatory uncovered sources, excluded sources and explicit B2 partial-context acknowledgement. For Full, every fragment exactly once; for Multi, exact complete ordered source coverage as proven by B2's verified Multi plan and part records.
- Stage06 number of requests and original source chunks, per-part request/manifest SHA, Stage07 status=ready_for_normalization, one fully charged attempt per part, same candidate counts per B4 pack, exact sum of provider charges, nonnegative spend and zero held reservation.
- B4 normalized + quarantined = every model candidate. No position silently disappears; B5 matched/rejected/skipped and B6 eligible/blocked, original quote and member/group counts reconcile. Individual rejected B5 and quarantined B4 facts are excluded from groups, but remain visible in warnings.
- Every B6 member retains verified server-owned provenance (original A source/version/output, locator and exact quote SHA). Duplicate members are NOT discarded.
- All potential B6 conflicts remain needs_review; no “winner” or automatic truth selection. Lack of a lexical conflict warning does not mean contradictions have been excluded.
- Source trust classification, ambiguous ownership, token/candidate limits and explicit partial-context source exclusion. All warnings are deterministic and auditable.
- Original immutable A VideoIntelligence refs survive separately in the B1 bundle, are checked for exact source-version/output/schema/SHA against the historical approved A Context, and are counted as available/missing B9 export pins. Missing or hash-mismatched video refs block publication/review handoff; they are never counted in MiMo text-pack completeness, and B7 does not process video content.

### Gate decisions

| Decision | Criteria | Meaning |
|---|---|---|
| blocked | Mandatory coverage missing; source bundle not ready; missing partial acknowledgement; zero quote-grounded groups; any part hit the 80-candidate cap; missing or changed A video export reference. Any corrupted provenance instead raises a hard integrity error without saving a gate | Do not hand off to B8 |
| partial_ready | At least one grounded group, all hard invariants pass, but some quarantined/rejected claims, lexical conflicts, partial context, uncertain ownership or unverified website extracts remain | B8 may start, but must display warnings and source context. No automatic approval |
| ready_for_review | Fully covered and paid packs, at least one grounded group, no blockers and no warning triggers | B8 may start, still needs explicit fact-by-fact human decisions |

An empty set of MiMo proposals yields blocked, even if the model returned a valid zero-candidate array. A complete pack does not prove semantic coverage of a subject. A MiMo part that returns exactly 80 candidates hits the hard cap, which is treated as possible truncation; do not silently claim completion.

B4 quarantine or B5 rejection with **remaining** grounded groups produces warning-level status rather than losing valid reviewable claims. However, those quarantined/rejected individual candidates are never approved or accepted as groups.

The gate checks the original role/subject metadata, not the content of a fact to infer “our product”. It warns if an origin is ambiguous, and for model-reported web extraction without independently verified live website retrieval.

## New public Python contract

    from intelligence.knowledge_extraction.quality_gate import FactQualityGateRepository

    repository = FactQualityGateRepository()
    summary = repository.assess(project_id, completed_deduplicated_run_id)
    # quality_gate_id, created, decision, handoff_allowed, findings,
    # counts, pack_audit, report_sha256, provider_calls=0

    verified = repository.materialize(project_id, completed_deduplicated_run_id)

The pure function evaluate_quality(...) is useful for contract QA with **replay-verified inputs**, not a public user-supplied verdict. Only the server repository provides independently verified B1–B6 inputs from its PostgreSQL and pinned historical A blobs.

A repeated call returns the same immutable report only if recomputation exactly matches stored values; no overwrite or new decision after a history mismatch. Concurrent assess calls on the same B6 record are serialized by an upstream B6 row lock. Foreign projects cannot inspect another project's report.

## PostgreSQL

Migration 050_ke_quality_gate.sql adds knowledge_fact_quality_gates with:
- Strong composite FK to exactly one B6 snapshot and its B5/B4/run/batch chain;
- immutable canonical input SHA and report SHA, decision, handoff_allowed and count invariants;
- append-only UPDATE/DELETE protection;
- one gate per project/run and one per project/dedup.

The findings are an immutable JSONB array with reason codes and counts. Store no raw A source text, whole MiMo prompts, API keys or media bytes.

### QA

    python -m unittest discover -s tests -p 'test_ke_stage11_quality_gate.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

Run on PostgreSQL17 in Linux and Windows CI. Test clean and warning handoff, empty/quarantine-only blocking, 80-candidate overflow gate, independent website trust, partial acknowledgement, B5 rejection, costs, pack count, source completeness, multi-pack, A video export pin availability/missing/SHA mismatch (without MiMo video processing), immutable report, historical replay across new profile revision and cross-project isolation. All “provider” outputs are fake.

## Next boundary

Stage12 B8 Human Knowledge Review requires separate explicit implementation. UI or API must show all warnings, source citations, B6 grouped proposals and possible conflict candidates, collect reviewer actions, and protect against silently approving rejected/skipped/quarantined claims. Human Review remains an additional decision before any immutable B9 KnowledgeSnapshot or C Adapter.

**Do not auto-merge B PR #30, do not call paid MiMo and do not claim production release solely from an offline B7 gate.**
