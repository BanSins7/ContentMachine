# Unified Analytics — Integration Boundary / Independent Module
**Status:** bootstrapped contracts only; live cross-module integration will be performed in later UA-MOD stages.
**Canonical new branch:** `feature/unified-analytics-full-module`.
**Preserved legacy branch:** `feature/analytics-engine-qualified-pipeline-20261023` (PR #23). All legacy files and 016–018 migration SQL are read-only sources.

## Ownership and truth semantics
Unified Analytics owns: verified join, qualification gate, immutable Unified Snapshot, descriptive aggregates, versioned read API, queue, worker, outbox and operator Studio. It does NOT own SRE, QDP, video acquisition, Group A/B extraction, Group C arithmetic or future Patterns/Insights.

### Incoming peer contracts (proposed until handshake is verified)
- **SRE**: approved/current relevance for video_id/project taxonomy, live execution and human review override; no offline_fixture may qualify a real publication.
- **QDP**: qualified video/member with project_id, qdp_run_id, preparation fingerprint and source references; ready B/C only means prepared, not completed execution.
- **Group A**: exact completed A video/carousel analysis PK, source/policy/media SHA, correct scene/speech/OCR/subtitle or carousel coverage. Do not convert unknown text into measured absence.
- **Group B**: exact completed B parent record with needs_review=false and applicable B1–B8, linked to exact A PK; no B2 image_post contract exists.
- **Group C**: completed **persisted** calculated_metrics id, metric_snapshot_id, approved configuration/cohort evidence and exact source metric definition/version. UAE must never recompute C7.
- **D1 source policy and canonical catalog**: `project_registry.id`, `videos.id`, `sources.id`, `source_policy_versions.id`, current retention/expiry, project membership and access control must be checked on creation and every read.

### Output
Immutable `analytics_unified_snapshots` (once schema is reconciled) with `qualified | partial | blocked`, machine-readable blockers, original A/B/C/SRE/QDP references, a stable fingerprint, and current visibility status. Descriptive aggregate with dimension, strata key/version, sample_size, missingness and statistics in Decimal, sent to Patterns/Insights via a versioned evidence manifest.

## Migration ownership policy
Old PR23 `016_analytics_unified.sql` is no longer safe to cherry-pick into main: main already has a different 016. Old PR23 `017_analytics_jobs_and_read_gates.sql` and `018_analytics_audit_hardening.sql` include source-policy/C governance modifications that belong partly to Group C. Audit statement-by-statement and create new forward-only UAE migrations at a collision-free stage, after required upstream tables; never edit installed migrations or their SHA.

## Qualification matrix
| Signal | Required for `qualified` |
|---|---|
| SRE | Current approved LIVE positive relevance or current explicitly human-approved match |
| QDP | Qualified membership with matching project/input hash and completed preparation provenance |
| A | Current complete, media-verified A analysis for the exact video/asset |
| B | Current complete B1–B8 applicable evidence; no review pending; exact A-analysis ref |
| C | Current complete persisted Group C calculation tied to real compatible metrics, approved config and frozen cohorts |
| Rights/retention | Current permits all required operations and referenced evidence remains valid |

Some diagnostic `partial` snapshots may be stored for debugging but must never appear in qualified-only aggregates and rankings. `blocked` sources cannot leak raw evidence through diagnostics.

## Version and hash invariants
video_id BIGINT >0, A/B/C analysis IDs BIGINT >0, project_id 32 lower-hex, SHA256 64 lower-hex; parse QDP/SRE UUIDs only if their actual port contract declares UUID. Use exact peer schemas, do not invent cross-product lookups. Input fields are canonical references, not proof of data availability.

## Breakout semantics
C score is not in [0,100] and is not normalized; `niche_outlier` is not a C7 component. UAE may compare complete scores in strictly compatible strata but must not silently change the weighting or recalculate it. No claims of causation from descriptive association.

## E2E gates
Disposable PostgreSQL 17 from fresh install and approved upgrade; replay/immutability + source revocation, model-free Worker recovery, full video and carousel fixtures, exact peer handshakes; explicit test labels for unavailable feature branches.