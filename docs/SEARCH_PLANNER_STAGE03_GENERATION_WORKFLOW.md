# ContentMachine — Search Planner SP-03: Run → Quote → Consent

**Date:** 2026-10-26. **Branch:** `feature/search-planner-full-integration`.  
**Status:** Implementation committed; exact HEAD focused/full Linux+Windows CI still pending at time of authoring. No live OpenRouter calls or provider payments made.

## Implemented code

| File | What it does |
|---|---|
| `intelligence/search_planner/generation_workflow.py` | Injectable **GenerationWorkflow** facade, reuses existing PostgreSQL transactional SQG generation and budget functions. Strict project/publication/config/quote IDs, idempotency key length, independent owner-auth gate. |
| `intelligence/search_planner/service.py` | Exposes new `SearchPlannerService` methods for register/plan/quote/budget/authorize/revoke, without an HTTP route or provider dispatch. |
| `intelligence/search_planner/__init__.py` | Exports `GenerationWorkflow` and `OwnerActionGuard` without affecting existing imports. |
| `tests/test_search_planner_generation_workflow_stage03.py` | 16 offline regression methods for zero-spend plan/quote, explicit consent, deny-by-default gate, idempotent operations, identity checks, bounded USD, independently gated revoke, no Collector/provider imports. |
| `.github/workflows/sp-stage01-fast.yml` | Adds SP-03 and existing legacy cost/config regressions to focused CI. |

No new PostgreSQL migration. The authoritative existing tables are `sqg_config_versions`, `sqg_generations`, `sqg_contexts`, `sqg_batches`, `sqg_quotes`, `sqg_consents`, `sqg_events`. Financial quote/consent ledger code already existed in `generation_engine/budget_manager.py` and run code in `generation_engine/run_manager.py`; not duplicated.

## Read/zero-provider registration

```python
from intelligence.search_planner import SearchPlannerService, GenerationWorkflow

service = SearchPlannerService()
config_record = service.register_generation_config(project_id, strict_generation_config)
run = service.plan_generation(project_id, published_c9_id, config_record["config_id"], idempotency_key)
quote = service.quote_generation(project_id, run["generation_id"])
budget = service.generation_budget(project_id, run["generation_id"])
```

Registering config, creating a run and making a quote do not grant spending permission or execute OpenRouter. Run is `awaiting_authorization`; the existing backend runs PostgreSQL-scoped immutable snapshot checks and stores contexts/batches atomically. Calling `quote_generation` persists an indicative conservative USD quote, checks the verified priced model and immutable batch manifest, reuses still valid matching quotes. Actual provider request and exact actual cost remain SP-04.

## Authorization must be separate owner-approved action

```python
# A server-controlled authorization gate must be constructed by SP-13 HTTP
# from a verified authenticated owner session, never from request JSON.
verified = GenerationWorkflow(
    owner_action_guard=lambda action, pid, object_id: server_verify_action(
        action, pid, object_id,
    ),
)
service = SearchPlannerService(generation_workflow=verified)
answer = service.authorize_generation(project_id, generation_id, quote_id, "1.25000000")
# Existing SQG ledger verifies current pinned quote, price, model capabilities,
# amount >= high estimate, and creates explicit immutable independent consent.
```

**Security limitation:** a Python callback is an internal trusted assembly port, not proof of authorization by itself. The local user HTTP layer in SP-13 MUST wire a genuine session/owner action verification and must not pass through a caller-defined callback or `approved=True` JSON. In SP-03, default `SearchPlannerService()` explicitly rejects `authorize_generation()` and `revoke_generation_consent()` with 403. No paid provider dispatch, batch worker or HTTP endpoint is wired here.

Guard semantics: verify `search_planner.generation.authorize` or independently `search_planner.generation.revoke`, exact project and object UUID; must return Python `True` exactly (not truthy 1/string). Failure blocks before ledger mutation; exceptions redacted. Repeat authorization with same quote/amount is idempotent via existing PostgreSQL ledger; conflicting approval rejected there. Revocation pauses authorized work using existing backend.

## Hard invariants and non-goals

- No Collector package requirement, no new registry, no provider HTTP in workflow.
- `main` and the Collector/C Corrector feature branches remain untouched.
- V1 approved C9 remains valid while real Deep C9 V2 is pending.
- Run and quote remain stored even before consent, clearly priced, project-scoped.
- Financial approval is completely distinct from C Corrector spending and Collector external execution consent.
- Budget/consent views do not trigger worker dispatch and are never interpreted as a provider result.
- Generation config registration is a new immutable version, not automatically idempotent by config body. **Run** idempotency is guaranteed by existing `(project_id,idempotency_key)` unique key.
- SP-03 tests are unit-level and mocked DB function ports. PostgreSQL17 real concurrent cases and *final* full Linux/Windows workflows are mandatory release gates (SP-16), not claimed here.

## Verification

```bash
python -m unittest discover -s tests -p 'test_search_planner_generation_workflow_stage03.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_cost.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_contracts.py' -v
python -m unittest discover -s tests -v  # disposable PostgreSQL 17 via CI
```

Use GitHub Actions run at exact branch HEAD for PASS/FAIL. Do not inherit baseline `main` PASS. A prior Deep V2 dependency from C Corrector PR #31 is still open, but SP-03 V1 generation workflow can be developed independently without faking an approved V2 publication. SP-04 next adds supervised worker/recovery only after QA and reviews.
