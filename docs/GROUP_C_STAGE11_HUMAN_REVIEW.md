# Group C — GC-MOD-11: Immutable Human Review (config + frozen cohorts)

**Branch:** `feature/group-c-full-module`  
**Migration:** `intelligence/migrations/058_group_c_human_reviews.sql` (additive, follows 057)  
**Code:** `intelligence/group_c/human_review.py`  
**Tests:** `tests/test_group_c_human_review.py` (13 pure + 12 disposable PG17 methods), updated `tests/test_group_c_runtime_schema.py`  
**Status:** CODE IMPLEMENTED; PostgreSQL17 executable tests and real human IAM integration **NOT VERIFIED**; score writer and config activation NOT enabled.

## Trust boundary — no autoapproval

A frozen Stage10 cohort is **`frozen_unreviewed`**, not an approved input. This stage records an immutable *request to review* and one final *human decision* for an exact project, target, frozen creator + engagement-prior cohort (optional niche), exact D1 source and version, calculation config version, explicit alpha/k, empirical prior ratios, sample sizes and explicitly reviewed minimum sample sizes. It cannot infer missing parameters, automatically accept peers or activate a config.

**Auth is externally owned.** `PostgreSQLHumanReview.record_decision` REQUIRES an injected trusted `HumanReviewerVerifier` implementing:
```python
verify(conn, *, signed_assertion, project_id, subject_sha256, decision)
  -> VerifiedHumanReviewer
```
The implementer MUST independently authenticate a **signed human action**, enforce project-specific review roles, actor-is-human (not service/bot), exact request subject SHA and decision binding, freshness/replay protection and stable provider-event audit. This module has NO default verifier or self-reported `reviewer_id` shortcut. `VerifiedHumanReviewer` is not inherently trustworthy when constructed by untrusted callers; only a controlled, authenticated verifier adapter may return one. The production external IAM connector **does not yet exist in this branch**, so actual reviewer onboarding/verification is `BLOCKED_EXTERNAL`; test-only `_TestVerifier` is explicitly synthetic.

**DB security boundary:** all database writers must run under a restricted service role with no independent INSERT permission on `group_c_review_decisions`; the trusted review service is responsible for invoking the verification flow. PostgreSQL stores the reviewer provenance and enforces immutability/idempotency, but does NOT independently verify an external cryptographic signature or IAM project role. A privileged database owner can bypass an application-layer IAM check. Production IAM binding and DB privilege setup are deployment prerequisites, not claimed complete by this stage.

## Exact review subject (no mutable defaults)

`build_human_review_subject(...)` creates an immutable `HumanReviewSubject` containing:

- Canonical project ID, target `videos.id`, source ID, exact `source_policy_version_id`, reviewed `platform_post_views` + **metric version**, platform and C content type.
- Exact `metric_calculation_configs.id` and `calculation_version`, positive explicit `alpha` and `bayesian_k` (NUMERIC(30,12) scale), exact `prior_source_policy`, `cohort_selection_rules`, `growth_policy` JSON objects, `normalization_enabled=False`, 48h half-life and immutable 0.45 / 0.30 / 0.15 / 0.10 raw weights.
- Exact creator, optional niche and engagement-prior **Stage10 freeze IDs**, `legacy_cohort_id`, rules/selection/evidence SHA, cohort size and empirical median/prior values, same target/source/policy/metric version/project/platform/kind. Every freeze must be `frozen_unreviewed`.
- **Explicit minimum sample sizes** for creator and prior (and niche if included), 1..500. The request is rejected if actual `member_count` is smaller. These minimums are human-specified review criteria, never hidden defaults.
- Stable canonical `review_payload` and `subject_sha256`. Changing any frozen sample SHA, snapshot/counter lineage captured by Stage10, reviewer sample minimum or config alpha/k/version or immutable policy/rule/growth JSON yields a **different** subject; an earlier approval cannot be silently transferred.

The pure builder is **not** a signing/approval service and never sets `approved=True`.

## PostgreSQL 058 and write semantics

