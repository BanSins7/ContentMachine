# UAE UA-MOD-07 — QDP Qualified Set, A/B/C preparations and durable handoff

**Branch:** `feature/unified-analytics-full-module` ONLY. **Status:** source code/migration/test implementation committed; Python/PG17/real QDP deployment QA must be reported separately.

## Source audit and exact contracts

At this stage QDP peer branch `feature/qualified-data-pipeline-v1-20261023` was `e6f395e0e20bec04ec534705a4aff3bf663023d8`, **read only**. Audited `016_qualified_data_pipeline.sql`, `040_qdp_integration_foundation.sql`, `041` quarantine, `042_qdp_group_current_preparations.sql`, `043_qdp_metadata_readability_fix.sql`, `044/045` delivery journals, QDP `contracts.py`, `run_manifest.py`, `qualification.py`, `preparation.py` and `integrations/contracts.py`.

**Three important independent SHA namespaces:**

- `InputScope.qdp_input_sha256` = `qdp_runs.input_sha256` (the qualified **run's** immutable selection fingerprint).
- `QDPQualifiedPins.qdp_manifest_sha256` = `qdp_run_manifests.manifest_sha256` (full QDP run-manifest checksum), with a separate `selected_sha256`.
- `QDPQualifiedPins.preparation_shas = (sha_A, sha_B, sha_C)` = the **three distinct group input digests** in `qdp_preparations.input_sha256`, checked again through the **current-rights-aware** `qdp_group_current_preparations` view and recalculated from `inputs` JSON using QDP's canonical JSON SHA256 serializer.

Do **not** silently treat one SHA as another. A delivered `preparation_ready` `qdp-event/v1` contains the **group-specific** SHA and a separate immutable outbox payload digest. A QDP `qdp-handoff/v1` pointer is merely proposed input identity. A delivery receipt/ACK attests to a committed consumer inbox, **NOT** the consumer's completed Group A/B/C analysis.

## Read adapter

`intelligence/unified_analytics/qdp_adapter.py` owns `QDPQualifiedPins` and `QDPQualifiedSetReader`. This reader is injected with an **authorized dict-row DB connection** used inside a caller-managed repeatable-read transaction with explicit UTC `as_of`. It never connects automatically, invokes QDP Python runtime from a peer branch, modifies other modules, starts jobs or rescales Group C metrics.

1. `capability()`: probes the exact QDP table/view topology and SRE classification tables. Missing standalone-branch QDP 016/040/042/043 gives `PortCapability.UNAVAILABLE`, not imaginary support. `UAEPorts.require/read` checks `uae-qdp-reader/v1` and `qdp-handoff/v1` separately.
2. `read_qualified_membership(scope)`: finds exact QDP run/project/video (not latest by timestamp), completed and noncancelled run, qualifying item with `status=ready`, positive `total_qualified`, exact source/policy/content kind and pinned metadata observation.
3. Verifies the QDP SRE source run is actually `execution_mode=live`, completed, linked to same project and approved active taxonomy. This is still not the SRE live receipt proof required in UA-MOD-06: the upstream LIVE SRE M13 settlement gap remains a production blocker.
4. Recomputes immutable QDP manifest SHA and matches project/SRE/taxonomy/selection, expected video/leaf counts, and *this* video's qualified state, exact SRE content ID/SHA, source/policy/metadata IDs and positive selected category/result IDs.
5. Checks the **whole qualified-run** SRE/QDP audit matrix coverage (`count(*), count(distinct video_id), count(qdp_items)`) against QDP run-manifest totals. A single seemingly positive video cannot hide missing negative/reviewed SRE leaves.
6. Uses the current QDP rights/readability views and explicit invalidation ledger. No revoked group, expired D1 source metadata, stale QDP item, cancelled run or invalidated video can produce `EvidenceRead.LOCATED`.
7. Reads **exactly three** unique current A/B/C `ready` preparations for that run/video. Recomputes each individual QDP input SHA, checks common SRE discovery/content/source/policy/metadata, and pins A/B to the **same media asset, SHA and byte size**. Group C gets independent rights and two exact eligible metric snapshot IDs, metric-history digest and a saved scoring config ID.
8. Re-reads SRE result lineage for the whole video (including `no_match` leaves), verifies result ID, SRE run, category, immutable content ID/SHA, evidence SHA and effective decision. Requires all current human-label IDs/decisions to match QDP's stored snapshot: superseded positive corrections deny previously qualified membership. The exact positive leaf set/results must equal pinned QDP member.
9. Returns only a typed `EvidenceRead.LOCATED` with matching QDP port/scope/wire and an immutable fingerprint; this alone **does not** qualify the UAE publication or assert A/B/C completed output.

**Known upstream gap:** QDP `042_qdp_group_current_preparations` gates A/B on `media_kind='video'` and `videos.content_kind IN ('video','slideshow_video')`. Thus QDP cannot currently issue fully current A/B/C preparations for `photo_carousel`/`mixed_carousel`; reader will not bypass or fabricate carousel rows. Track this as a peer dependency until QDP extends an authoritative carousel-aware handoff.

## UAE-owned durable idempotency: migration 082

`intelligence/migrations/082_unified_analytics_qdp_handoff_inbox.sql` adds only:

- `uae_qdp_handoff_inbox`: immutable event UUID PK; project/run/video/group, exact group-input digest, original QDP outbox payload SHA, and verified evidence lineage digest. Composite FK `(project_id,video_id)` references canonical `sre_project_videos`. No jobs or analytics records created.
- `uae_qdp_handoff_quarantine`: append-only, keyed forensic observations of reused QDP event IDs with conflicting SHA/group/source identity; no raw media/transport secrets stored.

`QDPHandoffEvent` follows the versioned `qdp-handoff/v1` pointer. `register_verified_handoff(conn, reader, event)` requires:
- same authorized connection/scope and current `QDPQualifiedSetReader` located membership;
- event's **group input SHA** equals that exact current group's prepared SHA;
- original persisted `qdp_outbox` row exists with exact event ID, project/run/video/group, event_type=`preparation_ready`; the original `qdp-event/v1` JSON body and SHA must recompute correctly and the payload's group-input SHA match;
- one DB transaction for all checks and the `INSERT ... ON CONFLICT DO NOTHING` claim; concurrent identical claims resolve by authoritative PK to `accepted` or `replayed`;
- a different observed payload SHA for the same event ⇒ `quarantined` with `event_payload_collision`. A stored event identity/group/input digest mismatch ⇒ `quarantined` with `event_preparation_collision`. Quarantine results should **commit** as a forensic record; do not throw and roll back afterward. Unqualified/unknown QDP events are rejected outright.

This journal does **not** claim that the QDP worker delivered successfully, nor that A/B/C has finished. A trusted caller/DB role and actual QDP tables must be available; any caller-supplied event is untrusted until source-outbox DB recheck. The stage does NOT expose an unauthenticated API route.

## Verification scope and test limitations

`tests/test_unified_analytics_qdp_adapter.py` covers mocked future/deployed QDP-positive membership, exact no-match negative leaves, invalidation and TTL, source/policy/project mixups, QDP status not completed, SRE legacy/offline vs LIVE, duplicate/missing A/B/C or conflicting media, wrong run/group SHA, stale effective human review, global missing SRE pair, malformed inputs, missing peer tables, outbox forgery, exact replay and conflicting event ID SHA quarantine. The PG17 disposable test runs the standalone `main + UAE 080/081/082` migration set and checks QDP tables are unavailable while UAE inbox/quarantine exist.

Required commands:
```bash
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
python -m compileall -q intelligence/unified_analytics
```

**No actual Python, GitHub CI, PostgreSQL 17 integration, or source-backed LIVE QDP E2E result is claimed until its output is observed.** The positive mocked QDP fixtures simulate both independent peer-branch tables and hypothetical complete LIVE SRE; they are **not** proof of a working production integration. No peer migration is copied into the UAE branch: this is intentional independence and a mandatory fail-closed capability report before merge.

## Next

**UA-MOD-08 — Group A exact-version verified facts reader.** Consume A's real completed media analysis only through pinned run/record/source-policy/content-version and QDP A preparation; reject partial coverage, missing media, wrong manifest, unknown carousel signals. QDP must publish current verified carousel readiness before carousel is eligible.
