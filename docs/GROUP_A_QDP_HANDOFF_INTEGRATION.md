# GA-MOD-04 — QDP v1 durable handoff and inbox

**Implementation status:** IMPLEMENTED (independently testable QDP inbox consumer).  
**Runtime status:** OFF by default. No host route, background worker or analysis dispatch.  
**QA status:** structural PASS; full Python/PG17 runtime test suite QUEUED.

The source of truth for the upstream counterpart was inspected on QDP branch
`feature/qualified-data-pipeline-v1-20261023` (head during design `3f528af`):
`intelligence/qualified_data/integrations/contracts.py` declares
`QDPHandoffPointer`; `040_qdp_integration_foundation.sql` declares
`qdp_outbox`, `qdp_outbox_deliveries`, `qdp_delivery_receipts` and
`qdp_invalidations`. These are **upstream-owned** tables. Group A migration
054/055 creates none of them.

## 1. Exact incoming envelope and digest

An upstream QDP outbox row must be durable, for `consumer='group_a'`,
`event_type='preparation_ready'`, `analysis_group='A'`,
`schema_version='qdp-event/v1'`, with `run_id`, `video_id` and project
matching the pointer. Its **payload JSONB** uses the nested envelope:

```json
{
  "schema_version": "qdp.preparation_ready/v1",
  "pointer": {
    "schema_version": "qdp-handoff/v1",
    "event_id": "11111111-1111-4111-8111-111111111111",
    "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "qdp_run_id": "22222222-2222-4222-8222-222222222222",
    "video_id": 123,
    "analysis_group": "A",
    "input_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}
```

`event_id` is the SAME ID as `qdp_outbox.event_id`. The QDP producer
must SHA-256 the strict canonical JSON UTF-8 encoding of this envelope
(sorted keys, no whitespace, no nonfinite values), and store/pass it as
`qdp_outbox.payload_sha256`. The Group A receiver validates both schema
and supplied payload SHA before opening a DB transaction. Hashes and UUIDs
must be lowercase canonical strings. Unknown versions, extra fields, missing
fields and group B/C handoffs are rejected.

Static schemas:
- `schemas/qdp-preparation-ready-v1.json`
- `schemas/group-a-receipt-v1.json`
- existing `schemas/qdp-handoff-v1.json` and `media-manifest-v1.json`

The upstream QDP branch currently declares the durable outbox contract,
but its actual outbox producer/delivery wiring must be verified during
integration. **An outbox table merely existing does not produce an event.**

## 2. Current authoritative QDP checks

`adapters/qdp.py::PostgresQDPReader` is an opt-in public-SQL adapter.
It does NOT import any `intelligence.qualified_data` private code.
It requires explicit version negotiation for `qdp-handoff/v1`; no default
version may be guessed. It reads (with row-share locks) the current:
- Group A `qdp_preparations` keyed by exact project/run/video/input_sha256,
  `state='ready'`;
- QDP run/item statuses and currently committed enrichment selection;
- matching QDP outbox event, `qdp-event/v1` and exact canonical payload SHA;
- QDP delivery row to `group_a`, not `dead_letter`;
- absence of QDP invalidations matching the run/video/group;
- D1 media-asset ownership, hash, permission and expiry;
- enabled source and active matching source-policy version with current
  media analysis permission.

The reader returns the bound **source ID, policy version ID and QDP-selected
media_asset_id**. Those values MUST equal the verified media manifest's
source, policy and at least one exact asset. A different file belonging to
the same video is NOT an acceptable substitute.

`CallbackQDPReader` exists only for a host with a genuine authoritative
public QDP read API and for offline fake-adapter tests. It must supply the
same five-field response
`{pointer,state,source_id,source_policy_version_id,media_asset_id}`.
Its callback is never implicitly trusted or installed.

## 3. Transactional receive and ACK contract

`GroupAInboxReceiver.receive(envelope, payload_sha256)` takes explicit
dependency factories and explicit
`GASettings(enabled=True,qdp_handoff_enabled=True)`; default flags
remain false. It opens **one PostgreSQL transaction**, verifies GA055 is
installed, serializes by incoming event UUID using a transaction-scoped
advisory lock, and atomically:
1. Re-reads the QDP public records + verifies the independently supplied
   `ga-media-manifest/v1` against project/video/QDP input/media selection.
