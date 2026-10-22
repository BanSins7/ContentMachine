> **Актуализация 22.10.2026:** **Исторический release gate от 21.10.2026.** PR #9 после данного отчёта был **объединён в `main`**. Фразы ниже «main: original single-pass» и «BLOCKED — not released» описывают состояние до merge. Свежий успешный Linux+Windows CI на нынешнем SHA и живой двухпроходный Opus E2E не подтверждены. Подробности: [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# RTB Hierarchical — release gate / 2026-10-21

## Current decision: BLOCKED — do not advertise as released

- PR: https://github.com/BanSins7/ContentMachine/pull/9
- Main branch: original single-pass, human-reviewed Taxonomy Builder.
- Feature branch: two Opus passes, two human reviews, coverage/dedup/gap/
  bounded saturation checks, immutable PostgreSQL tree + SRE projection,
  mono UI with expandable tree, cost/rights safety.
- Additional documents:
  - RTB_EXTERNAL_ALGORITHMS_2026-10-21.md
  - VIDEO_COLLECTOR_V1_RESEARCH_2026-10-21.md

## What is actually verified

- Earlier Linux CI with disposable PostgreSQL executed full simulated
  two-pass -> human1 -> second -> optional expansion -> human2 ->
  SRE publish tests successfully. It does **not** prove live Opus quality.
- Browser controller with synthetic data was exercised outside Actions
  through first and second human approvals and a visual final tree;
  there were no paid calls.
- No live Opus / OpenRouter E2E run of deep mode has been performed.
- A standalone zero-cost Windows script is provided as
  `CHECK_RTB_RELEASE_WINDOWS.bat`.
- Last branch checks are NOT green: Linux and Windows GitHub Actions
  jobs finish before *any* step begins (`steps=[]`), logs are empty.
  Retries and fresh commits reproduce the same. The exact account
  reason cannot be inferred from this symptom.

## How to unblock

1. Repository owner examines GitHub **Settings -> Billing and licensing**
   / Actions usage, spending limits and payment restrictions. This is a
   hypothesis, **not** a confirmed cause.
2. Also review repository **Settings -> Actions -> General** and
   runner availability; no change should be made without reviewing
   permissions and billing.
3. If the symptom persists, open a GitHub Support request with an
   affected workflow run ID, job ID and description `0 steps,
   no worker allocation/logs`. Check GitHub's general status page.
4. Re-run Linux PostgreSQL tests **and** Windows smoke on the current
   exact PR head SHA. Both required.
5. Verify semantic safety of edited/merged/rejected nodes, deterministic
   billing cap and no retry after provider uncertainty.
6. On the owner's Windows machine, optionally run
   `CHECK_RTB_RELEASE_WINDOWS.bat` in a separate branch checkout.
   It is an **additional** no-cost check, not a substitute for
   PostgreSQL integration testing on disposable database.
7. Only after both CI checks are green, merge PR #9 into main.
8. Owner pulls main and starts ContentMachine, verifies connected
   PostgreSQL + new migration + one human-reviewed demo in the browser.
9. Live Opus test requires separately approved budget; absence of a
   paid demo must be disclosed even after code release.

## What will not be done

- No silent merge bypassing a known red release gate.
- No automatic paid test, repeat, or synthetic claim that the actual
  Opus research quality passed.
- No wholesale replacing legacy projects/Taxonomy v1.
- No confusing Video Collector discovery with taxonomy generation.
