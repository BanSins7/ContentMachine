# SRE M09 — Zero-Side-Effect Dataset Preview

**Date:** 2026-10-26. **Branch:** `feature/sre-production-v1-20261023`; **PR:** [#22](https://github.com/BanSins7/ContentMachine/pull/22) (Draft). **Implementation:** committed; **CI:** queued/not an observed PASS.

## Canonical input and trust boundary

Production entrypoint:

```python
from intelligence.relevance.dataset_preview import load_dataset_preview
preview = load_dataset_preview(project_id, approved_c9_publication_uuid, [d1_content_version_id])
```

`load_dataset_preview` opens ONE PostgreSQL `REPEATABLE READ READ ONLY` transaction (injected `connect` may be used only by a trusted service/test). `read_dataset_preview(conn,...)` is a lower-level helper for an existing consistent trusted transaction. There is **no** write, HTTP, OpenRouter invocation, source crawling, implicit Collector or QDP integration, mutable taxonomy pointer, automatic classification, worker enqueue, budget authorization or paid run.

Inside the transaction:
1. S2 `read_authorized_taxonomy` checks **actual deep RTB publication and H1/H2 human review**, facts, SRE mirror for the *explicit* `(project_id,publication_id)`. M06 `build_c9_bundle` gives all approved hypothesis keys, contexts, ancestry and hashes (never trust a user-provided bundle for this service).
2. User-selected content version IDs must be 1..1000 true positive PostgreSQL signed BIGINTs. Duplicate IDs are reported and counted; no global D1 search, public title fallback or arbitrary Collector query UUID.
3. Query `sre_content_versions` by the registered project's existing workspace/project-video association, **not** the rights-filtered view. That is intentional: revoked, expired or incomplete **owned** selections must still produce *blocked* rows, not mysteriously disappear. Any input version missing from the project scope fails the whole preview (avoid cross-project ID oracles).
4. Group versions by canonical `videos.id`, retaining highest explicitly selected content `revision` (tie = highest content_version_id). `deduplicated_video_versions` records every intentionally omitted older version with the version actually kept. Repeated exact version IDs have explicit `duplicate_references` accounting; no silent collapse or automatic latest unselected version.
5. For each selected video, M08 `read_live_content_gate` rechecks S3 rights, observation expiry/revocation, M07 D1 query/source/request lineage and the exact selected content SHA. Rights-expired, source-disabled, invalid provenance, malformed content or policy is represented as `blocked` for *every* C9 leaf; never as `no_match` and never as zero rows.
6. Metadata-only with no title, description or hashtags is `blocked/no_classifiable_content` even when rights are valid. Missing title/description/transcript/scenes are explicit warning codes. Actual transcript/scenes are never invented or guessed from a URL.

## Output / status

`sre-dataset-preview/v1`: `project_id`, `publication_id`, pinned `taxonomy_sha256`, `taxonomy_version_id`, `bundle_sha256`, exact ordered `hypothesis_keys`, `selected_versions`, `deduplicated_video_versions`, `videos`, **complete** `pairs`, `counts`, `execution_allowed=False` and `preview_sha256`. No raw content or sensitive title/quote returned in preview response; a blocked version's original content hash is not returned.

Every selected video pairs with every approved leaf. A pair contains `(video_id,content_version_id,category_key,status,reason)` with `eligible` or `blocked` status only. An eligible pair is **not** classified positive and is **not** consent for spending or provider dispatch. A blocked pair remains visible with a deterministic reason (`rights_unavailable`, `content_expired`, `invalid_d1_provenance`, `content_validation_failed` or `no_classifiable_content`). Counts always satisfy `total_pairs = selected_videos * hypotheses = eligible_pairs + blocked_pairs = len(pairs)`.

Limits: 1..1000 input references, <=48 actual approved C9 leaf hypotheses, <=48000 pair records. Pair list is never truncated. Version ambiguity is disclosed, not silently resolved by a latest-global-pointer lookup.

## Tests

- `tests/test_sre_dataset_preview.py`: fake DB read-only calls; C9 full keys; 100x20=2000 exact pairs; 2 versions for same video highest-revision pin; duplicate reference metrics; five blocked/eligible conditions; foreign project/missing input denial; BIGINT and malformed IDs; deterministic fingerprint; optional dependency import independence.
- `tests/test_sre_offline_pg.py`: additional real disposable PostgreSQL 17 tests: approved two-human C9 + two actual D1 videos, changing source-specific observation to create second immutable version, exact selection/dedup and 6/6 pair coverage; separate read-only production loader; source policy revocation and observation deletion resulting in 6/6 or 3/3 blocked pairs; cross-project disclosure rejected.
- `.github/workflows/sre-contracts.yml`: same Linux + Windows no-provider unit job and existing real PG17 S4 job. Full repository/Windows/Knowledge B CI also required. No live model spend.

```sh
python -m unittest discover -s tests -p test_sre_dataset_preview.py -v
CM_TEST_PG_DSN='<DISPOSABLE_PG17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

**QA truth:** merely committing tests does not mean they executed. Until exact code SHA is green, M09 code is implemented, acceptance remains **CI PENDING**. Future M10 typed live run contracts and M11 durable SQL runs do not yet exist here. M09 never grants authorization and future M08 checks are still mandatory at `before_external_send`/publish.

**Next:** M10 — explicit typed LIVE FSM/DTO and technical outcome taxonomy, on the next user request.
