# Content Machine / SP-09 — Tool Capabilities Port & Matching

**Date:** 2026-10-26. **Branch:** `feature/search-planner-full-integration`.  
**Code:** IMPLEMENTED. **QA:** tests committed, GitHub Actions runner has not confirmed execution on final SHA. This stage does **not** build or copy the Collector Registry and does **not** issue provider requests.

## Actual upstream evidence used (read-only)

Collector PR #20 branch `feature/video-collector-dev-20261023`, SHA `a4f658de77fb82de2437474e60c2b917ae2a9811`:
- `intelligence/collector/registry.py`: `REGISTRY_VERSION=collector-source-registry/v2-dtk-installed`. `dtk_tiktok` declares **keyword → search_posts**; no hashtag operation. `apify_tiktok`, `youtube_official`, `instagram_official` have no executable declared operations. All four have `operationally_verified=False`, `source_policy_verified=False`, no live quote; declarations are **not** proof of readiness.
- `intelligence/collector/contracts.py`: source descriptor has `source_id,platform,integration_state,capabilities,operationally_verified,source_policy_verified,pricing_status`; no true source-language, market, credential, item-level price or current quota attestation.
- `intelligence/collector/planner.py`: offline preview independently warns about source verification; a keyword `search_posts` query has a 120-character limit, and pagination beyond one page is not verified.
- `intelligence/collector/policy_gate.py`: a real DB lookup is required for enabled policy and quota, but it is **not** wired as a trusted external source-capabilities publisher for SP. No live Collector acceptance contract is inferred.

**Decision:** the SP09 module consumes **server-trusted, versioned, read-only snapshots** through `CollectorCapabilitiesPort`. It never imports `intelligence.collector`, invokes Collector registry/worker, creates another source list or turns unverified declarations into an executable plan. Source readiness cannot be inferred from `operation_for`.

## Files created and changed

| File | Function |
|---|---|
| `intelligence/search_planner/capabilities/__init__.py` | Module exports, standalone imports |
| `intelligence/search_planner/capabilities/service.py` | `CapabilityMatcher`, `validate_capability_snapshot`, `match_capabilities`, `project_collector_declarations`, `unavailable_snapshot` |
| `intelligence/search_planner/service.py` | Read-only `source_capabilities(project_id)` and `match_query_capabilities(project_id, query)` |
| `intelligence/search_planner/api_contracts.py` | Public status advertises both read operations; `capability_port_configured` is **not** `external_search_ready` |
| `intelligence/search_planner/__init__.py` | Exports `CapabilityMatcher` and snapshot validator |
| `tests/test_search_planner_stage09_capabilities.py` | Offline contract/unsafe input/locale/market/cost/expired/unavailable/no-Collector regressions |
| `.github/workflows/sp-stage01-fast.yml` | SP-01–SP-09 focused QA |

No SQL migrations, third-party installation, background worker or app startup side effects.

## Exact versioned schema

`search-planner-source-capabilities/v1`:
- top-level exact fields `schema_version, project_id, registry_version, captured_at, expires_at, sources`.
- timezone-aware observed/expiry timestamps, at most 24-hour expiry horizon, never future-dated; **expired catalog blocks every match**.
- each source has strict `source_id, platform, integration_state, enabled, operationally_verified, source_policy_verified, credentials_verified, pricing_status, cost_usd_per_request, quota_available_requests, capabilities`.
- `pricing_status` is `unknown`, `verified_free`, or `verified_paid`. Unknown costs remain `null`, **never converted to USD 0**. Confirmed prices must be finite non-negative 8-decimal-or-less strings; paid >0, free =0.
- per-mode operation includes `mode, operation, languages, markets, max_query_length, max_pages, pagination_verified`. Only explicit language and market scopes or exclusive `*` wildcard are accepted for an otherwise ready source. Unknown/missing bounds fail closed.
- descriptor limits: at most 64 source IDs, 16 operations per source, 120 characters per SP query and explicit locale/market. Duplicate source/mode IDs and malformed dictionary fields raise `PipelineError`.

`search-planner-capability-match/v1`:
- per-source distinction between `capability_declared`, `operationally_ready` (enabled, credentials & adapter verified), `policy_and_budget_verified` (DB/owner source policy, current quoted price & quota), and **`planning_compatible`** (all constraints pass, fresh catalog).
- deterministic `block_reasons`: missing provider, unsupported mode, text length, unverified language/market, incomplete credentials, disabled adapter, unverified source policy, missing price, unknown/exhausted quota, expired snapshot or unknown pagination.
- pinned `catalog_sha256` of the exact producer document. **Fingerprint does not authenticate its publisher**, so the server must assemble/trust the port; never accept arbitrary browser-submitted snapshot as a source of executable authority.
- `external_dispatch_authorized=False` and `provider_calls_in_this_match=0` on all previews, including hypothetical fully compatible catalog. SP10 can plan against this; SP12 + Collector must independently authorize actual execution.

## Standalone/no Collector behavior

```python
from intelligence.search_planner import SearchPlannerService

sp = SearchPlannerService()
sp.source_capabilities("a"*32)["sources"]  # []
sp.match_query_capabilities("a"*32, {
    "query_mode":"keyword", "text":"домашние упражнения",
    "language":"ru", "market":"BY",
})["compatible_source_ids"]  # []
```

If an internal backend later installs a genuine server-owned `CollectorCapabilitiesPort`, assemble `CapabilityMatcher(port)` and inject it in `SearchPlannerService(capability_matcher=...)`. The port is a trusted server dependency, not user JSON. SP09 does not implement a concrete Collector adapter because that branch is unmerged and cannot supply operationally trusted price/credential/locale/market/quota evidence today.

### Safe mapping from old Collector declarations

`project_collector_declarations(pid, registry_version=..., descriptors=collector_registry_list, observed_at=clock)` is a **one-way read-only compatibility translator**. It preserves declared operations, but sets `enabled/operationally_verified/source_policy_verified/credentials_verified=False`, pricing=`unknown`, quota=`null` and language/market scopes empty. All matches remain blocked. Those missing fields must come later from an **actual trusted deployment policy/health/quota adapter**, never invented based on the old source description.

## Required quality gates

```bash
python -m unittest discover -s tests -p 'test_search_planner_stage09_capabilities.py' -v
python -m unittest discover -s tests -v
```

- Unit tests exercise fake fully verified port behavior for **contract logic only**; they do not assert any actual source is ready. No API calls, paid spend, or Collector imports.
- GitHub Actions `Search Planner SP-01 to SP-09 focused QA` + Linux + Windows must complete on a specific branch HEAD. Until then mark tests `QUEUED/NOT RUN`, not PASS.
- SP10 will consume only server DB-verified QV candidates/lineage with this matcher and deterministic plan limits. SP11/12 handles true QV projection and durable Collector handoff.
- SP15/16 must supply a real trusted capability publisher with expiring policy/price/quota evidence, mock-Collector integration E2E, and Windows/Linux release tests on the final SHA.

**User bookkeeping rule:** mark SP-09 **[x] ~~code implemented~~** while preserving separate unpassed QA gates. Do not mark Collector operationally ready; do not merge PR #34 without explicit owner approval.
