# SRE M18 — Project-Scoped Embedding Cache / Exact Pinning / Revocation

**2026-10-26 · branch:** \`feature/sre-production-v1-20261023\` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Status:** implementation and tests committed; actual GitHub Actions acceptance PENDING. Genuine pretrained ONNX model verification remains **BLOCKED** by missing independently pinned real model.onnx/tokenizer.json. Cached transport math and synthetic PG fixtures do **not** satisfy actual semantic inference DoD. No network download, cloud embeddings, provider POST, price, finance writes or main merge.

## Purpose

M18 adds **transactionally deduplicated, durable (PostgreSQL17) local M16 embedding vectors**, tenant-scoped to a precisely pinned M12 LIVE item, C9 deep publication and current D1/S3 content. It reuses all 1–48 positive/exclusion leaf vectors rather than caching potentially stale classification labels. The M17 output still contains every C9 leaf; low/negative scores never become \`no_match\`. No raw video, transcript, source quotes or unapproved query text are persisted in the cache.

A hot cache entry is **not authority** to reuse expired or revoked video rights, bypass a deleted D1 version or infer on unapproved C9. On every request M18 revalidates live S2 C9/H1/H2, live D1 source policy/observation/expiry, immutable M12 run/item pins, and operator-approved ONNX file checksums **before looking up** cached vectors, then rechecks C9 and D1 once more immediately before returning.

## Implementation

- New forward-only additive \`intelligence/migrations/057_sre_local_embedding_cache.sql\`; no edits to earlier SRE 016/017/054–056 or main migrations. \`sre_local_embedding_cache\`:
  - \`project_id\` FK to SRE workspace; actual C9 \`(project_id,publication_id,taxonomy_version_id)\` FK to \`tb_sre_taxonomy_links\`.
  - D1 immutable composite \`(content_version_id,project_id,video_id,source_id,source_policy_version_id,content_sha256)\` FK to \`sre_content_versions\`, **ON DELETE CASCADE**. Revoking a record never requires retaining an embedding. A deletion deletes dependent cache.
  - Full immutable unique key: project, exact C9 publication/version/tree SHA/context SHA, real local M16 model identity SHA, D1 video/version/content SHA/source/policy and semantic input SHA.
  - Only JSONB numeric vectors, count 2..97 (video + 48 positive + 48 exclusions), dimensions 1..4096, ≤2 MB total JSON per row; streaming source SHA is checked before creation. SQL refuses expiry in past or more than 24 hours ahead and any UPDATE of protected entries. Explicit DELETE is allowed for rights invalidation. No PUBLIC read/write permission.
- \`intelligence/relevance/local_embedding_cache.py\`:
  - \`rank_live_run_item_cached(conn, project, run_uuid, item_id, model_directory, model_pins, ttl_seconds=3600)\` is the **internal project-authorized** entrypoint. Project IDs alone are not authentication. It locks the project workspace \`FOR UPDATE\` to serialize duplicate misses and enforce max **1,000 entries per project**, then \`source_policy_versions FOR SHARE\` so a policy revocation cannot commit halfway through a cache-use transaction.
  - Calls the existing real DB authority \`_authorize_live_rank_inputs\` and validates C9/D1/manifest exactly. SHA-checks the trusted locally provided M16 \`model.onnx\` and \`tokenizer.json\` **for both cold and hot lookups**, so missing/changed local weights never become a fake real inference cache.
  - A hit validates independently pinned exact key, expiry against **PostgreSQL \`clock_timestamp()\`**, vector dimensions/count/numeric values/unit norm and stored SHA. Cache SHA failure → typed fail-closed; it never switches to a fabricated lexical vector.
  - Cold miss loads **only genuine verified CPU ONNX backend**, obtains video/leaf/exclusion vectors from M17 in batches ≤32, checks model/output/input SHA, normalized finite vectors and writes one immutable row. \`ON CONFLICT DO NOTHING\` plus workspace lock is idempotent under simultaneous requests. Reusing the same full key returns a deterministic ranking over stored vectors; \`cache_hit\` is surfaced transparently.
  - TTL strictly 1–86,400 seconds; SQL expiry is \`min(now+requested TTL, D1/S3 actual content expiry)\`; no hidden extension past source policy's expiry. Expired rows and oldest excess entries are deleted project-locally. Cold/hot scoring never writes classification results, LLM attempts, paid ledger or irreversible decisions.
  - Near transaction return, repeats **fresh C9 approval and D1 source/observation/expiry** checks and compares full content and approval fingerprints, refusing changed rights. This additionally guards long CPU computations while policies evolve.
  - \`purge_local_embedding_cache(conn, project, content_version_id=... | source_policy_version_id=... | expired_only=True)\` allows a *trusted* revocation/deletion hook to delete matching rows without reading already prohibited D1 source text. Explicit target is required. Even if a revocation event did not call purge yet, the next cache lookup always fails the current D1 gate.
- \`intelligence/relevance/live_leaf_ranking.py\` refactored without changing the original uncached M17 API: \`_authorize_live_rank_inputs\` unifies C9+D1 authority; \`_rank_inputs\` and \`_rank_vectors\` provide shared full-leaf scoring over genuine just-computed or verified previously-stored embedding vectors.

## Privacy and contract boundaries

Store vectors (potentially sensitive derived data) **only** in the scoped database, for at most 24 hours, under normal deployment access controls; never log raw texts or vector payloads, never expose cache table directly as a public HTTP API. User must separately authenticate to the project at the host boundary; an untrusted caller cannot simply specify a different project id. SQL REVOKE FROM PUBLIC is necessary but not by itself a replacement for DB role isolation, backups retention or host-level authorization.

While \`source_policy_versions\` is revoked, a cached row is **unavailable even before cleanup**: M18 acquires current active policy lock and M08 rejects source use before attempting to load the local model or returning any vector. Deleted D1 content cascades to delete the cached row. Expired entries cannot be fetched by SQL and are also subject to current D1 expiry checks. Taxonomy or content/model revision changes alter the full key and require new real model inference; there is no cross-project global cache lookup.

## QA and reproducibility

- \`tests/test_sre_local_embedding_cache.py\`: pure fake DB/mocked loader contract tests for cold/hot hashing, scoped unique inputs, duplicated inference prevention, exact C9/D1/model/source policy pins, rejection before cache lookup on revoked source or unavailable local model, zero/NaN/dimension mismatch/tampered stored SHA, bounded TTL, explicit purge. **Mocked math is not evidence of real ONNX**.
- \`tests/test_sre_local_embedding_cache_schema.py\`: forward-only 057 table, composite FKs, immutable SHA/vector cap, cascade source-deletion, expiry guards, migration next-prefix 058.
- \`tests/test_sre_offline_pg.py\`: actual disposable PostgreSQL17 with approved two-human-reviewed C9 and D1/S3: tenant FKs, immutable insert vectors, TTL >24h denial, no classification results, revoked D1 rights denial **before unavailable model lookup**, explicit safe purge. The test's directly injected vectors are **schema-validation fixtures only**, NOT genuine semantic model results.
- In that same PostgreSQL suite, \`test_m18_actual_onnx_cache_restart_requires_operator_model\` is the required **real-model** acceptance: condition \`CM_SRE_M16_REAL_MODEL_REQUIRED=1\`, legitimate local M16 pretrained model and checksums, cold genuine ONNX result, \`commit\`, new independent DB connection/hot-hit exact ranking, subsequent D1 policy revocation denial. If missing, it explicitly **SKIPS/BLOCKED** and cannot count toward M18 real DoD.
- \`tests/test_sre_migration_guard.py\` and \`test_sre_migration_guard_pg.py\`: new 057 mandatory, initial fresh install, main-only upgrade, no-op reapply, checksum change blocked, next migration prefix 058. \`.github/workflows/sre-contracts.yml\`: Linux/Windows fast and PG17 negative suites. Exact SHA CI pass is not asserted while \`queued\`.

Local fast (not semantic acceptance):

\`\`\`sh
python -m unittest discover -s tests -p test_sre_local_embedding_cache.py -v
python -m unittest discover -s tests -p test_sre_local_embedding_cache_schema.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

Real M16+M17+M18 acceptance only after operator supplies independently verified pretrained weights; must execute in the controlled CPU host, with disposable PG17 DSN:

\`\`\`sh
CM_SRE_M16_REAL_MODEL_REQUIRED=1 \
CM_SRE_M16_MODEL_DIR=/trusted/absolute/local-model \
CM_SRE_M16_PINS_FILE=/trusted/model-pins.json \
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES17_DSN>' \
python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

Capture exact model name/revision/SHA, PostgreSQL17 and CPU/ONNX versions, actual cold/hot values and exact branch HEAD before marking real-model QA completed. \`skip\`, fixture-only math and queued CI are **not PASS**.

**Outcome:** M18 code and offline/PG test suites committed; real-model acceptance BLOCKED, GitHub Actions not yet confirmed success. Next **M19 — Complete human-approved C9 hypothesis context into Opus**, only after the user asks again; M18 grants no paid/LLM execution permission.
