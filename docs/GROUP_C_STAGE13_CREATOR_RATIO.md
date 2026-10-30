# Group C — GC-MOD-13: C1 Creator Ratio with exact reviewed creator-cohort lineage

**Branch:** `feature/group-c-full-module` (standalone, `main` untouched)  
**Code:** `intelligence/group_c/creator_ratio.py`  
**Tests:** `tests/test_group_c_creator_ratio.py`  
**CI:** `.github/workflows/group-c-module.yml` Linux/Windows pure test discovery + PG17 fresh/upgrade.  
**Migration:** NONE. Do not modify historical 003/004/056/057/058/059.  
**Status:** CODE IMPLEMENTED; production IAM/score workflow and exact-head CI PG17 **NOT VERIFIED**.

## Explicit formula and numeric contract

```text
C1 Creator Ratio = (current_raw_views + reviewed_alpha) /
                   (frozen_creator_cohort_median_views + reviewed_alpha)
```

`compute_creator_ratio(views: int, creator_median_views: exact decimal,
alpha: exact decimal) -> Decimal` is **pure**: it does not import Analytics,
persist anything, contact providers, normalize, or assert approvals. It uses
the pre-existing independent `group_c.pure_math.d` and `q`: decimal local
precision 80, `ROUND_HALF_UP` to **12 decimal digits**, PostgreSQL
`NUMERIC(30,12)` overflow refusal. Views must be exact nonnegative BIGINT
(no bool, float, string, null); creator median must be finite nonnegative
NUMERIC(30,12), alpha must be explicitly supplied, finite, **positive**
NUMERIC(30,12). Inputs that would be rounded at the storage boundary are
rejected `invalid_c1_precision` rather than silently changing an approved
alpha/median. A zero current view count is valid for the *standalone C1*
ratio; the complete C1–C7 pipeline will reject it where engagement rates
require a nonzero denominator. No implicit prior/alpha/missing median defaults.

Reference pure golden vector from pre-existing Stage07 Analytics parity:
`views=200, creator_median=100, alpha=5` yields exact
`creator_ratio=1.952380952381`.

## Approved creator provenance, not just a frozen flag

`PostgreSQLCreatorRatio.derive` requires exact project/video/metric
snapshot/metadata/source/source policy/view version/config ID/creator freeze
ID/**approved request ID**, plus an externally injected
`CreatorRatioApprovalVerifier`. It owns a top-level independent
`REPEATABLE READ, READ ONLY` PostgreSQL transaction.

In that single stable DB snapshot, it:

1. Re-reads actual project/video, source and pinned snapshot via the canonical
   GC-MOD-05 `read_scoped_d1_bundle`. Uses GC-MOD-06
   `validate_c0_snapshot` with explicit `platform_post_views` and exact
   version, quality/exactness, non-assumed `observed_at`, retention and raw
   counters. Checks the pinned snapshot is **the most recent RAW observation**
   in D1 for that video/source, even if a newer retracted/version-changed
   snapshot exists. No last-good fallback.
2. Reads the specific GC-MOD-10 creator freeze, the real
   `comparison_cohorts` parent and every frozen creator peer. Requires
   exact project, target snapshot/metadata IDs, canonical creator ID,
   platform/analytical content kind, current source/policy revision,
   definition and version of views, canonical parent `is_frozen=TRUE`,
   exact frozen median and member count, original peer snapshot/media IDs,
   original raw views and old membership snapshot IDs. Rejects any target
   self-comparison, peer duplicates, cross-project/creator/metric leakage.
   All target and frozen peer source rights/TTL rechecked with the canonical
   Stage05 policy adapter.
3. Checks **the same frozen creator cohort** was named in the GC-MOD-11
   actual approved human request/decision and that the exact GC-MOD-12 config
   is **currently the one global active configuration** with an immutable
   matching activation event. Reconstructs the complete Stage11 review
   subject from config alpha/k/fixed weights/48h/prior, cohort/growth-policy
   JSON and all creator/prior/optional-niche frozen cohort hashes; verifies
   canonical `subject_sha256` and JSON payload. A former approval for
   another version/project/snapshot cannot be recycled.
4. Requires **trusted external** `CreatorRatioApprovalVerifier.verify_c1_use`
   to revalidate original human IAM identity/event, global configuration
   authorization and the current review/activation grant for the **exact**
   project/video/snapshot/creator cohort. No permissive production
   verifier is installed. A synthetic test verifier is strictly confined
   to disposable PG17 test fixtures.
5. Calculates the pure C1 result and returns frozen
   `CreatorRatioResult` with exact source/video/snapshot/metadata IDs,
   creator and config IDs, human review ID + approved decision ID,
   activation ID, frozen creator median/alpha/raw views, the numeric
   `creator_ratio` and canonical `evidence_sha256` of those inputs.

The `_read_frozen_cohort` helper reads the exact same frozen evidence as
the Stage11 helper **without `FOR SHARE` locks**, since PostgreSQL cannot
acquire row locks in a `READ ONLY` transaction. The `REPEATABLE READ`
snapshot preserves a consistent read without any SQL INSERT/UPDATE/DELETE.

## Status and side effects

`CreatorRatioResult.status='c1_derived_unpersisted'` and
`ready_for_c7=False` **always**. It does NOT return
`score_status='complete'`, persist a `calculated_metrics` row,
ACK QDP, invoke a worker, activate any config or publish to Analytics.
C1 is an independently auditable partial result only. The Stage07
`compute_c_metrics` continues to be unexposed as a production scorer.

The trust adapter has a documented Protocol but **no real production IAM
connector**. Returning `True` without verifying the original signed
approvals is dangerous. This branch MUST remain blocked from production
score persistence until the IAM adapter, database service permissions,
GC-MOD-14..21 worker stages and current source/QDP E2E are verified.

## QA (do not claim CI PASS until observed)

`tests/test_group_c_creator_ratio.py` covers independent C1 golden
Decimal vector, zero-view exactness, even-size median and HALF_UP,
Decimal global-context isolation, invalid floats/bools/BIGINT range,
missing/negative median/alpha, denominator/overflow, subscale inputs,
cross-project, snapshot/source/policy/metric version and wrong author,
invalid frozen or legacy parent, peer target-self, duplicate and changed
raw evidence; plus disposable PG17 fixtures for positive exact reviewed
creator+prior cohort (only creator median contributes to C1), pending
review/IAM failure, source revocation, latest raw source supersession,
changed peer creator and deterministic read-only replay.

- Group C pure suite is discovered on Python 3.11 Linux and Windows.
- Dedicated PG17 `test_group_c_creator_ratio.py` jobs run on both a freshly
  migrated database and a pre-module upgrade database, after Stage12 tests.
- PG tests require `CM_TEST_PG_DSN` and `GC_TEST_DISPOSABLE_DB=YES`.
  Test-only setup uses Stage11/12 synthetic cohorts and IAM; **this does
  not prove real signed human authorization**.
- At implementation time neither executable PostgreSQL17 service nor
  complete repository checkout was accessible locally; GitHub Actions
  remained queued. Authored tests and statically inspected SQL are **NOT
  equivalent to tests passing**. Record exact test/CI results if and when
  available; never fabricate them.

**Next:** GC-MOD-14 — C2 Niche Outlier (optional approved niche cohort with
the same project/source/metric version, `(views+alpha)/(niche_median+alpha)`);
must **never** add C2 to C7 or synthesize a niche median when missing.
