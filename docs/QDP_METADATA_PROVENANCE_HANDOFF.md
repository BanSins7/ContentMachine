# QDP FIX-07 — exact SRE S3 / D1 metadata integration handoff

## Source of truth
SRE's real `intelligence/relevance/intake.py::attach_discovery` uses `sre_content_versions` with `source_id`, `source_policy_version_id`, `metadata_observation_id` and `provenance.discovery_id`. It associates `sre_project_discoveries(project_id,video_id,discovery_id)` to the exact D1 `discoveries`. Its independently-owned SQL migration is `016_sre_d1_intake_rights.sql`.

QDP extracts SRE source/observation/provenance from each verified DB classification row and freezes exact references in `qdp-run-manifest/v1` per video. For Group A/B/C preparation QDP requires a **currently readable SRE content version**, matching immutable SRE SHA and same-project discovery. A D1 metadata row is fetched only using the exact pinned `metadata_observation_id` and matching source, policy, video, and current D1 permissions. Source content title/description is cross-checked for in-place modification. Missing fields, missing SRE migration, revoked rights, cross-project association, or changed source metadata BLOCK readiness with explicit reason codes.

No alternative source, latest fetched metadata, guessed publication date, API calls, paid providers, or downloads. The preparation inputs include exact `sre_content_version_id`, SHA, and `sre_discovery_id`, and QDP stored metadata includes the D1 observation pointer.

## Schema contract

Required from the SRE branch: `sre_content_versions.metadata_observation_id BIGINT` and `sre_project_discoveries` table with project-scoped links. QDP does not own these migrations. `sre_readable_content_versions` must enforce SRE rights; QDP checks it by joining the actual content base table for columns that may have been added after the original view was created. If peer SRE S3 is not installed, return `sre_metadata_contract_unavailable`, not success.

## Test contract

- Unit: `python -m unittest tests.test_qdp_provenance -v`
- Disposable PG17: `QDP_DISPOSABLE_TEST_DB=YES CM_TEST_PG_DSN=<TEST_DATABASE> python scripts/qdp_verify.py --with-postgres`
- Test cases: same video M1 from source S1 and newer M2 from S2 (must stay M1), an absent/revoked M1 (no fallback), foreign/missing project discovery, exact content SHA mismatch, and NULL D1 published_at staying NULL.
- QDP's standalone disposable tests can create SRE S3-like columns/tables **only in test DB**; no claim of actual peer S3 deployment or production E2E.
- Peer integration owner must install upstream SRE migration after checking collision/order with existing QDP `016` and `040/041` migrations. Never rewrite applied migrations.

## Status 2026-10-26
FIX-07 = CODE_IMPLEMENTED, QDP-owned PG17 QA_PASS: [40308355403](https://github.com/BanSins7/ContentMachine/actions/runs/40308355403), 116/116 tests, code SHA `b677a8dd`. Earlier FIX-00..06 PG17 success [40307133052](https://github.com/BanSins7/ContentMachine/actions/runs/40307133052) at `e2844c8e`. Actual SRE S3 cross-branch LIVE E2E remains BLOCKED_EXTERNAL.
