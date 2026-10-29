# UAE + PIE integration pipeline (staging, v1)

**Branch:** `integration/uae-pie-pipeline`  
**Source branches:** `feature/unified-analytics-full-module` and `feature/patterns-insights-full-module`  
**Base:** current `main` at `e647060d69c00a5988773ca335564c47cd397103`.  
**Purpose:** integrate both independently written packages and their migrations, UI/API mounts, signed row-level research boundary, and fail-closed admission checks **without merging into main yet**.

## Runtime data flow

1. Upstream SRE/QDP/Group A/Group B/Group C and source-policy authorities generate authoritative current records. UAE handles qualification, immutable snapshots and persisted C score lookup; it does not compute C scores.
2. UAE's `ProjectRankedRunStore` stores a reproducible authorized ranked run. An injected trusted selector resolves that run and its *exact* current `VisibilityPins` tuple. Its implementation is deployment-specific and **must** recheck project access and never trust a client-selected rowset.
3. `PIEResearchExportReader` checks all selected pins again in a single fresh PostgreSQL REPEATABLE READ transaction, then emits `uae-to-pie/v1` sorted row-level observations, a dataset SHA256, a manifest SHA256, and a nonce/expiry-bound HMAC-SHA256 receipt. Zero/denied/non-current inputs return no dataset, never an incomplete subset.
4. `UAEPIETransport` maps the actual UAE receipt onto PIE's `UAEExportReceipt` and `UAEExportEnvelope` **without rewriting signed fields**. `prepare_pointer()` creates an untrusted, immutable `PIEDatasetPointer` and runs PIE's real JSON/schema/hash parser.
5. `UAEReadBridge` re-exports the same run with a fresh 256-bit challenge, verifies the independently configured trusted HMAC key, checks immutable pointer identity, scope, expiry and the independent `CurrentRightsAuthority`.
6. `SourceEligibilityGate` independently checks current project policy, retention, SRE LIVE, QDP, A/B/C, snapshots and every source-row fingerprint via `ProvenanceAuthority`. Any denied scope, missing pin, changed row or expired permission suppresses the entire dataset.
7. `UAEPIEPipeline.admit(pointer)` returns redacted `UAEPIEAdmission` (sample count, blocker codes, candidate eligibility). It **does not** automatically run a statistical test, invoke an LLM, release a Pattern or publish a result.

### Wire repeatability fix

Old UAE used the database wall clock as the signed manifest's `scope.as_of`, so preparing a pointer and re-exporting with a new HMAC nonce changed the manifest hash every time. The integrated producer now pins `as_of` to the maximum source `observed_at` among the exact frozen row set. Issued/expiry times remain **fresh** per request and live source authorization is still repeated. If any row/feature/policy/version changes, the re-export either fails current eligibility or changes the digest; PIE rejects the prior pointer rather than silently refreshing its cohort.

### Deployment composition (explicit wiring, not a default app secret)

```python
from intelligence.unified_analytics.pie_research_handoff import (
    PIEResearchExportReader, ReceiptSigner
)
from intelligence.patterns_insights.intake.uae import HMACReceiptVerifier
from intelligence.uae_pie_pipeline import UAEPIETransport, UAEPIEPipeline

# The host must independently provision:
#   db: fresh transactional PG17 connection
#   select_current_run(pointer): trusted (run_id, tuple[VisibilityPins, ...])
#   signer_secret and key_id: external rotated credentials, never hardcoded
#   rights: independent CurrentRightsAuthority, current per request
#   provenance: independent ProvenanceAuthority, current scope + each row
signer = ReceiptSigner(signer_secret, key_id=key_id)
reader = PIEResearchExportReader(db, signer)
transport = UAEPIETransport(reader, select_current_run, issuer=signer.issuer)
receiver_keys = HMACReceiptVerifier({key_id: verifier_secret})
pipeline = UAEPIEPipeline(
    transport, verifier=receiver_keys,
    rights=rights, provenance=provenance,
)
pointer = pipeline.prepare_pointer()
admission = pipeline.admit(pointer)
# Never publish or run paid analytics on candidate_eligible alone.
assert admission.ready_for_analysis is False
```

There is **no enabled automatic producer** and no magic fallback connection: by design neither the app nor CI receives production DB credentials or signing secrets. The host must provide a real verified selector, independently trusted key material, current-rights and row-provenance authorities. Never mount raw research-manifest exports in public HTTP.

## Merged surfaces

- Packages: `intelligence/unified_analytics/` and `intelligence/patterns_insights/`; composition root `intelligence/uae_pie_pipeline.py`.
- Additive forward-only SQL: `080..090_unified_analytics_*.sql` and `200..207_pie_*.sql`; do not rename/copy old 016..018 migrations.
- Both packages' docs, fixtures, independent test suites, optional cross-package UAE tests, and node tests.
- `app.py`: preserve existing main RTB/Search Planner and project sources routes; mount both UAE and PIE GET/POST adapters; expose scoped static files.
- `site/index.html` / `site/projects.js`: preserve main UI; add UAE Studio and PIE Explorer entry.

## Test commands on developer machine

```bash
python -m compileall -q app.py intelligence/unified_analytics intelligence/patterns_insights intelligence/uae_pie_pipeline.py
python -m unittest discover -s tests -p 'test_uae_pie_pipeline.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_pie_peer_contract_optional.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
python -m unittest discover -s tests -p 'test_pie_*.py' -v
# PostgreSQL17: use only an isolated disposable database and CM_TEST_PG_DSN
```

The new cross-package tests use the existing synthetic UAE fake DB and the *actual* UAE HMAC signer, PIE JSON validator, signed receiver and independent provenance interfaces. They cover deterministic second reads, source-rights denial, row revocation, bad receipt keys, row drift, and missing authority. **Authored, not executed in this remote connector environment.** Do not report PASS until actually run.

## Real release blockers — do not confuse code merge with production verification

- Current UAE qualification/source-policy state intentionally has **no real positive qualified snapshots**, so a real end-to-end research sample cannot presently be generated from upstream SRE/QDP/A/B/C.
- PIE's `UAEReadResult` and `EligibilityReport` intentionally return `verified_live=False` and `ready_for_analysis=False`, including with passing synthetic checks. Do not bypass these flags or pass samples into inference/publication without a separate, independently verified production approval design.
- The actual trusted current-run selector, per-project authentication, rights and provenance authorities, key provisioning/rotation and revocation propagation need deployment-specific wiring and a disposable PG17 E2E test with genuine approved producer lineage. This integration intentionally does not invent credentials or grant flags.
- PIE Explorer HTTP is read-only and remains fail-closed until an independently authenticated `ExplorerPorts` runtime is installed.
- Cross-module migrations and startup must be exercised against the **actual** production base and upstream module schemas, not just imported code.
- No CI workflow result, local Python test result, release certification or main-merge completion is claimed here.

## Merge policy

Review this branch as a single integrated change set against `main`. Run smoke, security, DB17 and rights-revocation tests locally; resolve any new upstream/main drift before merge. Merge **the combined integration branch**, not both old standalone branches independently, to avoid reinstating old app.py/site files and losing currently connected routes.