- `group_c_review_requests`: immutable request UUID, project and target ID, source/policy/view definition/version, config ID/version/alpha/k, exact frozen cohort IDs and explicit minimum counts, subject SHA and full JSON payload. `UNIQUE(project_id,subject_sha256)` makes exact replay idempotent.
- `group_c_review_decisions`: immutable reviewer decision `approved/rejected`, one final decision per exact request, bound `subject_sha256`, reviewer ID, identity provider, authentication event ID, signed assertion fingerprint and nontrivial reviewer note. `UNIQUE(identity_provider,authentication_event_id)` prevents reuse of one verified human action on different review records.
- Four triggers enforce append-only request/decision records and exact cohort/config/request lineage at insert time; there is no automatic approval trigger or SQL write to `metric_calculation_configs.is_active`.
- `PostgreSQLHumanReview.create_request` opens an independent top-level `SERIALIZABLE` transaction, reloads immutable freeze evidence and inactive config, checks policy rights/TTL on the **target and every frozen member** and creates or reuses an exact pending request.
- `PostgreSQLHumanReview.record_decision` opens another `SERIALIZABLE` transaction, reloads the same exact config/frozen payload, verifies its SHA matches the request, rechecks current D1 rights/TTL, requires a trusted external signed-human verifier and appends one final decision. A contradictory follow-up is `review_decision_conflict`; an identical verified replay returns the same decision ID.
- `PostgreSQLHumanReview.read_status` is read-only: without a decision returns `pending_human_review`; an approved decision only indicates **human-reviewed evidence**, not a permission to score or the runtime `complete` status. Approval remains tied to the old exact subject and must be revalidated again by Stage12/20/21/24.

Example (IAM is explicitly required):
```python
from intelligence.group_c import PostgreSQLHumanReview

review = PostgreSQLHumanReview()
pending = review.create_request(
    conn,
    project_id=project_id, config_id=unactivated_config_id,
    creator_freeze_id=creator_freeze_id,
    prior_freeze_id=prior_freeze_id,
    minimum_creator_posts=reviewer_selected_minimum,
    minimum_prior_posts=reviewer_selected_prior_minimum,
)
assert pending.status == "pending_human_review"
assert pending.approved is False

# Only after an actual external human reviewer explicitly approves:
approved = review.record_decision(
    conn, request_id=pending.request_id, decision="approved",
    signed_assertion=external_iam_signed_user_action,
    note="Reviewed the exact source, sample and parameters",
    reviewer_verifier=trusted_iam_adapter,
)
# Still NOT a score or active config:
assert approved.approved is True
```

There is **no** access to a production user authentication system in this stage. The example expresses the intended integration boundary, not a tested production reviewer workflow.

## QA and migration boundaries

- **13 authored pure tests:** exact subject SHA, frozen cohort/version/sample sizes, param changes, optional niche, cross-project/creator/source/platform/kind rejection, missing alpha/k, fixed C weights and time half-life, unknown SHA, no implicit human reviewer.
- **12 authored disposable PostgreSQL 17 tests:** pending request, approved signed-human fixture, rejection/replay/immutability, IAM missing or invalid, changed sample criteria/config versions, source revocation before request and after request/before review, signed IAM event reuse across subjects, forged verifier response, versioned new config, legacy-only cohorts denied, migration checksum.
- Existing Group C workflow discovers `test_group_c_*.py` on Linux and Windows; separate PG17 steps added to fresh and upgraded test databases. Upgraded test now excludes **056,057,058** from pre-C setup and asserts all three install in order.
- **Actual tests not executed locally**: no local PostgreSQL17/psycopg/full GitHub checkout. GitHub Actions runs on latest branch were **queued** at last inspection. Static source/migration checks are *not a PostgreSQL PASS*. External IAM, signed reviewer identity and end-to-end source rights remain **BLOCKED_EXTERNAL**.
- Historical `003_performance_c.sql`, `004_performance_c_integrity.sql`, and old Group C `056` remain unchanged. **Important earlier fix:** Stage10 `057` PL/pgSQL insert-guard body had malformed dollar quoting; its source was repaired before any verified deployment. If 057 is already recorded in a real migration ledger, migration checksum conflict must be resolved explicitly: do NOT silently overwrite an applied migration. No applied deployment has been verified in this session.

## Next

**GC-MOD-12 — config activation.** Revalidate currently approved, signed human review and exact canonical member/config lineage, fixed weights and half-life, alpha/k and source rights. Implement explicit version replacement instead of rewriting historical input versions. No C7 score should be treated `complete` until later C0–C7 worker, persistence and integrity stages.
