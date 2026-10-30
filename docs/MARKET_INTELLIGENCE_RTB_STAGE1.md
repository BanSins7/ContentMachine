# Market Intelligence → RTB: implementation in feature branch

Date: 2026-10-30. Branch: `feature/rtb-market-intelligence-20261030`.

## Scope implemented

- Project-scoped user seeds (video and author URLs) using **existing**
  `project_sources` / `project_source_versions`, not another URL database.
- Supported TikTok, YouTube, Instagram *direct* video URLs and account URLs,
  strictly canonicalized by existing Project Sources / D1 normalizers.
- Existing D1 manual URL import is enqueued idempotently when videos are provided.
  No provider search, profile crawl or paid AI invocation is silently started.
- Existing D1 videos are matched via canonical platform + platform video ID
  or exact saved creator canonical URL. No cross-project market reads.
- Use only eligible metric snapshots from active sources with explicit
  `can_store_metrics` and `can_derive_scores` policies; only completed
  Group B broad-format analyses with `can_analyze_media`; optionally join
  completed Group C creator-ratio calculations on the *same* metric snapshot.
- Deterministic grouping, coverage, measurement references, quality
  limitations and immutable `market-intelligence-snapshot/v1` PostgreSQL storage.
- Versioned, evidence-limited `rtb-market-evidence-context/v1`, and
  market-aware **preview-only** Opus strict-JSON format specification transport
  in `intelligence/taxonomy_builder/market_intelligence_v3.py`.
- Standalone Russian UI at `/market-intelligence.html`, API under
  `/api/projects/<project_id>/market-intelligence/*`. Default main RTB untouched.

## API

- `POST .../seeds` `{"videos":[...],"authors":[...]}` registers real project
  references, launches D1 offline import for video URLs.
- `GET .../preview` creates a read-only report from currently available data.
- `POST .../freeze` with `{}` writes immutable, deduplicated report.
- `GET .../snapshots/<UUID>` loads pinned report; rechecks source
  eligibility and refuses stale/revoked evidence.
- `GET .../rtb-context/<UUID>` returns grounded future RTB V3 DTO,
  **not a paid Opus call**.

## Explicit limitations and next integrations

1. User-provided accounts are saved as seeds, but automatic profile traversal,
   pagination, social API credentials, quota/pricing and rights are NOT installed
   by this patch. The current Collector registry only declares DTK keyword
   searches. A user seed is not proof that all author posts were collected.
2. D1's *manual* source **cannot store provider metric history or derive scores**.
   The report cannot claim success until another correctly permissioned source
   has supplied real snapshots. No fabricated metrics or default popularity.
3. Group B's `video_primary` / `carousel_format` are **coarse categories**,
   not detailed creative templates; fine-grained hook/scene/CTA specification
   requires a verified Group B/A creative evidence integration.
4. Current QDP path requires approved RTB→SRE before Group A/B/C, thus this
   branch does not pretend the existing analytics pipeline can fully run
   *before* RTB. A rights-preserving preliminary dataset analysis path is needed.
5. Trend stages are `unknown_insufficient_time_series`. Real trend lifecycle
   requires repeated comparable observations, publication-time cohorts and
   independent-creator diffusion; a one-time snapshot cannot prove acceleration.
6. V3 Opus format transport is **preview-only**. It deliberately does not
   alter V2 payment ledgers, skip Human Review, call OpenRouter, or publish
   incompatible format objects as old SRE leaf hypotheses.
7. No guarantee of live PostgreSQL migrations or provider E2E until tested
   on a disposable PostgreSQL 17 with actual approved data.

## Smoke tests

```bash
python -m unittest tests.test_market_intelligence_contracts -v
python -m intelligence.migrate  # AFTER database backup and disposable PG17 check
python app.py
# Visit http://127.0.0.1:8765/market-intelligence.html
```

No changes to legacy RTB V1/V2 schemas or committed migrations.
