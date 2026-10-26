# SRE M12 — LIVE Run Planning and Idempotent Enqueue

**2026-10-26 · Branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Implementation:** committed, actual CI acceptance pending. **No paid calls**, provider dispatch, consent approval, worker start, financial tables, or alterations to main.

## Service boundary

`intelligence/relevance/live_planner.py` exposes two trusted internal entrypoints:

- `create_live_run(conn, project_id, approved_c9_publication_uuid, selected_d1_content_version_ids, pipeline_config_id, idempotency_key)`. Writes one immutable `sre_live_runs` plan and one `sre_live_items` record per selected video within a **single PostgreSQL transaction**, serialized by `SELECT ... FROM sre_workspaces ... FOR UPDATE` on the project row.
- `get_live_plan(conn, project_id, run_uuid)`. Read-only project-filtered status and item list, no source text, provider data, credentials, or price.

**Caller must authenticate the project owner** before invoking either operation. A self-declared `project_id` is NOT authentication. These are **not public HTTP endpoints** (see later M39).

## C9, D1 and pipeline authority

1. S2 `read_authorized_taxonomy(conn, pid, pub)` reads the **actual deep two-human-reviewed RTB publication and matching SRE projection**, not a self-hashed client bundle or mutable latest pointer. M06 `build_c9_bundle` derives every approved hypothesis leaf, exact version hashes and source-backed ancestry.
2. The selected D1 content IDs are bounded to 1–1000 signed BIGINT values and restricted by `(project_id,video_id)`. Duplicate identical IDs are canonicalized, as is their order. Multiple *explicitly* selected content revisions for one video retain the highest revision; the older ones are **listed explicitly** in `superseded_in_selection`. All originally selected distinct version IDs remain in the manifest/digest; adding an old revision is therefore a **changed request**, not a silent replay.
3. Every effective video is rechecked under M08 `read_live_content_gate(phase="planning", external_llm=True)` for currently reviewed active D1 rights, an **explicit** external-LLM grant, D1 source/request/discovery chain, observation, source-specific expiry and exact content SHA. Revoked/missing/unowned/unclassifiable input fails closed: M12 does **not** stage blocked content. The external grant here is solely source permission — **not** consent to pay.
4. Project-owned pipeline configuration is read from `sre_pipeline_configs`, validated with the existing pure `validate_pipeline` and SHA-pinned. **Model/provider catalog selection is NOT asserted** by M12; later stages enforce exact model identity, capabilities and budgets. No fallback model can execute here.
5. Versioned `sre-live-plan/v1` immutable manifest includes explicit `live` mode, project/C9 taxonomy and bundle SHA, pipeline/contexts SHA, full selected version set, superseded versions and per-video source/policy/origin/revision/content SHA and exactly all C9 leaf UUIDs. Canonical sorted JSON SHA-256 of this complete manifest is the stable `request_sha256` and `manifest_sha256`.

## Idempotent transaction

- A workspace row lock serializes concurrent planners in the same project; the SQL unique key `(project_id,idempotency_key)` provides the second defense.
- Same project/key with the **same verified canonical inputs** returns the existing run ID and its current status, `replayed=True`; input order and repeated exact IDs do not affect the request digest.
- Same project/key with different selected versions, taxonomy/pipeline configuration or content provenance returns `SREContractError(code="idempotency_conflict")`; future localhost API should translate this to **HTTP 409**. Failed validation or SQL insertion rolls back all run/item writes.
- New project/key creates `sre_live_runs(state="pending_consent",execution_mode="live")` and each `sre_live_items(state="not_evaluated")`. **No batch queued for dispatch**, no OpenRouter/LLM HTTP request, no provider attempt, no money reservation, no stored classification result. Actual enqueue-for-execution requires consent, quota and the next stages.
- A later source-rights revocation makes a new plan (and a current-rights replay) fail closed; it does not mutate or expose source text from the existing immutable plan. M08 **must be called again immediately before a future external send and result publication**.

## QA

- `tests/test_sre_live_planner.py`: deterministic no-DB fake connection; strict IDs/keys, actual fixture-derived approved C9, live D1 gate invocation, canonical request digest and mutation conflict, duplicate/version handling, tenant denial, absent/granted source rights, no mock provider import, no phantom batch/result.
- `tests/test_sre_offline_pg.py` extended with disposable **real PostgreSQL17** two-human-approved C9 and actual D1 observation; deny missing outbound grant; create and replay same run; reject changed-content key, assert persisted S3 revision/SHA, count no synthetic classifications; revoke policy; race two independent PostgreSQL connections for the same project/key and assert **one** run, **two** returned responses and **one** item.
- `.github/workflows/sre-contracts.yml` includes unit tests on Linux/Windows and PG17 integration plus inherited M01–M11 and full-repository regression requirements.

Reproduce without any paid model calls:

```sh
python -m unittest discover -s tests -p test_sre_live_planner.py -v
CM_TEST_PG_DSN='<DISPOSABLE_PG17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

**QA acceptance remains open until the exact final SHA has actual `conclusion=success` on all required Linux/Windows/PostgreSQL17 workflows.** CI `queued` is neither PASS nor FAIL. No external connected Collector/QDP feature branch is required.

**Next M13 (only on next user request):** deterministic exact batch manifest, coverage accounting and future dispatch boundary; remain zero-cost until later explicit consent/ledger stages.