2. Creates or reuses a `ga_runs` pinned to QDP hash + media manifest SHA +
   deterministic intake config SHA. Never creates another D1 video.
3. Inserts exact ordered `ga_media_refs`, checking the D1 asset and pin
   integrity, with one unique run/media ID and ordered slide index.
4. Inserts one `ga_steps` queued `initial:v1` job (technical, slides or
   mixed slides depending on verified media kind).
5. Inserts immutable `ga_inbox` event ID/payload SHA/pointer.

**Only after the transaction successfully commits** does it return
`group-a-receipt/v1` containing `state='recorded'`, stable `run_id`,
`job_id`, `payload_sha256`, `replayed` and
`analysis_state='not_started'`. Never interpret a receipt as completed A.

Replay rules: identical event ID + identical payload and pointer returns
the original run and job (`replayed=true`) without enqueuing twice.
Different event ID with the same canonical run inputs can reuse the run
and existing initial job, but gets its own immutable inbox receipt.
Same event ID + different valid payload hash or pointer is quarantined
in `ga_inbox_conflicts` (forward-only migration 055), commits that
conflict evidence and raises `event_id_conflict`, with **no ACK**.
An envelope whose claimed SHA doesn't match its bytes is rejected before
opening the database. Writes that fail at any step rollback the run,
media pins, queue entry and inbox row together.

If any required QDP/public delivery or media dependency is missing,
incompatible, revoked, expired or structurally invalid, no job is
accepted. Neither QDP tables nor paid acquisition calls are created.

A prior ACK remains historical evidence of committed intake even if
the source is later revoked. **Executors must re-verify QDP/media policy
before processing** (GA-MOD-20), and may not infer current rights from
the receipt alone. New events after invalidation are rejected.

## 4. Host integration (example, NOT automatic startup)

```python
from intelligence.group_a import GroupAService, GASettings
from intelligence.group_a.adapters.qdp import PostgresQDPReader

service = GroupAService(settings=GASettings(
    enabled=True, qdp_handoff_enabled=True
))

# connect() must yield a disposable / configured PostgreSQL connection.
# catalog_factory(conn) MUST be a real verified MediaCatalog;
# GA-MOD-05 has not yet installed its byte-verifying implementation.
receiver = service.make_qdp_receiver(
    connect=connect,
    qdp_factory=lambda conn: PostgresQDPReader(
        conn, observed_version="qdp-handoff/v1"
    ),
    media_factory=catalog_factory,
)
ack = receiver.receive(qdp_outbox_payload, qdp_outbox_payload_sha256)
```

The Group A module deliberately does **not** insert a QDP delivery receipt
in a QDP-owned table. The QDP delivery host should consume this committed
ACK, then record `qdp_delivery_receipts` and
`qdp_outbox_deliveries.state='acknowledged'` using QDP's own lease-fenced
API. This avoids impersonating the upstream dispatcher or holding an open
distributed transaction. Never claim cross-process exactly-once delivery;
the guarantee here is durable idempotent downstream intake.

No built-in media acquisition/AI calls, job-worker activation, public
HTTP route, or Group B/Analytics publishing has been enabled. Those are
GA-MOD-05..24 responsibilities.

## 5. Tests and integration gaps

- `tests/test_group_a_handoff.py`: strict event/ACK JSON Schemas, invalid
  hashes, foreign project, disabled flags, incompatible versions, missing
  outbox, tampered media hashes, conflicting QDP-selected asset/policy,
  revoked prep, append-only conflict log and replay.
- Disposable PostgreSQL17 integration includes two concurrent deliveries
  of the same UUID, distinct event IDs for the same run, a failing queue
  trigger that must rollback *all* rows, a public QDP outbox stub consistent
  with QDP's observed DDL, and post-accept QDP invalidation.
- `tests/test_group_a_migration_db.py`: upgrades archived A1 through GA
  054 and GA 055 in strictly increasing order.
- `.github/workflows/group-a-module.yml`: PG17 + psycopg/jsonschema,
  runs every GA unit/DB test and now reacts to either GA-only migration.

**STATIC structural verification PASS**, but **Python/PG17 execution
QUEUED/NOT RUN**: GitHub Actions runners have not started any relevant
Group A jobs. QDP peer producer/dispatcher, true media byte resolver and
host wiring are NOT integrated into main. This module remains in its own
feature branch, draft PR only.
