# PIE-MOD-09 — Outcome & Comparison Policy (candidate only)

**Owner:** `feature/patterns-insights-full-module`. **Artifact:** `pie-outcome-comparison-policy/v1`. **Implementation:** `intelligence/patterns_insights/research/outcomes.py`. **Production status:** NOT LIVE; UAE UA-MOD-25 research exporter and authoritative research approval service are not available.

## Study preregistration and outcomes

`OutcomeComparisonPolicy` requires all study choices explicitly (nothing defaults to a percentile, a cutoff or a control group): `policy_version`, SHA-256 of the frozen upstream design, one of three named endpoints, version of the endpoint definition, canonical Decimal-text lower and upper thresholds, exact endpoint operators, baseline=`low`, **one** frozen stratum SHA, UTC-offset-aware inclusive observation window, and separately declared minimum N in high/low. `policy_sha256` is a deterministic canonical hash of this design. These values are source/policy identifiers, not trusted preregistration signatures.

| Endpoint | Status in current `uae-to-pie/v1` row contract | Behavior |
|---|---|---|
| `breakout_score` | Explicit completed raw Group C C7 decimal string | Available for **candidate-only** high/low partition; raw scale is NOT [0,100], and not normalized |
| `creator_ratio` | Not exposed as independently evidence-bound observation | `outcome_unavailable`, **not** recalculated from C7 or guessed |
| `creator_adjusted_views` | Not exposed as independently evidence-bound observation | `outcome_unavailable`; no inferred numerator/denominator or fabricated sample |

## Comparison group assignment

The policy selects **all** members of exactly one strict PIE-MOD-08 comparable stratum, with an exact stratum SHA. All other included posts remain in `other_strata_video_ids`; they are **not** missing data and cannot be silently folded into the contrast. Each selected row must have its `observed_at` inside the explicitly approved inclusive window and at or before the frozen UAE `as_of`; any out-of-window member blocks the entire comparison. No automatic overlap/matching: comparable controls and covariate balancing belong to PIE-MOD-10.

For complete observed C7 values: `low` when `score <= lower_bound`, `high` when `score >= upper_bound`, otherwise `neutral`. Require `lower_bound < upper_bound`, positive explicit `min_low_count` and `min_high_count`, and actual group sizes satisfying those minima. Neutral records remain in the artifact and count towards `selected_total` but **never** become implied controls or p-values. Each group contains exact canonical `videos.id`, frozen row SHA, UAE observation version and explicitly supplied weight text; `original_exclusions` and `included_total` are preserved. There is no statistical effect, test, CI, significance, or insight publication in this stage.

## Approval handshake, fail closed, and limits

A separate injected `DesignApprovalAuthority` must return a **typed** `DesignApprovalDecision` pinning `project_id`, frozen study/policy SHA, candidate snapshot SHA and stratum SHA, approval/reviewer IDs, pre-registration time (no later than window start), review/check/expiry times, and positive `approved`, `active`, `reviewer_authorized`, `not_revoked` flags. All fields are checked against an explicit timezone-aware `now`, including a maximum 5-minute check freshness. Missing, stale, revoked, wrong-project, version-mismatched, or untyped decisions block. The authority is queried **twice** (before/after membership classification), and any change in approval identity or denial blocks the entire output. Provider errors are converted into typed safe exceptions without leaking provider detail.

**Trust boundary:** This design gate checks an injected authority's claims, **not** the authenticity of the authority itself; tests use clearly synthetic approvals. It does not mark a candidate as live, permission-verified, or statistically approved: `CandidateOutcomePlan.verified_live=False`, `ready_for_analysis=False`, `approved_live=False`, `evidence_state='unverified'` always. A later runtime must authenticate both independent UAE producer and reviewer authority and recheck source rights at read time. `replay_candidate_outcome_plan` reconstructs from frozen snapshot + stratum + typed design and detects tampering; offline replay never refreshes human approval or rights.

## Implementation and QA

Code: `outcomes.py`, module exports, `tests/test_pie_outcomes.py`. The tests exercise raw negative/over-100 C7, explicit low/high and neutral, inclusive window, full-population counts, synthetic preregistration, reviewer revocation between checks, expiry/stale/mismatched receipts, missing other endpoints, thresholds, version/hash identity, resealed tampering and replay.

Run `python -m unittest discover -s tests -p 'test_pie_*.py' -v` and `python -m compileall -q intelligence/patterns_insights tests/test_pie_outcomes.py`. Record **actual** full-repository/PG17 CI outcomes in the tracker; do not count simulated pure-unit smoke as integrated tests. No new migrations, side effects, SQL queries, LLM calls, peer branch writes or statistical inference.

**Next stage:** PIE-MOD-10 must use this explicit study and stratum policy for control/contrast assembly and leakage checks; any real significance inference remains disabled until upstream and human approvals can be verified.
