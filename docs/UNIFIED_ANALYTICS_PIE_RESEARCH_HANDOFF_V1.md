# UA-MOD-25 — UAE→PIE research handoff, signed row-level manifest (v1)

**UAE branch:** `feature/unified-analytics-full-module` · **PIE peer inspected read-only:** `feature/patterns-insights-full-module` HEAD `53cd63bdd13c8261620568026b0afa759b445611` on 2026-10-28.

**Implementation:** `intelligence/unified_analytics/pie_research_handoff.py`. No PIE/SRE/QDP/Group A/B/C/RTB files changed, no new SQL migration, no HTTP/Studio/publisher or scheduled side effects. This is an **internal producer-side capability**, not a live PIE integration or qualified release.

## Exact consumer wire: what was actually inspected

PIE `intelligence/patterns_insights/dataset_contract.py` expects **`uae-to-pie/v1`** with *exactly*:

- `schema_version`, `producer` (`engine_version`, `run_id`, `dataset_build_version`);
- `scope` (`project_id`, `source_id`, `source_policy_version_id`, `calculation_config_id`, `source_metric_definition`, `source_metric_version`, `as_of`);
- `versions` (`group_a_version`, `group_b_version`, `group_b_taxonomy_version`, `sre_taxonomy_version`, `creator_cohort_version`, `prior_cohort_version`, `niche_cohort_version`);
- `selection` (`rule_version`, `population_sha256`, `scanned_count`, `included_count`, `excluded_count`, `exclusions`);
- `observations`: **1..50** sorted, unique rows (UAE enforces min **5**), with exact UAE snapshot, project/video/source/policy/QDP/SRE/A/B/C identities, timestamped publication/metric provenance, `creator_key`, platform/kind, `features_a`, `features_b`, `features_sha256`, producer/metric digests, and persisted `breakout_score` (finite explicit Decimal string).

Every row is based on an **already current UA19-qualified** `VisibilityPins` and exactly the stored UA24 `ProjectRankedRunStore` run UUID/digests. Source values are obtained **in the same fresh PostgreSQL REPEATABLE READ transaction** using UA20 `QUALIFIED_METRIC_SQL`, UA22 `AUTHOR_TIME_SQL`, exact `A_VIDEO_SQL` or `A_CAROUSEL_SQL`, UA21 `GROUP_B_DIMENSIONS_SQL`, and exact UAE immutable `uae_snapshot_payloads.payload_sha256`. We check all members with `VisibleSnapshotReader._read` **before** reading any Group C metric, row features, creator facts, or saved run manifest. The stored UA24 manifest is NOT a read grant: its exact input joins, version, stats SHA and full ranking digest are revalidated against current source state.

Feature extraction never calls an LLM or reclassifies media. Measured A features (video scene count/subtitle state or carousel slide count/audio boolean) come from the exact completed A analysis; B1 hook mechanisms, B2 format, B3 story roles, B5 editing, B6 subtitle type, B7 CTA goals and B8 topic identity come from the exact completed current B run. Unknown or not-applicable B dimensions remain absent/empty/None; not silently imputed as `none` (B7 completed empty may explicitly be `none_observed`). C score is **not recomputed, normalized or clipped**.

### Qualified selection vs full-project exclusions

PIE's `selection` contract requires `scanned_count = included_count + excluded_count` and explicit excluded rows. Our `rule_version='explicit-current-qualified-pins/v1'` defines the population as **only the bounded server-selected currently qualified input set**. Thus `scanned_count=included_count=N`, `excluded_count=0`, `exclusions=[]` *only in this narrow pinned universe*. This **does not claim** zero missing/denied/excluded videos in the full Content Machine project. A source-right-denied member suppresses the **entire export**; never include hidden video IDs or reasons in a signed manifest as diagnostic leakage. The precise selected (video_id, UAE snapshot_id) tuple is bound by `population_sha256`. Future full population scanning/exclusion rules require separately approved project-level safe analytics and non-leaking methods.

### Signatures are not SHA-256 digests

`ReceiptSigner(secret,key_id,issuer)` accepts a **required explicit 32+ byte external key**; no default/app-wide key and no generated `signature=canonical_hash(data)`. It signs PIE's exact ordered *receipt fields* with **HMAC-SHA256** over canonical UTF-8 JSON, matching PIE's read-only `HMACReceiptVerifier`. Every receipt binds project, source, source-policy version, engine version, run UUID, `manifest_sha256`, `dataset_sha256`, explicit verifier-provided 64-hex **challenge**, `issued_at`, `expires_at` (at most **300 seconds**) plus issuer/key ID. `SignedResearchExport` returns `raw_manifest` bytes and typed `SignedResearchReceipt`. A plain content checksum, A/B result SHA or `VerifiedSnapshot` identity **is not a digital signature**. The exporter does not store secret in GitHub, migration, schema or manifest.

