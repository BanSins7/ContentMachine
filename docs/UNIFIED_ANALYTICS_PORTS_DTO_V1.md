# UA-MOD-02 — Standalone Ports, DTO and Capability Boundaries

**Date:** 2026-10-26. **Implementation owner:** `intelligence/unified_analytics/`.
**Status:** UAE-owned Python contracts and mock-only tests implemented. **No LIVE peer adapter/qualification/persistence or database migration is implemented in this stage.**
**Branch:** `feature/unified-analytics-full-module` only; `main`, legacy PR #23 and all peers READ ONLY.
**Reference:** [UA-MOD-01 ownership audit](UA_LEGACY_PORT_MATRIX.md), [full plan](UNIFIED_ANALYTICS_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md).

## Why this boundary exists

UAE must join only source-backed, current, version-pinned SRE relevance, QDP membership, A measured observations, B reviewed analysis, and C persisted score. It does not own any of these underlying calculations. A successfully constructed pointer, SHA-256-shaped value, valid wire schema, capability report or mocked reader response is **not evidence** of completed work, producer authenticity, approved source rights or a qualified publication.

`UAEPorts()` deliberately yields `unavailable/not_connected` for **every** dependency. No peer module is imported from UAE. Caller adapters are injected explicitly. A port claiming `declared` is merely ready for a structural read; its `qualified` flag is **always false**. UA-MOD-06–13 must verify actual reads, source ownership, human review, policies and pinned versions before any qualification state can become true.

## Files and concrete public surfaces

| File | Responsibility |
|---|---|
| `intelligence/unified_analytics/contracts.py` | Existing bootstrap `UnifiedAnalyticsInputPointer`, canonical hash and content kind mapping (unchanged) |
| `intelligence/unified_analytics/dto.py` | Immutable `InputScope`, `EvidenceRef`, `EvidenceRead`, `ReadState`, `PatternsHandoffPointer` |
| `intelligence/unified_analytics/ports.py` | `SREReader`, `QDPReader`, `GroupAReader`, `GroupBReader`, `GroupCReader`, `D1Reader`, `SourcePolicyReader`, `PatternsPublisher` Protocols; `PortCapability`, `PortStatus`, `UAEPorts` |
| `intelligence/unified_analytics/configuration.py` | Explicit read limits, paid/network/polling disabled; no environment auto-activation |
| `intelligence/unified_analytics/__init__.py` | Importable top-level API on `main` with stdlib only; **no** `compute_c_metrics` export |
| `tests/test_unified_analytics_ports.py` | Mock success/unavailable/incompatible/wrong version, wrong scope, no fake result, syntax and configuration regressions |

## Exact versions: peer facts versus UAE adapter revisions

