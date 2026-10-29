# Group B GB-MOD-02 — standalone architecture and dependency ports

**Branch:** `feature/group-b-full-module` · **Scope:** DI/contract declarations, NOT an executable analytics engine. No QDP/A/LLM/DB adapter is installed automatically. Existing `005_content_features_b.sql` and peer modules stay unchanged.

## Ownership and layering

| Runtime domain | Owned goal | Required declared ports | Implementation stage |
|---|---|---|---|
| intake | Durable QDP inbox, exact A lineage binding | qdp, storage, group_a | 04–05 |
| classify | semantic B1/B2/B3/B4/B6/B7/B8 | model, budget, media | 09, 13–20 |
| deterministic | B5 cuts, sampling | group_a, media | 12, 15 |
| validation | validate observations, evidence and schema before persistence | group_a, media | 21 |
| storage | B analysis versions, outbox, no double writes | storage | 03, 22 |
| review | human correction, immutable review history | review, storage | 23 |
| delivery | only validated B completion to Unified Analytics | analytics, storage | 24–25 |

Source of domain declarations: `intelligence/group_b/architecture.py`. Importing `intelligence.group_b` must work using baseline main without installing peer branches. `adapters/__init__.py` deliberately does not import or instantiate any peer.

## Proposed versioned interface map

`intelligence/group_b/ports.py` defines eight typed Protocols and opt-in `GroupBPorts`.

| Port | Expected contract | Interface |
|---|---|---|
| qdp | `qdp-handoff/v1` | `fetch_preparation`, `acknowledge` |
| group_a | `group-a-to-b/v1` | `fetch_snapshot` |
| media | `group-b-media/v1` (GB-owned) | `fetch_manifest`, `access_allowed` |
| model | `group-b-model/v1` (GB-owned) | `quote`, `invoke_approved` |
| budget | `group-b-budget/v1` (GB-owned) | `reserve`, `reconcile` |
| storage | `group-b-storage/v1` (GB-owned) | `commit_intake`, `save_analysis` |
| review | `group-b-review/v1` (GB-owned) | `submit_review` |
| analytics | `group-b-analytics/v1` (GB-owned) | `publish_completion` |

**Version labels and method shapes are local negotiation targets**, not evidence the QDP, Group A, or Analytics providers currently support them. Actual provider payloads remain untrusted until ownership-specific translator and compatibility tests. The Protocols deliberately return opaque mappings; **do not interpret them as vetted records**. They must be parsed/verified at their future stages.

All ports are `None` by default and **UNAVAILABLE**. Incompatible signatures or version produce **INCOMPATIBLE**, and a callable metadata-only probe returning the requested version produces **DECLARED**, which is *not* `ready` or `completed`. A peer exception is reduced to safe `probe_failed`, without echoing media, keys or response bodies. `require(name)` raises a typed error rather than silently using a fallback. This phase does not verify ownership, DB or actual provider reachability.

The declared `capability()` method must be local, deterministic, free of network/cost/side effects. `GroupBPorts.status/report/require` must **never call** `fetch_preparation`, `acknowledge`, `invoke_approved` or persistence methods. A future runtime must independently inspect current upstream payload version, lineage, permissions and media coverage. No confidence estimate creates a missing A observation.

## Operational defaults

`GroupBConfig()` defaults to `enable_paid_execution=False` and zero USD ceiling, strictly typed/finite numeric quotas, no secrets or credentials. Even a config explicitly enabling the switch cannot authorize a paid model call: `require_paid_approval()` rejects until GB-MOD-10 implements signed/reserved per-attempt approval and ledger. No direct HTTP handler is allowed to execute the model.

All domain work remains disabled for now: no QDP ack, no A/media download, no LLM, no DB modifications, no reviews and no downstream completion. This is intentional fail-closed plumbing.

## QA / evidence

- `tests/test_group_b_ports.py`: 8 pure tests covering broken adapter inspection, missing ports, declared-not-ready, wrong versions/methods, leaked exception prevention, paid call denial, domain partition and subprocess import without QDP/A/Analytics.
- `python -m unittest discover -s tests -p 'test_group_b_ports.py' -v`
- Cross-platform CI adds same suite to `.github/workflows/group-b-module.yml`.
- No production PG17, real provider or real peer adapter claims at this stage. CI passing pure tests will not satisfy stages 03–29.

## Next work

GB-MOD-03 will add Group-B-owned runtime migrations only, preserving legacy `005_content_features_b.sql` checksum and respecting sorted migration order. Later GB-MOD-04/05/25 will negotiate actual peer versions, verify IDs and source hashes, and test durable handoff; their absence must remain reported as unavailable.
