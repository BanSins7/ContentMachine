# PIE-MOD-07 — Reproducible Analysis Dataset Builder

**Owner:** `feature/patterns-insights-full-module`. **Contract:** `pie-research-candidate/v1`. **Status:** implemented **pure candidate builder**; not a live research handoff, not a verified inference dataset.

## Architecture / invariants

The builder is independent of UAE, QDP, SRE, A/B/C runtime, PostgreSQL, LLM and networking. It uses only the frozen `PIEValidatedDataset` inside a `UAEReadResult`, a mandatory `SourceEligibilityGate` and an explicitly versioned immutable `ResearchBuildSpec`. The input must revalidate byte-equivalent to its originally validated manifest. The gate rechecks current project/source policy **and every included row**, including a final rights recheck after scanning; if any row is denied or revoked, **no snapshot is returned**, and no silent reduction of N occurs.

`build_candidate_dataset(read, gate, spec)` makes a deterministic, immutable `CandidateResearchSnapshot` with:

- Exact project/source/policy, UAE run/as_of/source metric/C configuration/lineage/producer version and complete original manifest; original SHA-256 and checked-receipt labels.
- Canonical ascending included `videos.id` and each exact UAE snapshot ID, row hash, feature hash, metrics/C7 hash, **unmodified** source row features and **raw unbounded Decimal text** C7 (no normalization and no C recalculation).
- Original excluded IDs and explicit reason codes. The original scanned/included/excluded counts are preserved exactly; duplicates and overlaps have already been rejected by `validate_dataset_manifest` and are replay-checked.
- An **explicit weight for every included video**, in ascending video ID order, with strict positive finite decimal-text semantics (no inferred or implicit weights). `weight_rule_version`, `design_sha256`, `algorithm_version`, `selection_reason_code` and an exact `frozen_as_of` are required. These are *labels of design*, not approval or evidence of statistical validity.
- A frozen feature dictionary with per-family feature names, types, present/absent/null counts; ordered B3-like sequences retain their duplicates. `null`, `false`, zero, absent and empty lists are never conflated. Score dictionary binds Group C config and metric definition/version.
- Canonical sorted JSON and `snapshot_sha256=SHA256(canonical_json)`. An unchanged input/design/receipt yields the same bytes and digest, while a new source version, observation, design, weight or receipt changes identity. This checksum is **integrity only**, not publisher authentication.

The result has constant `verified_live=False` and `ready_for_analysis=False`. A mock-positive gate only permits **synthetic/offline serialization tests**. Its JSON must not be supplied to statistics or LLMs as source-authenticated evidence. There is no persistent write, migration or job; storage access/retention/visibility remain gated by later runtime.

## Offline replay

`replay_candidate_snapshot(raw, expected_sha256)` rejects malformed/duplicate JSON keys, nonfinite values, unknown snapshot state/version, mismatched checksum and any resealed internal contradiction. It rebuilds the *entire snapshot* from the pinned UAE manifest + source pointer + typed weights/specification and compares exact reconstructed members, feature dictionary and exclusions. It does **not** contact the current rights authority, and a successful offline replay must never be used to claim current access rights.

Synthetic golden file `tests/fixtures/pie/pie_research_candidate_identity_v1.json` pins the snapshot and dictionary digest for the invented 2-included/1-excluded selection. It is explicitly **non-authoritative** and has no usable producer credentials.

## Mandatory failure cases and QA

Tests cover: positive repeatability (including golden SHA and replay), clone/mutation resistance, source-version invalidation, missing/extra/duplicate weights, invalid scores/weights/timestamps, unknown/absent features, cross-project/snapshot lineage rejection in the preceding contract and gate, policy denial before read, policy revocation after scanning, row hash poisoning, forged dataclass and resealed JSON tampering. No proprietary or live data are involved.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights tests/test_pie_research_dataset.py
```

**Local Python 3.13:** see tracker stage PIE-MOD-07 for observed count; conditional PostgreSQL tests are SKIPPED without `CM_TEST_PG_DSN`. Linux/Windows GitHub CI is **not PASS until its completed result is observed**. PostgreSQL 17 fresh/upgrade and real UAE handshake remain unverified.

## Deferred by design

No automatic effect-size calculations, treatment/control construction, confidence intervals, holdout splitting, summary inference or signal publication. PIE-MOD-08 will partition comparable strata; PIE-MOD-09+ requires explicit approved outcomes. UAE still lacks its planned UA-MOD-25 real row-level exporter, source-policy authentication and provider production rights verification; early-stage structural integrity must never be presented as production-readiness. No changes to main/peer branches.