| UAE port | UAE adapter interface (new) | Observed peer wire | Checked source / meaning |
|---|---|---|---|
| `sre` | `uae-sre-reader/v1` | `sre.classifications_ready/v1` | [SRE outbound](https://github.com/BanSins7/ContentMachine/blob/25f4465ff492bf74f1a64e3ad980b52c83c835b4/intelligence/relevance/ports/outbound.py): `execution_mode=live`, run completed, full leaf coverage, digest, approved/human-effective decision still needs DB verification |
| `qdp` | `uae-qdp-reader/v1` | `qdp-handoff/v1` | [QDP integration contracts](https://github.com/BanSins7/ContentMachine/blob/0ed73d76df65cffcc7b16ea965da417f3baed5fc/intelligence/qualified_data/integrations/contracts.py): canonical event/run UUID, group A/B/C, project/video, `input_sha256`; an ACK is **not** a completed analysis or qualified result |
| `group_a` | `uae-group-a-reader/v1` | `group-a-to-b/v1` | [Group A contracts](https://github.com/BanSins7/ContentMachine/blob/381fc721814241989d97a20bd0f8bb1502371719/intelligence/group_a/contracts_v1.py): exact A FK, stream truth/coverage, QDP input SHA and media manifest |
| `group_b` | `uae-group-b-reader/v1` | **not yet frozen for UAE output** | [Group B contracts](https://github.com/BanSins7/ContentMachine/blob/3415c87242bb568ebd732a388bdeed0220c2c848/intelligence/group_b/contracts.py): `group-b-input/v1` is an *input* pointer, not authorization to invent a completed output version. UAE requires exact B parent FK to A, B1–B8/needs_review |
| `group_c` | `uae-group-c-reader/v1` | **not yet frozen for UAE output** | [Group C contracts](https://github.com/BanSins7/ContentMachine/blob/39512bf236ddb527172eecdb5c5896a3643f25f0/intelligence/group_c/contracts.py): `group-c-input/v1` is an *input*, not a completed-score output. UAE reads persisted `calculated_metrics` only |
| `d1` | `uae-d1-reader/v1` | to be verified | `videos.id`, project membership, source identifiers and original media/metadata/metric versions |
| `source_policy` | `uae-source-policy-reader/v1` | to be verified | Current reviewed source rights and retention/expiry at write **and read** time |
| `patterns` | `uae-pattern-publisher/v1` | to be verified | Future Patterns consumes a pointer; no network publisher or derived conclusion now |

**Caution:** the peer SHAs above are audit observations; peers can advance. UAE never imports these modules. `KNOWN_PEER_WIRES` explicitly uses `None` for unverified peer outputs rather than inventing names. Metadata negotiation of an adapter for B/C does **not** assert actual downstream compatibility; those handshakes are deferred until UA-MOD-09/10.

## DTO invariants

- `InputScope`: project = exactly 32 lower-case hex; `videos.id` and policy ID positive integers (not booleans); source ID bounded ASCII; content type strictly video/slideshow_video/photo_carousel/mixed_carousel; QDP run canonical hyphenated lowercase UUID; QDP input exact lowercase 64-hex SHA. `image_post` remains unsupported without a B2 contract.
- `InputScope.from_pointer`: maps only known bootstrap `UnifiedAnalyticsInputPointer` fields; does **not** dereference it or certify any row. `scope_sha256` pins *all* scope fields.
- `EvidenceRef`: exact scope, port, bounded opaque record key, producer schema/version and a 64-hex payload digest. `lineage_sha256` deterministically commits to the entire reference including `scope`. Changing project/video/source/policy/QDP run/QDP input, version, record or payload digest changes the lineage hash.
- SHA-256 here is a tamper-evident **binding digest**, not a digital signature or proof that a remote producer actually created the record. There is **no key management or authenticated signature validation** at this stage; future adapters must validate current DB authority and, if needed, signed event transport separately.
- `EvidenceRead`: `located` requires a reference to exactly the requested scope+port and forbids a failure code; `missing` and `unavailable` require a typed reason and forbid any evidence reference. `located` is an unverified *claim* and is never synonym for `qualified`.
- `PatternsHandoffPointer`: immutable identity and content digests only. No Patterns/Insights logic nor publisher side-effect.
- `UAEConfiguration`: strict typed bounds for local reads and pagination; automatic polling, external provider requests and unknown fields are denied. This is not a rights grant.

## Runtime capability semantics

```text
UAEPorts() --> every port unavailable/not_connected
     |
injected adapter (explicit only)
     |
Protocol methods present? ---- no --> incompatible/missing_protocol_methods
     |
capability probe throws? ----- yes --> unavailable/probe_failed (no leaked exception)
     |
declared adapter schema = uae-*-reader/v1? -- no --> incompatible
     |
known peer wire matches? ---- no --> incompatible
     |
declared: structural read MAY be attempted, qualified is ALWAYS false
     |
read result: typed EvidenceRead for same port+scope and known peer schema
     |
located (UNVERIFIED) / missing / unavailable
     |
UA-MOD-06..13 real source-backed verification needed before qualified
```

`UAEPorts.read(port, scope)` invokes one injected adapter after negotiation, checks typed result/scope/schema and reports failures with sanitized error codes. It does not calculate C, grant source rights, accept unsigned events as authoritative or persist evidence. A mock-adapter regression demonstrates structural compatibility only.

## QA and future handshake

Expected no-provider test command:
```bash
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
python -m compileall -q intelligence/unified_analytics
```

**QA truth:** tests exist; their execution outcome and Windows/Linux CI must be reported from actual runs. PostgreSQL 17, real peer/SRE LIVE handoff and full E2E are **NOT RUN / NOT VERIFIED** at stage 02, by design. A source audit and mock-only tests cannot establish production coverage.

**UA-MOD-03:** introduce a pure qualification gate, preserving typed blockers without treating `located` as complete. **UA-MOD-04:** own forward-only additive migrations after collision-free peer ordering; no reuse of legacy 016/017/018 or Group C approval constraints.
