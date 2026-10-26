# QDP FIX-05: SRE completed-run event consumer handoff

**Scope:** QDP-only integration adapter on branch `feature/qualified-data-pipeline-v1-20261023`. This is an implementation contract **for a future completed SRE producer**, not a claim that the SRE outbox or an HTTP endpoint already exists.

## Implemented receiver

Entry point: `intelligence.qualified_data.sre_inbox.receive_sre_completion(payload, announced_sha256, *, connect=None)`.

The receiver is intended only for an authenticated internal SRE producer/transport. **Do not expose it directly as an unauthenticated public HTTP API.** It owns the PostgreSQL transaction, performs QDP schema preflight, validates the announced digest, locks the event ID, re-reads authoritative SRE LIVE completed state with exact video×category coverage and evidence, checks the pinned SRE taxonomy and publication, writes accepted or quarantined inbox state, then returns a receipt only after transaction commit. No QDP runs/jobs or provider calls are created here. Explicit user `create_run` remains a separate operation.

### Envelope — SRE-provided `sre-analytics-event/v1`

```json
{
  "schema_version": "sre-analytics-event/v1",
  "event_id": "<canonical UUID>",
  "event_type": "sre.classification_run_completed",
  "project_id": "<32 lowercase hex>",
  "run_id": "<canonical UUID>",
  "publication_id": "<canonical UUID>",
  "taxonomy_version_id": 123,
  "occurred_at": "2026-10-26T09:00:00Z"
}
```

The JSON above is **schema only**, not a real completed event. Supply real values only from SRE's committed run/outbox. The transport's `announced_sha256` is `SHA256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))`. QDP does not accept decisions, video IDs, fake run status, budget grants or labels in this summary.

Do not confuse this with `sre.classifications_ready/v1` in `intelligence/relevance/ports/outbound.py`: that is a **per-video** contract not accepted by this run-completion receiver. SRE must implement/confirm its own real run-completed producer (`SRE S10/M36`) and durable delivery protocol.

### Call/ACK semantics

```python
from intelligence.qualified_data.sre_inbox import receive_sre_completion

receipt = receive_sre_completion(outbox_event_body, outbox_payload_sha256)
if receipt["ack"] and receipt["event_id"] == outbox_event_body["event_id"]:
    # SRE-owned code: commit delivery ACK to SRE's OWN outbox ledger.
    ...
else:
    # SRE-owned code: retain unacknowledged state; escalate quarantine or retry.
    ...
```

- Same ID + same digest after accepted commit → `{"ack": true, "state": "accepted", "replayed": true, ...}`; no extra QDP work.
- Same ID + different digest → `{"ack": false, "state": "quarantined", "reason_code": "event_id_hash_conflict", ...}`; the original accepted row is immutable and a sanitized forensic record is appended to `qdp_sre_inbox_quarantine`.
- Invalid announced hash → `transport_hash_mismatch`, audit only; no accepted inbox row or ACK.
- Valid event with wrong run status, offline mode, wrong taxonomy/publication/project or incomplete SRE coverage → persisted state `quarantined`, no ACK. The producer MUST NOT treat this as success.
- QDP migration/SRE LIVE schema absent → fail closed with a retryable failure, no ACK; the transaction rolls back.
- SQL/connection error or crash before commit → rollback, no ACK. A duplicate after a previous successful commit always replays the durable receipt.
- The receiver never calls an LLM, schedules Group A/B/C or authorizes paid work.

### Install / verify

1. Preserve the applied original `016_qualified_data_pipeline.sql` and QDP `040_qdp_integration_foundation.sql` SHA-256 checksums. Install new forward-only `041_qdp_sre_inbox_quarantine.sql`.
2. Deploy SRE-owned live-mode/content-hash migrations in their own integration window; QDP cannot silently fabricate them.
3. Disposable PG17: `CM_TEST_PG_DSN=... python -m unittest tests.test_qualified_data_db tests.test_qdp_sre_inbox tests.test_qdp_schema_migrations -v`.
4. Confirm durable replay, hash mismatch quarantine, foreign project, failed/offline SRE, crash before/after commit, concurrent duplicates, no `qdp_runs` or `jobs` side effects.
5. Once SRE has a genuine producer, run one real completed-LIVE integration smoke against a disposable combined-schema PostgreSQL, preserving evidence and exact commit SHAs.

## Ownership / remaining stages

- **QDP FIX-05 owns:** receiver, canonical digest validation, authoritative reread, durable inbox & conflict journal, post-COMMIT ACK, QDP-side tests.
- **SRE owns:** live classification result producer, transactional SRE outbox, authenticated internal delivery, replay scheduling/backoff and marking its own delivery acknowledged.
- **QDP FIX-06 owns:** pinning the run manifest, materializing user-explicit QDP runs/jobs.
- **QDP FIX-12 owns:** QDP → Group A/B/C consumer delivery and receipts.
- **QDP FIX-15 owns:** processing SRE invalidation/rights-revocation events and current-read invalidation.

**Current status:** Code and regression tests committed, real SRE producer and exact-SHA CI/PG17 acceptance remain unverified.