PIE's `intake/uae.py` expects its **own** `UAEExportEnvelope`, `UAEExportReceipt`, a transport implementing `describe`/`export_qualified_manifest(pointer,challenge)`, an **independently configured verifier trust root**, and a separate `CurrentRightsAuthority`. PIE-MOD-06 also requires an independent `ProvenanceAuthority` to recheck every row and source. UAE's neutral dataclasses are not automatically that PIE type; a small integration adapter wrapping the receipt into PIE-owned types, project authorization, trusted key management/rotation and source/row provenance **must be built and independently QA'd on integration**. We have **not** edited the PIE branch. Neither side advertises `verified_live=True` or `ready_for_analysis=True` merely because the HMAC validates.

PIE also requires source-backed individual values. The exporter provides **source-backed descriptive SHA-256 digests** for metric snapshots and calculated score facts derived from their pinned IDs/values/timestamps; they are **NOT independently authenticated producer signatures**. PIE's actual provenance verifier must compare against the authoritative records, not accept those digests alone. Group A's `result_sha256` comes from its verified result; B's aggregate digest binds the current B run, analysis ID and all eight exact block evidence SHA claims. Again, non-signature hashes alone do not prove peers' approval.

### Versioned descriptive aggregate/dimension evidence without inventing insights

The transport contains an additional internal `ResearchAnalyticsEvidence` sidecar, **outside the strict PIE v1 JSON wire**. It binds `manifest_sha256`, the current validated UA24 `stats_sha256`, authorized N, UA20 Decimal Breakout Score and views `MetricStatistics`, plus eight canonical UA21 descriptive dimensions (`content_kind`, B1, B2, B3, B5, B6, B7, B8) and each eligible label's N and persisted-score `MetricStatistics`. A video with repeated hooks/roles/CTA appears at most **once per label**; one video may appear in multiple different labels. Every label with fewer than five distinct videos is completely omitted, including its identity and count. All output is sorted deterministically; no cross-source/platform or mixed C/metric versions.

The sidecar **is not separately included in PIE's receipt signed fields**; it must be independently reproduced or checked against the signed source manifest and UA24 run digest before consumption, not treated as an additional authenticated claim. The signed row-level dataset remains the authoritative PIE research input. UAE **does not discover patterns, do significance testing, generate insights, infer causal relationships, or publish**. PIE owns research/interpretation/human-review and must independently apply the UA23 uncertainty caveats.

### Production availability and blockers

The current UAE UA13/UA84/UA85 state intentionally prevents effective `qualified` positive snapshots. Consequently no real positive signed PIE dataset or run can be produced today. The signed transport is **inert by default**: no signer => `pie_export_capability_unavailable`; all current rights, QDP/SRE/A/B/C/C frozen-cohort conditions must pass. Existing UA24 run creation likewise blocks real positives. The independent PIE module exists only in a separate branch. Even after code merge it requires concrete project policy authority, independent signed receipt verification, row-level provenance enforcement and a secure trusted signing key before use.

RTB is still **`unavailable_not_bound`** in UAE24 and no RTB producer signature is fabricated here. RTB analysis enrichment requires an independently reviewed future port. Missing references must be surfaced as integration blockers, not edited in another branch without instruction.

## QA inventory

- `tests/test_unified_analytics_pie_research_handoff.py`: **24 authored** synthetic positive/negative unit and shape tests, golden exact manifest/row versions, deterministic sorted IDs, independently calculated HMAC and two SHA digests, nonce/expiry binding, live visibility denial, A/B source mismatch, score drift, no invented project missingness, empirical eight-dimension sidecar and min-N suppression.
- `tests/test_unified_analytics_pie_research_handoff_pg17.py`: **2 conditional** disposable PostgreSQL 17 tests: real UAE15 partial cannot read C/A/B facts or generate a signed manifest, and current UAE85 denies production qualified records.
- `tests/test_unified_analytics_pie_peer_contract_optional.py`: **2 conditional cross-branch** tests that import the actual PIE `load_dataset_json` parser and `HMACReceiptVerifier` only when the PIE package is present after integration; on standalone UAE they **SKIP**, not PASS.
- Static re-fetch/source invariants are **not equivalent** to executed Python tests or live signed peer E2E. In this remote-only session repository is not checked out locally and isolated PG17 is not attached; any unexecuted tests must be marked **NOT RUN**. Developer local commands:

```bash
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Optional disposable-only PostgreSQL17: CM_TEST_PG_DSN
```

**Next:** UA-MOD-26 — project-scoped HTTP API integration, but **do not expose raw PIE research manifests/signed receipts publicly**, provide authenticated current rights and CSRF/Host/Origin protection or enable hypothetical qualified states before the separately reviewed peer integration.
