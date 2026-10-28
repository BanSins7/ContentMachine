# UA-MOD-31 — Scale & adversarial QA: reproducible local protocol

**Branch:** `feature/unified-analytics-full-module` · **Stage:** IMPLEMENTED TEST/LOAD HARNESS; REAL LOAD MEASUREMENTS **NOT RUN** · **PR #38:** Draft · No peer/main/migration/code-owner changes.

## Honest load scope

The **current UAE13/084/085 trust/approval gates forbid inserting positive `qualified` snapshots**, and `uae_readside_visibility_candidates` cannot legitimately contain 10,000 or 50,000 qualified rows without real independently authenticated QDP/SRE/Group A/B/C approved producer data. Do **not** relax triggers, write `checked=true`, shadow producer tables or fake positive counts just to achieve green load results.

`python -m intelligence.unified_analytics.scale_benchmark` is an **opt-in PostgreSQL 17 storage/index benchmark**. It populates exactly 10,000 or 50,000 *unqualified `blocked`* historical UAE snapshots, each with its own immutable request, sharing one disposable project/source/video. SQL inserts use bounded 1..5,000-row set-based transactions and real request-source FKs. The rows intentionally contain **NO seven-peer approved evidence, NO 084 JSON payload, NO real score/cohort and NO media content**. This measures batch insert throughput and project-scoped keyset pagination (50 rows/page); it does **not** measure Group A/B/C worker throughput, source resolver throughput, joined extraction, SQL qualifying gate, qualified read latency or end-to-end success.

A **200-row small PG17 smoke** is conditional on `CM_TEST_PG_DSN` in `tests/test_unified_analytics_scale_benchmark_pg17.py`. It validates actual SQL/indices/triggers and source cleanup without imposing 10k/50k in the ordinary test suite. The 50k case is manually invoked when you want a load benchmark, never by the default QA runner. The module rejects production and worker/operator DSN matches, checks PG server v17 and requires `--confirm-disposable`. Each run uses a fresh random project/source/video and always attempts scoped FK-safe cleanup. There is **no TRUNCATE, DROP TABLE, trigger disabling or arbitrary project traversal**.

### Actual measurement commands (local PostgreSQL 17 only)

Set `CM_TEST_PG_DSN` to an isolated throwaway PG17 DB; it **must not equal `CM_DB_DSN`**, `CM_UAE_WORKER_DSN` or `CM_UAE_OPS_DSN`. This is a destructive **test-only** database: the harness applies existing project migrations and deletes ONLY its generated project/source/video after the run.

Linux/macOS:

```bash
export CM_TEST_PG_DSN='postgresql://<user>:<secret>@127.0.0.1:5432/<disposable_uae_db>'
python -m intelligence.unified_analytics.scale_benchmark --rows 10000 --batch 2000 --confirm-disposable
python -m intelligence.unified_analytics.scale_benchmark --rows 50000 --batch 5000 --confirm-disposable
```

Windows PowerShell:

```powershell
$env:CM_TEST_PG_DSN = 'postgresql://<user>:<secret>@127.0.0.1:5432/<disposable_uae_db>'
python -m intelligence.unified_analytics.scale_benchmark --rows 10000 --batch 2000 --confirm-disposable
python -m intelligence.unified_analytics.scale_benchmark --rows 50000 --batch 5000 --confirm-disposable
```

The script reports **actual** `insert_seconds`, `insert_rows_per_second`, `keyset_seconds`, `keyset_rows_per_second`, `rows` and `scanned` **only if you execute it successfully**. These measurements exclude fixture provisioning/migration and cleanup, and must NOT be described as production qualified throughput. The emitted report explicitly states `qualified_records: 0`, `qualified_e2e_throughput_measured: false`, `worker_throughput_measured: false`, `producer_model_latency_measured: false` and `peak_memory_measured: false`. No raw DSN, project/source IDs, media IDs, transcripts or scores are returned. The CLI catches SQL errors with a fixed sanitized message and exit 2; Ctrl+C exit 130.

**Measured results in this GitHub-only session:** Not available. No measured p50/p95, throughput, peak RSS or real 50k qualified success rate is asserted.

| Load objective | Fixture | Measured duration | Verified throughput | Caveat |
|---|---|---|---|---|
| 10k batch write | 10k synthetic **blocked**, one project/video | **NOT RUN** | **NOT RUN** | Only storage SQL, no immutable seven-port payload |
| 10k keyset read | 50 rows/page, blocked-only | **NOT RUN** | **NOT RUN** | Not UA19 qualified readside |
| 50k batch write | 50k synthetic **blocked**, one project/video | **NOT RUN** | **NOT RUN** | Synthetic/known count |
| 50k keyset read | 50 rows/page | **NOT RUN** | **NOT RUN** | No live project rights re-verification |
| 16 worker claimers / 100 jobs | Synthetic QDP A/B/C *queue receipts* | **NOT RUN** | **NOT RUN** | No trusted resolver or A/B/C producer |
| 10k/50k genuinely qualified snapshots | Actual reviewed QDP/SRE/A/B/C | **BLOCKED** | **BLOCKED** | UA13/085, independent peer trust not connected |

