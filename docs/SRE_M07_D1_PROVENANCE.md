# SRE M07 — Canonical D1 Video Provenance Reader

**Date:** 2026-10-26. **Branch:** `feature/sre-production-v1-20261023` (Draft PR #22). **Status: code implemented / GitHub Actions CI queued (not PASS).**

## Canonical boundary and immutable pin

Discovery D1 is the system of record for `videos`, `discoveries`, `sources`, `source_requests`, `search_runs`, `search_queries`, `source_video_metadata` and source policy versions. M07 does **not** create a duplicate video catalog. S3's existing `attach_discovery` is the explicit, idempotent owner-triggered D1 association, not automatic Collector ingestion; M07 is a **read-only** verification layer for an already attached content version.

Use `intelligence.relevance.ports.discovery.load_canonical_d1_provenance(project_id, content_version_id)` or `ports.d1_provenance.load_d1_provenance` for DB-authoritative reads. It opens one `REPEATABLE READ READ ONLY` PostgreSQL transaction and rechecks the S3 `read_content` content checksum, reviewed source policy, dynamic retention/expiry, and explicit project/selected-discovery membership **before** returning the exact D1 lineage. A lower-level `read_d1_provenance(conn,...)` exists for existing trusted DB transactions (must be a consistent, project-authorized transaction; not an HTTP/user-controlled connection).

The query joins:
- `project_registry`, `sre_project_videos` and `sre_project_discoveries` by exact project/video/selected discovery;
- `sre_readable_content_versions` by exact `(project_id, content_version_id)`, no mutable latest pointer;
- `discoveries` by the selected version's persisted discovery id, verifying video/source;
- D1 `source_requests` (when present) by id and exact `run_id`, `source_id`, `query_id`, with `search_runs` identity anchored by D1's FK;
- D1 `search_queries` (when present), rejecting a query from a different source;
- `source_video_metadata` (when present), matching video/source/exact reviewed policy ID;
- canonical D1 `videos.platform` and `videos.platform_video_id` **only** as identity. Never `videos.title`, guessed hashtag, global fallback or Collector candidate ID.

The returned `sre-d1-provenance/v1` has: `project_id`, `video_id`, `platform`, `platform_video_id`, `discovery_id`, `source_id`, `source_policy_version_id`, `source_request_id`, `search_run_id`, `search_query_id`, `metadata_observation_id`, `content_version_id`, `content_sha256`, `completeness`, `expires_at`, `provenance_sha256`. Nullable request/query/run/observation IDs are valid for **direct/manual D1 discovery** without Collector or a search run. All non-null D1 numeric IDs are explicitly 1..2^63-1 Python ints: reject Collector UUIDs, strings, floats and bools. Only D1-backed SQL proof is authoritative: the validator/digest alone does NOT grant access.

## Idempotent project-specific replay

`load_canonical_d1_provenance_many(project_id, content_version_ids)` and underlying `load_d1_provenance_many` accept 1..1000 explicitly chosen content IDs, sort and deduplicate **identical version IDs**, and read them in one consistent transaction. A different content version or explicitly selected discovery is a distinct provenance record; it cannot be collapsed merely because two D1 observations refer to the same global video. No attachment, writes, background work or request dispatch happens during this read.

## Negative and PostgreSQL17 tests

- `tests/test_sre_d1_provenance.py`: read-only single transaction and joins, exact IDs/SQL bind, manual D1 with null source_request/query/run, duplicate replay, invalid IDs/BIGINT overflows/Collector UUID, version/hash tampering, foreign project, wrong video/source/policy, D1 request/run/query mismatch, foreign-source query, wrong observation, ambiguous/deleted attachment, import without optional modules.
- `tests/test_sre_intake_pg.py` M07 extensions: real disposable PostgreSQL17 approved D1 direct/manual content, twice-attached idempotency, tenant denial, unlink denial, real search_runs/search_queries/source_requests binding and mutation fail-closed, source-policy revocation, query owned by another D1 source.
- `.github/workflows/sre-contracts.yml`: focused tests on Python3.12 Linux/Windows; PG17 extensions run in existing S3+M07 PostgreSQL suite **once**, avoiding duplicate CI spend. Full repository and Windows smoke must also pass on the same code SHA.

Commands:

```bash
python -m unittest discover -s tests -p test_sre_d1_provenance.py -v
CM_TEST_PG_DSN="<disposable-postgres17>" python -m unittest discover -s tests -p test_sre_intake_pg.py -v
```

**Not claimed in M07:** unrelated Collector feature-branch integration, authenticated public HTTP endpoint, external LLM rights/quote/consent, actual classification, M08 dynamic late-dispatch gate, physical data purge, successful CI or a production database deployment. No paid OpenRouter calls, SQL migrations, `main` merge or changes to other feature branches.

**Next stage:** M08 — content/rights/expiry version-level gate, only after the next user request.
