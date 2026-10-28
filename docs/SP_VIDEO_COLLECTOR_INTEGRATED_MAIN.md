# Search Planner ↔ Video Collector: main pipeline integration

This integration unifies two *previously separate* durable contracts. SP owns
the production QV inbox and immutable hypothesis/candidate provenance.
Collector owns immutable query sets, separately authorized held plans, jobs,
DTK worker, D1 ingest and downstream SRE outbox.

## Deployed flow

1. RTB Deep C9 V1 **or** RTB Deep V2 is published after the required Human
   Reviews. Search Planner generates candidates under a separately approved
   OpenRouter budget. QV verifies real local embeddings and writes immutable
   selected/rejected candidate decisions and coverage.
2. In Search Planner, approve the **HELD** source plan. Then run
   **Handoff → Ingest QV**. SP saves full reviewed QV coverage, selected query
   UUIDs, aliases and citations, and ACKs QV only in that transaction.
   The direct Collector QV publisher MUST NOT be run against the same event:
   only one system owns the source ACK.
3. In Search Planner, choose the received SP inbox event, then
   **Deliver**. Owner token `CM_SP_OWNER_TOKEN` is required. The SP
   dispatcher commits an uncertain attempt *before* invoking Collector.
   No automatic retries, provider requests or external payments.
4. The real `RealCollectorAdapter` checks the installed PostgreSQL schema.
   It calls `intelligence.collector.sp_handoff.accept_sp_handoff` directly,
   with no HTTP self-call and no browser-supplied reviewer/accepted flag.
5. Collector independently reconstructs the authoritative QV→SP projection,
   checks the exact stored chunk, source event, owner-approved HELD plan and
   original RTB V1/V2 lineage, then commits a real Collector QuerySet and
   immutable receipt **in one PostgreSQL transaction**. The canonical
   Collector fingerprint is recorded separately from the original SP chunk
   checksum to preserve both UUID orderings.
6. SP independently reads back the Collector receipt and only then marks the
   corresponding delivery accepted. Lost responses become
   `needs_attention`; **Reconcile** reads the existing receipt without
   re-sending a query set.
7. In the Video Collector UI, enter the same project ID and select
   **Show accepted SP QuerySets**, then pick a real stored QuerySet.
   **Preview → Commit HELD** creates only held jobs. Separate
   **Release** requires Collector owner confirmation, DTK rights, source
   policy, archive and explicit zero-spend cap.
8. Worker → DTK → D1 checkpoint → SRE delivery are the existing Collector
   implementation. D1 readback and run trace use the real V1/V2 publication
   ID and include SP receipt lineage.

## Real database prerequisites

- Apply the existing `054_collector_planning.sql` to
  `057_collector_discovery_outbox.sql`, Search Planner `060_sp_rtb_deep_v2_pipeline.sql`,
  then additive `061_collector_sp_durable_intake.sql`. Apply migrations
  using the repository's actual filename-ordered migrator. Do **not**
  edit already applied versions.
- Set `CM_RTB_REVIEWER_ID` to an authorized reviewer for V2.
- Set a high-entropy local `CM_SP_OWNER_TOKEN`; do not expose it via
  frontend configuration or logs. The UI asks for it when needed.
- Real QV embedding model, authoritative provider catalog, supported
  DTK credentials, explicitly reviewed source permissions and local
  worker configuration must be provided separately.
- `collector_sp_intake_receipts` is immutable, project-scoped, indexed by
  idempotency key and SP event/chunk, and its query set is independently
  read back before SP finalization.

## Validation and release restrictions

- Offline unit tests: `python -m unittest discover -s tests -p 'test_sp_collector_bridge.py'`
  plus the existing Search Planner/Collector V1 suites; JS UI tests via
  the repo's current Node test command.
- Run fresh and upgraded disposable PostgreSQL 17 schemas and a real V1
  + Deep V2 QV → HELD → SP inbox → Collector accepted receipt run.
- Exercise same-key replay, concurrent deliveries, cross-project denial,
  lost-response recovery, altered chunk/hash rejection, missing rights,
  revoked consent, D1 persistence and real SRE ACK.
- This change **does not** prove live DTK credentials, A/B test fixture
  provenance, source-policy approval or paid model execution on your
  machine. No background provider work is enabled by merging.
- The old direct Collector QV publisher and manifest UI remain available
  for legacy V1 workflows but cannot acquire an already-ACKed SP outbox.
