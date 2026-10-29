# SRE ↔ QDP integration in main — 2026-10-29

The source for SRE `feature/sre-production-v1-20261023` at
`21da91853be15252af3da81712c0934a9b39e37a` is composed with
main's QDP (and preserved Collector, RTB, Search Planner, Group A).
**The first real modern SRE M11/M30/M32 → QDP qualification path is wired in
source, but must pass disposable PG17, real-media and live-model E2E on the
operator's machine before being declared operational.**

## Durable and authoritative flow

1. Collector/D1 provides actual persisted, licensed video and source metadata.
   Optional Collector → SRE M35 adapter `CollectorDurableInboxAdapter` remains
   separately operator-authorized; neither SRE nor QDP forges the prior D1 ACK.
2. SRE M11 LIVE plan uses approved RTB/C9, real D1 content and explicit model/
   provider controls. Only M30 authentic response+receipt+evidence-backed full
   hypothesis coverage, *completed* run and most recent M32 Human Review can
   produce one immutable per-video M36 `sre.classifications_ready/v1` event.
   S4 offline/fake outcomes are never qualified.
3. Trusted SRE dispatcher claims one event with SRE's PostgreSQL fencing
   token; QDP re-reads the **same original SRE outbox row** and immutable
   payload+SHA plus current `sre_m36_live_only_export` and source rights,
   inserts `qdp_sre_live_events` and independently reads its durable receipt.
   Only after this evidence does SRE perform a separate fenced source ACK.
   A crash before the ACK can replay the same original UUID+SHA, not create
   a second QDP source.
4. SRE M34/M32 invalidation intents are materialized into signed, immutable
   `sre.results_invalidated/v1` events and prioritized by the original
   source lease. QDP writes a durable *prior-ready UUID + SHA tombstone*
   even if invalidation arrives before the ready event. That original source
   cannot be resurrected by replay.
5. The **modern QDP intake** uses SRE's actual `sre_live_runs`,
   `sre_live_items`, M30 UUID `result_id` and M32 UUID `review_id`;
   it requires one already-ACKed, still-current source event **per** original
   SRE video, full sorted leaf coverage, exact effective-vs-model decisions,
   immutable D1 content SHA and current approved taxonomy. It computes the
   QDP Qualified Set without inserting fake BIGINT SRE results or rewriting
   original SRE decisions.
6. New QDP runs are pinned using a distinct composite FK
   `qdp_runs.sre_live_run_id` and `qdp_item_lineage.sre_live_result_id`;
   legacy v1 numeric SRE FKs and historical SHA remain unchanged. Each
   QDP worker and group-preparation rereads the authoritative LIVE source;
   the `qdp_current_base_enrichments` and
   `qdp_group_current_preparations` views hide revoked/changed results
   immediately. The downstream **QDP → Group A** durable receipt, queue
   and execution rights gate remain separate and intact.

## Local operator workflow

### PostgreSQL schema gate

Before operating on a valuable database, back up Postgres, the migration
ledger, D1 media, QDP provenance and cost/consent ledgers. Apply to a
**disposable PostgreSQL 17** first:

```bash
python -m intelligence.migrate
```

New integrated migrations:

- SRE `016_sre_...`, `017_sre_...`, LIVE `054_sre...` through
  `071_sre_mutation_request_receipts.sql`.
- `072_sre_qdp_live_event_inbox.sql`: dual-source QDP durable per-video ACK
  and immutable revocation tombstones.
- `073_qdp_modern_sre_qualification.sql`: exact modern UUID lineage and
  legacy FK coexistence.
- `074_qdp_modern_sre_current_rights.sql`: instantaneous current-rights
  revalidation for new QDP runs/Group A/B/C.

**Explicit SRE 062 repair:** The original standalone
`062_sre_live_opus_receipts.sql` was syntactically corrupt: truncated
`evidence_sha256` CHECK, missing `input_tokens` at that location and
a duplicated tail. The main integration tree contains the single reconstructed
forward SQL body. This changes that file's SHA: if an existing
`schema_migrations` ledger contains a different applied version, **STOP**,
do not change its checksum or run destructive fixes. Reconcile from an
audited database backup with an operator. No migration execution was
performed in this GitHub-only integration session.

### Explicit real SRE publisher → QDP

In the LOCAL process environment, with no secrets committed:

```dotenv
CM_DB_ENABLED=1
CM_DB_DSN=postgresql://YOUR_LOCAL_USER:YOUR_PASSWORD@127.0.0.1:54329/contentmachine
CM_SRE_QDP_ENABLED=1
CM_SRE_QDP_ALLOWED_PROJECTS=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
CM_SRE_QDP_ACTOR=local-sre-qdp-operator
```

Replace the example project ID with the **actual authorized project ID**.
Run after SRE has a genuinely completed LIVE run with all M30/M32 decisions:

```bash
python -m intelligence.relevance.qdp_delivery_cli \
  --project YOUR_32_HEX_PROJECT_ID \
  --publish-run YOUR_COMPLETED_SRE_LIVE_RUN_UUID --drain 100
```

The publisher reads real SRE data and **does not perform** an Opus or
other paid request. It materializes pending revocations before claiming
deliveries. Repeat `--project ... --drain 100` as needed until no work;
the same original event is idempotent. All functions require explicitly
trusted project+actor authorization, not a browser-supplied actor header.
There is NO unbounded or implicitly enabled worker for this bridge.

Open **Qualified Data Studio**, select the completed/accepted modern LIVE run,
preview Qualified Set, explicitly create a QDP run and let the existing QDP
local worker prepare A/B/C. An accepted SRE item is NOT itself a completed
QDP run or Group A analysis. Group A's actual local extraction workers/models
still need independent operator configuration.

## Runtime/QA restrictions

- Existing SRE M04 GET manifest/health can be mounted in the main local
  same-origin host, and static SRE Studio files are served. The privileged
  M38/M39 WSGI API requires **independent host session identity, CSRF,
  persistent rate limits and human-presence proof**. It is not silently
  mounted behind mere Host/Origin headers, and therefore this static Studio
  cannot perform paid mutations in the merged default host.
- SRE M35 Collector receiver requires independent publication resolver and
  rights-authorizer; it is installable but not automatically dispatching D1.
- Static source validation and authored contract tests are not a PASS for
  fresh/upgrade PG17 SQL, external DTK, live pretrained ONNX, paid Opus,
  Windows workers, media decoding, execution resources or actual Group A.
- Before release, run:
  `python -m unittest tests.test_sre_qdp_integrated_main`
  and the existing QDP + SRE suites; fresh and upgraded disposable PG17
  migrations including 062/072/073/074; real M30/M32 completed per-item
  publisher, 100x idempotent replay, lost reply, latest review supersession,
  source loss, invalidation before ready, QDP current-views disappearance,
  Qualified Set creation and actual Group A accepted receipt.
- Do not expose DSNs, operator allowlists or review credentials over HTTP.
  An OpenRouter key is not permission to incur costs. No automatic paid
  execution is enabled by this merge.
