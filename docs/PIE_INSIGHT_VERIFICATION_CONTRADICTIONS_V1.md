# PIE-MOD-31 — Insight Verification, Contradictions & Hallucination Guard v1

Branch: feature/patterns-insights-full-module. This stage ONLY changes PIE-owned files.

**Wire version**: pie-candidate-insight-verification/v1.  
**Implementation**: intelligence/patterns_insights/research/insight_verification.py.  
**Migration**: intelligence/migrations/205_pie_insight_verification_events.sql (additive).  
**Tests**: tests/test_pie_insight_verification.py (31 authored methods, synthetic and opt-in disposable PostgreSQL17).  
**Status**: implemented candidate-only verification and quarantine; NO public read/publish/approved-state API and NO real UAE/reviewer signoff.

## Trust boundaries and design

An already saved stage30 candidate is **not** authorized for display just because the generation code previously rechecked rights. A later rights revocation, new pattern revision, source edit, forged phrase or unavailable source must fail closed.

**verify_candidate_insight(connection, query, series_id, generation_sha256, authorizer, reader, gate, signer)** does the following under the same per-project advisory transaction lock used by stage25 registry writes, stage27 invalidations and stage30 generation:

1. Call stage28 prepare_candidate_insight_prompt, which itself invokes stage26 project+owner-scoped current UAE, source-policy, every original row's rights and eligible match checks, plus stage27 current invalidation. Authorizer must be independent; source/model metadata are not self-attesting. Reject denied rights BEFORE loading the stored generation.
2. Independently re-evaluate stage27 exact source revocation again and load the **absolute latest** stage25 immutable revision by project+series+current owner. Verify source hypothesis, original immutable registry payload hash and exact revision. Old stored generations cannot be audited as current candidates.
3. Fetch the stage30 generated artifact by exact project, generation SHA and series. The DB's generation revision, source prompt SHA and hypothesis SHA must match the newly constructed source. A missing or cross-project generation is refused, not treated as an empty successful audit.
4. Independently reconcile original registry JSON to the stage28 source facts (rather than trusting model-authored text): exact project, owner, hypothesis, factor/feature/metric, registered discovery-only cohort, matched-pair count and original sample pair identities count, bin support count, exact normalized rational raw difference and its original physical unit, descriptively directional status and original candidate status. A registered p/q-value or confidence interval, promoted approval, false holdout confirmation or unapproved rights claim is contradictory to this candidate schema and cannot be silently accepted.
5. Independently regenerate the *entire* stage30 expected canonical result from the frozen current stage28 source facts and deterministic enumeration; compare JSON fields and SHA byte identity. Any modified summary, invented percentage, source link, causal claim, altered sample N, unsupported effect, missing caveat, alternative source version, metadata addition or changed model mode becomes a redacted **quarantined** verdict. Neither untrusted prose nor video/source-row IDs enter the audit output.
6. If stage30 mode is provider_reported, require a current same-project stage29 committed reported_charged outcome, exact response SHA and prompt SHA linked to the original claim. Missing/uncertain model result quarantines the saved candidate even when the copied draft text looks plausible.
7. Require an explicitly recorded **known stage23 robustness/sensitivity status**, nonempty stage24 original quality dimensions and original missingness diagnostics as bounded counterevidence metadata. An absent or unrecognized source status is **missing_counterevidence_audit → quarantined**. Stage23 is NOT an independent validation or independent counterfactual; if all source checks pass, the audit records independent_not_measured and remains blocked from public release. This is not a statistical significance determination.

All possible successful internal outcomes retain may_public_read=false, may_publish=false, causal_claim=false, human_review_confirmed=false, independent_counterevidence_confirmed=false. Verdict candidate_checked_for_review is **not** a green publication gate. This prevents an unverified/unknown score from being promoted.

For source-rights denial, absent/stale revision or missing generation the function raises a generic typed boundary error and returns **no source text** and no public artifact. For a still-authorized candidate whose registered source facts contradict the stored draft, a compact immutable quarantine reason code is stored. A quarantine payload stores hashes/status only — no media IDs, model text, external source URLs, prompt secrets, creator data or original raw source observations.

