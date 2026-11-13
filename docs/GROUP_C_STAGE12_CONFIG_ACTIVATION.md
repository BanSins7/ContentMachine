# Group C — GC-MOD-12: Explicit Config Activation

**Branch:** `feature/group-c-full-module` (standalone, main unchanged)  
**Schema:** `intelligence/migrations/059_group_c_config_activation.sql` (new, additive, follows 058)  
**Code:** `intelligence/group_c/config_activation.py`  
**QA:** `tests/test_group_c_config_activation.py`, migrated `tests/test_group_c_runtime_schema.py`, Group C workflow (fresh/upgrade PG17).  
**Status:** CODE IMPLEMENTED. Exact-head PG17 / full suite CI **NOT VERIFIED**, external real IAM **BLOCKED_EXTERNAL**. **NO C7 worker or persisted breakout score**.

## Global one-active constraint — explicit CAS, not project-local

Historical `003_performance_c.sql` contains a PostgreSQL partial unique index allowing only **one** `metric_calculation_configs.is_active=TRUE` row *for the entire database*. This stage deliberately does **not** pretend a separate active version can exist for each project. An activating request is associated with one signed Group C project-specific human review, but the active C calculation version is **global**.

`PostgreSQLConfigActivation.activate(conn,project_id,review_request_id,expected_config_id,replace_active_config_id,approval_verifier,ports=None)` requires all inputs explicitly:
- The exact canonical review request UUID and project; exact config ID. If no active config exists, `replace_active_config_id=None` is mandatory. If one exists, the caller MUST supply its exact, different positive ID as an optimistic **compare-and-swap** expectation. A stale/replaced ID fails `active_config_conflict`, never silently deactivates another project's selected version.
- An **approved** GC11 human decision tied to that exact review request and config; frozen creator and empirical engagement-prior cohorts (optional niche); all subject payload fields including immutable rules/selection/evidence hashes, platform/content kind, source/policy ID, metric definition+version, pinned alpha/k, prior source policy, cohort selection rules, growth policy, C fixed weights 0.45/0.30/0.15/0.10, 48h half-life, `normalization_enabled=False`, reviewer-selected minimum peer counts.
- All current D1 source rights and retention for target and **every admitted frozen peer** are rechecked under a single independent `SERIALIZABLE` transaction. The subject is re-created from the exact frozen rows and inactive normalization of `is_active`; only an activated/replayed configuration may have the state changed, never the formula inputs.
- The external `HumanApprovalAttestationVerifier.verify_approval` MUST independently validate the **original** signed human IAM event, current human/project reviewer grants, event freshness/revocation, subject and decision ID, identity provider and attestation SHA. A mere `group_c_review_decisions.decision='approved'` row is NOT sufficient. A **second, separately signed global activation action** must be passed in `signed_activation_assertion`, bound to the exact activating config ID, explicitly expected old config ID and privileged global activation capability (`group_c.activate_global_config`). The trusted IAM verifier must validate that **global administrator permission**; a project-only reviewer grant cannot switch this database-wide setting. There is NO installed real IAM connector in this branch. The test's `_SyntheticIAMApproval` is not trusted or usable in production.

This API is **not** a user-facing approval button, and does not invent a reviewer or stage11 decision. Production applications must inject a truly trusted IAM provider and ensure untrusted callers cannot supply arbitrary fake verifiers.

## Atomic transition & history

`activate` acquires/rechecks the current globally-active config, deactivates it only if the explicit replace ID matches, inserts immutable `superseded` event, activates reviewed config and appends `activated` event, **in one SERIALIZABLE transaction**. Historical `calculation_version`, alpha/k, JSON policies and all other formula inputs are left untouched; existing 004 config guard remains authoritative.

New additive `group_c_config_activation_events` stores project, config ID, exact reviewed request/approved decision IDs, subject SHA, event kind, prior/replacement config IDs, transaction XID and time, plus the SHA-256 of the *separately* signed global activation action. A partial unique index on the activation-action SHA (`kind='activated'`) prohibits reusing one signed action to enable multiple configurations; the matching superseded event may share the action SHA in the same atomic switch. UPDATE/DELETE forbidden; unique review request/event/config pairing prevents repeat event creation.

