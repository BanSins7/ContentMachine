# Search Planner SP-10 — trusted held task planning and immutable approval

**Date:** 2026-10-26. **Branch:** `feature/search-planner-full-integration`.  
**Code:** implemented in SP branch; **QA:** new offline tests committed, exact-HEAD Linux/Windows/PostgreSQL CI not yet confirmed. Provider calls: **0**. No Collector dependency, no merge to main.

## 1. Existing trusted inputs

SP10 does not accept `accepted=True` or `validated_query_set` from the browser. Production accepts **only** `project_id + validation_run_id` and bounded policy. `task_planner/repository.py::load_verified_planning_input()` reads existing immutable `validated_queries/v1` from `qv_runs, qv_decisions, qv_coverage, sqg_candidates`, plus SP07 original candidate↔hypothesis↔fact links from `get_selected_query_links()`. It then checks inside a PostgreSQL **REPEATABLE READ READ ONLY** transaction:
- exact project, qv run, completed production `semantic_local` and selected count;
- original `sqg_generations` completed, its immutable `publication_id, taxonomy_version, taxonomy_sha256, input_sha256`;
- matching `qv_outbox` event and `sqg_outbox.delivered_at` source ACK;
- actual approved Deep C9 V1 publication via `read_published_snapshot(conn,project,pub)`, including both Human Reviews and exact final review #2 tree;
- model/policy/source SHA pointers and all original source query IDs.

SP07 and QV source rows are immutable by existing triggers, so the two existing reusable QV readers may read before the joint identity transaction without a write/TOCTOU mutable-source window. Do **not** replace these reads with caller-provided JSON; injected readers are strictly trusted internal/testing ports.

## 2. New code and storage

| Path | Scope |
| --- | --- |
| `intelligence/search_planner/task_planner/repository.py` | Trust-checked, project-bound QV+Deep C9 provenance source and complete link validator |
| `intelligence/search_planner/task_planner/service.py` | `TaskPlanner`, deterministic `build_held_task_plan()`, `validate_plan_limits()`, `preview()`, `commit_held()` |
| `intelligence/search_planner/task_planner/commit.py` | Owner-gated, atomic/idempotent plan approval; re-check qv/SQG/source SHA under SQL transaction |
| `intelligence/migrations/054_sp_held_task_plans.sql` | Additive `sp_held_task_plans` immutable snapshot and `sp_held_plan_approvals` immutable audit, unique idempotency and FKs to QV/project |
| `intelligence/search_planner/service.py` | `preview_source_tasks()` read-only and `commit_held_source_plan()` owner-approved SQL mutation, no provider calls |
| `tests/test_search_planner_stage10_task_planner.py` | No-source, trust, cost, expiry, budget/candidate cap, source dedup, fairness, reordered input hash, SQL read boundary |
| `tests/test_search_planner_stage10_approval.py` | Default-deny owner guard, immutable snapshot, idempotent replay/conflict, multi-source routing, schema/trigger checks |
| `.github/workflows/sp-stage01-fast.yml` | SP-01..SP-10 focused QA |

Applied migrations `015_search_query_generation.sql` and `016_query_validation.sql` remain **unchanged**. The new schema introduces **only SP-owned** held-plan records, not an alternative QV registry/Collector source catalog. Immutable triggers use the existing `sqg_reject_immutable_write()` function.

## 3. Deterministic held-only task plan

`search-planner-held-task-plan/v1` includes project/Deep C9/version/SHA, generation/QV/outbox/model pins, capability catalog SHA and expiry, immutable limits, physical tasks, all blocked selected candidates with reason codes, complete hypothesis coverage, expected request/candidate counts and Decimal-cost ceiling.