## Append-only audit and SQL guard

Migration205 creates pie_candidate_insight_verification_events:

- Primary key (project_id, verification_sha256), composite project + stage30 generation foreign key, and composite project + source pattern version foreign key.
- Bound source registry payload SHA, version, hypothesis, prompt SHA and immutable canonical redacted audit JSON.
- Exact enumerated verdict (candidate_checked_for_review, quarantined), reason (exact_source_consistent, numeric_or_scope_contradiction, fabricated_or_modified_draft, missing_counterevidence_audit) and counterevidence_status (independent_not_measured, unknown_quarantined).
- SQL checks reject approved/publishable or causal audit JSON; before-update trigger rejects mutation. Identical repeated verification is idempotent; new source evidence/changes produce new verification SHA/reason rather than rewriting a prior audit.
- BEFORE INSERT trigger independently verifies exact stage30 generation and stage25 source version/payload, then rejects current-latest version mismatch or ANY of the nine original exact-source stage27 tombstones. This blocks direct SQL bypass of model result provenance and revocation status, although SQL cannot authenticate human/row source-rights; the trusted host and fresh stage26 read remain mandatory.

A later rights change cannot retroactively erase a previously generated candidate or audit. It **must** suppress any later query/read. This stage intentionally provides NO public read API; future review/release work must freshly authorize each read. No historical revision fallback is allowed.

## Numeric oracle, adversarial and reproducibility coverage

The fixed synthetic example has four original matched pairs, high=(2,6,8,9) and low=(1,3,4,7), so the correct unweighted mean raw difference is [(2−1)+(6−3)+(8−4)+(9−7)]/4 = 10/4 = **5/2 ms**, **not** 250% uplift. Altering 5/2 to 6/2, changing ms to seconds, adding fake p=0.001, claiming new holdout, changing N=4 to N=400, or fabricating supported bins is quarantined.

31 authored test methods include:
- positive internal candidate_checked_for_review with independent counterevidence explicitly unmeasured;
- manual exact-rational oracle, source unit and discovery cohort; stable audit SHA and idempotency;
- hostile numeric/feature/unit/status/cohort/p-value/holdout/owner/source-data manipulation, invalid source hash and missing/bogus robustness metadata;
- fake 98% causal success inserted into a rehashed stage30 draft: quarantined without leaking that text;
- absent/stale original pattern/rights/revocation: no source output and no successful audit;
- source-row IDs, video identities, URLs, secrets and injected instructions must not appear in safe audit;
- prior model attempt relabelled as paid/uncertain, source prompt/response mismatch and untrusted conclusions fail to clear independently;
- optional PostgreSQL17 schema fresh/upgrade existence, append-only triggers, and cross-project/missing generation foreign-key denial when CM_TEST_PG_DSN is provided for an explicitly disposable named-test database.

Positive fixture stage28 rights and stage27 invalidation checks, fake database and source quality counterevidence are **MOCKED** — no live UAE/producer rights/reviewer is implied. No real database mutation beyond committed schema files and no model payment/retry is performed.

Suggested validation commands on a full checkout:

    python -m unittest discover -s tests -p 'test_pie_insight_verification.py' -q
    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_insight_verification.py

Disposable PostgreSQL17 CM_TEST_PG_DSN tests additionally cover migration/upgrades. Local tests/compileall and independent PG17 fresh/upgraded/concurrency results were NOT EXECUTED when committing this stage; GitHub CI status must be observed before QA can be called passed. No claim of production-ready rights, statistically confirmed effect, source license, paid LLM reconciliation or external publishing.

## Next

PIE-MOD-32 — human review and feedback loop with immutable reviewer decisions, disagreement/appeal, calibration and separation of AI proposal vs approved release. Stage31 candidate_checked_for_review must never be interpreted as permission to publish or skip current-rights checks.
