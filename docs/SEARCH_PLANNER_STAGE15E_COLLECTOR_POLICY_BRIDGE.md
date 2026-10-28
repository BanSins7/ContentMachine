# Search Planner SP-15E — Collector policy/quota bridge (read-only)

**2026-10-27. Source implementation committed to the independent Search Planner branch. Real Collector integration and PostgreSQL17/CI PASS remain unverified.**

## Objective and external truth

Collector PR #20 independently maintains `intelligence/collector/registry.py` with *declared* operations and `policy_gate.py` with DB reads from `sources`, `source_policy_versions`, `source_quota_ledger`. Neither a declaration nor an enabled global policy proves provider credentials, actual adapter health, project consent, verified locale, published price or external execution readiness.

## Delivered SP-15E implementation

- `intelligence/search_planner/capabilities/collector_db_port.py`: optional `CollectorPolicySnapshotPort` with server-owned `declaration_supplier(project_id)`. The source envelope must match `collector-source-declarations/v1` and the exact project; source IDs are bounded and unique. No Collector import or fallback registry copy.
- The `.snapshot(pid)` call uses existing SP09 `project_collector_declarations` to project declared operations, **always downgrading source enablement, readiness, credentials, quota and pricing to unavailable**. It cannot grant provider dispatch.
- `.inspection(pid)` performs one PostgreSQL `REPEATABLE READ READ ONLY` transaction for the declared sources, reading only source status, reviewed rights/retention and active request quota. No credentials, query content or provider IO are accessed. Stale/missing policy and quota are blockers. Unexpected database errors produce a redacted `policy_preflight_unavailable`, with no fabricated quota.
- Even when global policy/quota checks pass, the response retains `operationally_ready=False` and `external_dispatch_authorized=False` and lists missing adapter, credential, pricing, locale and project consent evidence.
- `SearchPlannerService(collector_policy_port=...)` wires it server-side. Without injection, no source is listed and no Collector DB read occurs. Existing SP09 matcher can reuse its declared-only snapshot.
- New protected read-only local `GET /api/projects/<project_id>/search-planner/collector-readiness` rejects query parameters and uses existing local project authorization boundary.
- Eleven offline contract tests in `tests/test_search_planner_stage15_collector_readiness.py` cover SQL read-only shape, missing Collector schema, bad quota, stale policy, forged declarations and GET validation. Ubuntu/Windows focused QA includes them.

## What is explicitly still unavailable

1. Real independently audited Collector publisher, low-privilege policy/quota SQL role and project-specific execution consent; the branch is unmerged.
2. Independently authenticated credential/adapter health, verified platform language/market capability, pinned pricing and true provider quota readiness.
3. Real Collector durable intake + externally re-read receipts. Existing SP12 mock receipts are QA only and do not authorize provider execution.
4. PostgreSQL17 fresh/upgrade integration, real two-process race and WAL restart tests, Windows/Linux deployed service lifecycle and exact-head green Actions. Parent SP-15 and SP-16 remain open.

**Safety:** An available declaration, global policy or quota is not a source query authorization. No adapter is auto-installed, no paid worker starts and no fake Collector receipt is ever produced by this bridge.