Migration 059 also installs deferred `AFTER UPDATE OF is_active` and `AFTER INSERT is_active=TRUE` constraint triggers on `metric_calculation_configs`. Changes must have a **matching same-transaction** approved activation/supersession event, or fail when committing. An event-insert guard verifies the exact approval request/decision, event matching enabled/disabled state, and current transaction identity. This prevents accidentally toggling an active flag without provenance; it **does not cryptographically authenticate humans inside PostgreSQL**.

Exact activation replay is idempotent only if the config remains globally active and the current source/IAM proofs are valid. Once superseded, reusing the *old* approval cannot reactivate it: a distinct new calculation version and newly approved review are required. A subsequent activation using another version does not rewrite prior activation events or historical configurations.

`read_approval(conn,project_id,config_id)` is **read-only historical activation evidence** with `currently_active` flag. It does not infer that the result is currently score-ready. Stages13–21 and 24 must still re-evaluate current D1, source version/expiry, reviewer grants, frozen peers and a successful C0–C7 execution before any `calculated_metrics.score_status='complete'`.

## Test coverage and isolation

`tests/test_group_c_config_activation.py` contains pure deterministic input + schema tests and disposable PG17 tests for:
- Canonical lowercase project + review UUID, strict positive config and replacement IDs, explicit global CAS, frozen receipt and no score payload.
- Exact human-approved activation, no implicit verified IAM, rejected/pending review, project/config mismatch, revoked source after approval, global previous config mismatch.
- Idempotent same-version activation, atomic switch from old to new version, preserved old alpha/k, preventing reactivation from superseded historical approval.
- Direct `UPDATE ... is_active=TRUE` and `INSERT ... is_active=TRUE` without matching event rejected by deferred PostgreSQL constraints, append-only activation events.
- Migration checksum installed once and no re-entry; fresh and main→056/057/058/059 upgrade consistency.

Tests MUST only run with `CM_TEST_PG_DSN` **and** `GC_TEST_DISPOSABLE_DB=YES`. PostgreSQL global active state is **administratively reset between test methods in this disposable DB only**, temporarily disabling the dedicated Stage12 toggle trigger. This is **not a production technique**; service/production roles must have no ALTER TABLE permissions to bypass audit.

The workflow runs pure Group C tests on Linux/Windows and explicit Stage12 tests after Stage11 on both PG17 fresh/upgrade databases. No local PostgreSQL17/psycopg or full checkout is available. A queued GitHub Action is not a PASS. Record each resulting failure and do not claim full QA until a green run is observed.

## Security/deployment constraints

The database service role MUST be prohibited from bypassing triggers, altering migration state or inserting activation/review decision audit rows except through controlled IAM-verified workflows. A database owner can bypass application authorization; triggers cannot replace role scoping or cryptographic IAM. The activation API does not accept a signed assertion at activation time, instead requiring a trusted service to **re-verify the persisted attestation**. The external IAM verifier and deployment-specific SQL grants remain blocking requirements.

GC-MOD-12 is config activation only, **not** any scoring or Analytics publication. No source metrics are fabricated, no default prior/alpha/k is added, and sibling module branches/main remain unchanged.

**Next stage: GC-MOD-13 — C1 Creator Ratio**, using exact approved frozen creator cohort and the same source/metric/creator lineage, Decimal `(views+alpha)/(creator_median+alpha)`, without self-comparison or silent normalization.

**Additional Stage12 security gate:** The original project cohort reviewer attestation and the *new* global activation signature are distinct proofs; the latter must authorize `group_c.activate_global_config` for the exact expected old and new config IDs. Only an external IAM connector can prove this. PostgreSQL records the action hash and denies cross-config replay, but does not validate a cryptographic signature itself. No production global IAM adapter was installed.