Rules:
1. Each selected QV query uses the preserved candidate UUID, exact mode/text/language/fact ancestry and known source capability. Source declarations alone are never treated as ready; unknown price or quota means blocked.
2. Validate explicit locale/market and source capability, adapter credentials, operational state, source policy, known unit price and current quota. Source matching is **preview**, not authorization to dispatch.
3. Deterministic round-robin across primary hypotheses prevents a single hypothesis consuming all early task slots. Within each hypothesis, order by QV selected rank and original candidate UUID. No viral-score assumptions.
4. By default choose first compatible source by pinned ascending source ID. **Only explicit `source_routing="all_compatible"`** can generate two different physical tasks for the same query on two ready sources. Both remain held.
5. Physical dedup uses `(source_id, operation, mode, language, market, NFKC-casefold(text))` with immutable paging/result limits in the plan policy; one physical task carries *all* original selected IDs, candidate aliases, hypotheses and source fact IDs.
6. Limits: `max_tasks<=50`, `max_external_requests<=250`, `max_pages_per_task<=5`, `max_results_per_page<=50`, `max_total_candidates<=2500`, per-source quota, known full upper USD cost within `max_cost_usd<=10000`. **No silent truncation:** every unallocated selected query appears with concrete block reasons.
7. USD unit/upper estimates use `Decimal` at 8-digit precision, no floating point billing. A planning cap is NOT authorization for a paid provider request. Missing-price sources never become `$0`.
8. Stable `plan_hash=sha256(canonical(plan_without_hash))`, no synthetic UUID or timestamp inserted. Catalog captured/expiry timestamps are **pinned inputs** and changing them produces a legitimate new plan hash.
9. `no_work=True` for zero selected QV, with honest all-hypothesis source coverage, zero tasks and zero outbound requests.
10. All tasks and the plan itself have `external_dispatch_authorized=False`, `execution_enabled=False`, status `held_preview_no_dispatch`. **No Collector ACK or receipt is implied.**

## 4. Explicit owner-held approval (not provider execution)

```python
from intelligence.search_planner import SearchPlannerService
sp = SearchPlannerService()
preview = sp.preview_source_tasks(project_id, production_qv_id, limits=limits)
# preview NEVER spends or queues Collector work.
# commit_held_source_plan is DENIED by default (403) because no real owner verifier:
# sp.commit_held_source_plan(project_id, production_qv_id, idempotency_key, limits=limits)
```

At SP13, assemble a genuine authenticated local-owner session checker **server-side** and pass it to `TaskPlanner(owner_action_guard=trusted_owner_action_checker)`. `commit_held_source_plan()` regenerates the entire held plan, checks exact owner action `search_planner.plan.commit_held`, re-checks PostgreSQL project/QV/C9 pins and inserts immutable snapshot/approval atomically. `(project,idempotency_key)` and `(project,qv_id,plan_hash)` unique indexes prevent duplicate approvals and allow stable replay. Reusing the same key for a different plan raises a conflict.

Returned state: **`held_approved_not_dispatched`**, not a Collector execution receipt. SP11/12 must separately create and atomically ACK the authoritative QV→SP inbox record, and Collector must independently acknowledge any downstream delivery. This stage neither mutates `qv_outbox` nor calls providers.

## 5. QA instructions and known limitations

```bash
python -m unittest discover -s tests -p 'test_search_planner_stage10_task_planner.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage10_approval.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage09_capabilities.py' -v
python -m compileall -q intelligence/search_planner/task_planner
```

The existing GitHub-hosted runners were queued as of this coding session. **Do not mark QA PASS before a real run starts.** The tests above use internal trusted port doubles; fake DB transactions don't replace real PG17 concurrent approval/rollback, migration apply+upgrade, source-catalog TTL racing, Windows/Linux or real provider integration checks. Those stay in SP16. Do not silently claim production release or merge the Draft PR #34.

**Stage bookkeeping:** SP10 implementation [x] after code commit; tests separately QUEUED/NOT RUN. Actual Deep C9 V2 is an upstream dependency of SP02, not fabricated by this V1-compatible planner.
