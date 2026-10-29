# PIE-MOD-05 — Receiving-side UAE research handshake

**Date:** 2026-10-26. **PIE branch:** `feature/patterns-insights-full-module`. **Status:** implemented adapter + mocked adversarial tests, **real live UAE integration BLOCKED**. No peer branch or `main` modifications.

## Peer reality as observed

At stage start UAE `feature/unified-analytics-full-module` had HEAD `5ac834a9caacd15406eac26baaa6a00119eb465d` and finished UA-MOD-04 (5 UAE SQL tables under `080_unified_analytics_core.sql`), but not UA-MOD-25, the row-level PIE handoff provider. UAE `uae-pattern-handoff/v1` (an earlier descriptive/pointer proposal) **is NOT compatible** with PIE `uae-to-pie/v1` row-level datasets; emitting a summary count of 2000 never proves there are 2000 qualified canonical videos.

PIE's `PIEPorts().require('uae')` continues to return `capability_unavailable` without an explicit adapter. PIE does **not** load the old `intelligence.analytics` implementation, scrape Group A/B/C tables, calculate C7, or consume unverified historical snapshots.

## Implementation and trust boundaries

`intelligence/patterns_insights/intake/uae.py` supplies a **receiving-side** `UAEReadBridge`, which structurally implements `UAEQualifiedReader`. Default construction has no provider/secret/rights authority and is unavailable. An explicitly connected transport must provide:

1. `describe()` -> `UAEExportDescriptor` with exact `uae-to-pie/v1`, engine version and explicit row-level, qualified-only, current-rights and signed-receipt capabilities. All advertised flags are initially **untrusted metadata**.
2. `export_qualified_manifest(pointer, fresh_nonce)` -> typed `UAEExportEnvelope(raw_manifest, receipt)`, where the receipt binds project, source, policy ID, engine version, producer run ID, dataset/manifest hash, random nonce, issue/expiry time, issuer and key ID.
3. Independently configured `UAEReceiptVerifier` (provided opt-in `HMACReceiptVerifier` with 32+ byte **external** key and no default production key). Signature is checked by constant-time comparison; key provisioning/rotation is NOT implemented by this stage. A keyed MAC does not authenticate a producer if the verification secret is shared with an attacker. Unit-test key is non-production.
4. Independently injected `CurrentRightsAuthority.check_dataset(pointer, dataset)` -> typed current `UAERightsDecision` matching exact project/source/policy/manifest **and one-use nonce**, positive decision, time checked **after** export receipt issuance and **not after** request clock. A provider-supplied arbitrary `rights=true` field is not a rights authority. Production authority must later be backed by real DB/current D1 policy; no implementation exists in this stage.
5. Strict `load_dataset_json` checks versioned exact row/sample/lineage SHA, canonical `videos.id`, C score completeness and version-pinned features. An absent provider, stale receipt, wrong scope, mismatched project, unverified signature, expired metadata, rights revocation, unknown summary schema or malformed body all fail closed with typed `PIEBoundaryError`.

The bridge returns `UAEReadResult` with `dataset`, `issuer`, receipt fingerprint and rights-check timestamp; **`verified_live=False` and `ready_for_analysis=False` unconditionally**. The result must not be exposed as analysis-ready, published to a dashboard or passed to the LLM. This is intentional: Stage 06 must additionally verify *each* UAE row's latest source eligibility, retention, SRE LIVE, QDP, A/B/C evidence lineage and audit receipt against the actually deployed databases. Mock tests cannot establish trust in the external independent rights authority.

## QA and non-negotiable blockers

Run from PIE branch root:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights
```

**Local Python 3.13 observed:** 71 tests collected, **63 PASS, 8 skipped PG-only tests**; compileall PASS. New 19 UAE-mock tests cover inert imports/capability, wrong format/version, missing approval components, signed one-time nonce and replay, wrong key, forged receipt, expired/future receipts, malformed/descriptive payload, producer run mismatches, revocation immediately denying the next read, project/policy/manifest mismatch, stale rights and sanitized dependency failures. No live UAE data and no paid calls. PostgreSQL 17 CI still needs a confirmed completed run; latest prior PIE run #40319116442 was queued during inspection.

**Next stage:** PIE-MOD-06 provenance/eligibility; do not mark production handshake DONE until UAE implements and authenticates `uae-to-pie/v1` at UA-MOD-25, and integration tests prove current read gates using a disposable real UAE database.