## Adversarial and concurrency inventory

| Test module | Authored tests | Purpose |
|---|---:|---|
| `test_unified_analytics_adversarial_http.py` | **32** | Duplicate nested/top-level JSON keys, NaN/±Inf, malformed UTF-8/truncated/deep/big/empty JSON, invalid media type/length, SQL query injection, path/query overflows, CSRF/version/Origin/token/cross-project gates; no writes or secret reflection. Actual UA26 HTTP handler/service via injected DB, no open listening socket. |
| `test_unified_analytics_adversarial_workers.py` | **14** | 8-way enqueue, 8-way and 20-job multi-worker mock race, stale generation and abandoned/cancelled lease, race with rights revocation, dead-letter, injected SQL/ack failures, rollback and exception redaction. Mock atomic locks are **not** evidence of PG17 concurrency. |
| `test_unified_analytics_adversarial_pg17.py` | **5 conditional** | Real disposable PostgreSQL17 100 queue jobs/16 concurrent claimers and 8-way idempotent replay, revoked-source renew/ack failure, canceled lease fence, no fake qualified result. `FOR UPDATE SKIP LOCKED` may legitimately leave work after one simultaneous claim wave; fixture drains remaining with bounded subsequent claims. |
| `test_unified_analytics_adversarial_precision.py` | **12** | Decimal Type-7 quantiles on 50 values; 50 order shuffles + 80 parallel computations; n≥5 and max 50, high precision, negatives and values above 100, overflow/NaN/float refusal. Pure arithmetic only; no live Group C scores. |
| `test_unified_analytics_scale_benchmark.py` | **19** | Mock virtual 10k/50k execution, strict opt-in, safe DSN, batch bound, keyset, exact zero qualified, failure cleanup, redacted report; virtual timing values are NOT production throughput. |
| `test_unified_analytics_scale_benchmark_pg17.py` | **3 conditional** | Small real PG17 200/25 blocked SQL/keyset/cleanup + invalid-size guards; large 50k is manual. |
| `test_unified_analytics_qa_runner.py` | **13** | Windows/Linux Python subprocess runner default no PG, explicit PG17 opt-in, timeouts, swallowed secret-bearing stderr, exit codes, no fake qualified claims. |
| `test_unified_analytics_adversarial_static.py` | **20** | Static verification of real UA085/088, 10k/50k benchmark scope, SQL params, token/DSN separation and no public grants or trigger disabling. |

**Total 118 authored checks, including 8 conditional disposable PG17 tests.** All **NOT EXECUTED** in this session; a static GitHub source consistency check is reported separately and is not equivalent to `unittest`.

## One-shot cross-platform local QA

Existing `.github/workflows/unified-analytics-module.yml` already declares Windows + Ubuntu Python 3.11 and disposable PostgreSQL17 jobs. The workflows are **not proof of a pass** while queued; no automatic CI waiting/merges or edits to peer workflows are required. Prefer local execution according to the user's QA preference.

Linux/macOS and Windows PowerShell, from repo root:

```bash
python -m compileall -q intelligence/unified_analytics tests
python -m intelligence.unified_analytics.qa_runner
# Only after provisioning a disposable PostgreSQL 17:
python -m intelligence.unified_analytics.qa_runner --with-pg17 --confirm-disposable
```

The runner uses `sys.executable`, bounded subprocess execution, actual elapsed suite durations and a JSON summary. Without `--with-pg17`, it deliberately removes `CM_TEST_PG_DSN` from its children; the default runs only fast UA30/31 source/mock/adversarial/precision/static suites. With PG flag it enables selected UA30/31 PG tests, and each test still independently checks PG17 and DSN isolation. Failed suite stdout/stderr are **not echoed** (may contain DB details); run the individual unittest locally if full logs are needed. CI/runner no-false-approval fields stay `qualified_positive_e2e_verified=false` and `qualified_50k_load_verified=false`.

Full regression on your machine:

```bash
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
```

This stage leaves **real performance numbers, Windows/Linux execution confirmation, large PG17 load evidence, producer-approved positive E2E and eventual acceptance SLAs outstanding**. Do not mark full UA31 DoD passed or call PR38 release-ready. Next UA-MOD-32 is independent diff/integration/merge-readiness audit, not a premature merge.
