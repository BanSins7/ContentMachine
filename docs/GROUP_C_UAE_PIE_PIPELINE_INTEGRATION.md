# Group C → UAE → PIE: staged working integration boundary

**Branch:** `integration/group-c-uae-pie-pipeline`  
**Parent:** `integration/uae-pie-pipeline` (draft PR #40); **main remains unchanged**.  
**Group C source:** `feature/group-c-full-module` pinned at `521ff74c9eda0279fc1318be7d187de3b44ffcf0`.

## End-to-end flow

```text
Current QDP C preparation + D1/source-policy + frozen reviewed cohorts
                       |
                       v
Group C worker -> persisted calculated_metrics C7 (only Group C calculates)
                       |
                       v
group_c_outbox pending, original certified metric ID + event SHA
                       |
            SERIALIZABLE transaction
                       |
       GC24 latest rights / retention / peer check
                       |
                       v
UAEGroupCTransactionalSink -> uae_group_c_receptions (immutable, unqualified)
                       |
             idempotent durable receipt
                       |
                       v
Group C outbox delivered (same transaction / commit)
                       |
        fresh REPEATABLE READ snapshot / QDP check
                       |
                       v
UAEReceivedGroupCReader -> UAEPorts.group_c
                       |
                       v
JoinedContextAssembler -> UAE qualification (SRE/QDP/A/B/C/D1/rights)
                       |
                       v
UAE authorized aggregates and exact signed dataset -> PIE admission
```

**Important:** The receipt field `analytics_snapshot_id` in Group C's pre-existing ACK contract is a positive, durable **UAE Group C reception ID**, not the ID of a `uae_unified_snapshots` row. A received C7 is *not* a qualified video. UAE's final independent gate decides whether a unified snapshot can be `qualified`. The reader does not return C7 values publicly or make a statistical inference.

## Files connected

- `intelligence/group_c/`: full standalone C modules, outbox, C7 arithmetic, current-rights and source retention.
- `intelligence/migrations/056..064_group_c_*.sql`: forward-only Group C schema after prior QDP/A/B prerequisites.
- `intelligence/migrations/091_uae_group_c_receptions.sql`: exactly-once durable receipt and immutable identity, no public GRANT or second score table.
- `intelligence/group_c_uae_pipeline.py`: `UAEGroupCTransactionalSink` (actual Group C analytics port), `deliver_group_c_to_uae` (actual GC transactional dispatcher), `UAEReceivedGroupCReader` (current-rights-scoped UAE group_c port), `build_uae_ports_for_group_c`, `build_joined_group_c_assembler`.
- `intelligence/unified_analytics/joined_context.py` and `worker.py`: opt-in `group_c_source_ports` threaded through the **actual fenced UAE qualification worker** into the joined readers. Default remains fail closed; final `worker_unapproved_qualification_denied` release guard is intentionally intact. Full UAE QDP/SRE/A/B source verification still applies.
- `tests/test_group_c_uae_pipeline.py`: synthetic receipt/replay, bad digest, missing rights/ports, dispatcher integration, migration invariants and optional disposable PostgreSQL17 real Group C→UAE delivery.
- Unchanged existing `intelligence/uae_pie_pipeline.py`: UAE's separately verified research export and PIE signed intake.

## Developer wiring (explicit, no implicit scheduler or credentials)

```python
from intelligence.group_c.d1 import local_d1_ports
from intelligence.group_c_uae_pipeline import (
    deliver_group_c_to_uae, build_joined_group_c_assembler
)

source_ports = local_d1_ports()  # real D1/permissions adapters; no fake approvals

# On a dedicated autocommit=True PostgreSQL connection with actual migrations:
receipt = deliver_group_c_to_uae(
    conn, project_id=project_id, source_ports=source_ports,
)

# For each project/video/current pins, inside UAE's fresh qualification workflow:
assembler = build_joined_group_c_assembler(
    conn, scope, tuple_of_verified_candidate_pins,
    as_of=now_utc, source_ports=source_ports,
)
# assembler performs the usual SRE/QDP/A/B/C/source joins and gate checks.
# A Group C receipt alone cannot qualify.
```

Do not run `deliver_group_c_to_uae` in an HTTP request handler without an independently reviewed project authorization and worker policy. No provider/API/LLM calls or UI event automatically enqueue delivery. Group C and UAE must use the **same PostgreSQL database connection and transaction for the ACK**. Do not replace this with an unverified HTTP ACK.

## Test commands (local)

```bash
python -m compileall -q intelligence/group_c intelligence/unified_analytics intelligence/patterns_insights intelligence/group_c_uae_pipeline.py intelligence/uae_pie_pipeline.py
python -m unittest discover -s tests -p 'test_group_c_uae_pipeline.py' -v
python -m unittest discover -s tests -p 'test_group_c_*.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
python -m unittest discover -s tests -p 'test_pie_*.py' -v
# Apply forward-only migrations to a disposable PostgreSQL17 database first.
# Enable PG tests only for a DISPOSABLE PG17:
# CM_TEST_PG_DSN=... GC_TEST_DISPOSABLE_DB=YES python -m unittest discover -s tests -p 'test_group_c_uae_pipeline.py' -v
```

Tests were **authored, not executed** in the GitHub connector session; a PostgreSQL17 local result cannot be claimed. Respect existing migration checksums, ordered 056..064 → 080..091 installation and prior installed project tables; migration 091 is additive. Do not amend applied migrations.

## Admission and release blockers

1. Real Group C complete run requires QDP C readiness, exact source/snapshot provenance, frozen and reviewed cohorts, activated C configuration and trusted human approval. Synthetic or unapproved cohorts do not suffice.
2. `UAEReceivedGroupCReader` rechecks current D1/source/retention, QDP C preparation, original C7 receipt and delivered event in a consistent DB snapshot, then yields **only a typed evidence claim**. Full UAE joined qualification separately requires SRE, A, B and source rights, not just C7.
3. UAE's actual `QualificationWorker` still intentionally rejects a positive `qualified` decision (`worker_unapproved_qualification_denied`) even when source evidence is complete. A separate reviewed production authorization/release stage must explicitly resolve this invariant; merely receiving C7 does not grant that approval. UAE's standalone stages still contain historical intentionally blocked positive-qualification paths. This integration adds an *explicit opt-in* Group C reader but does not override other legal/safety/freshness gates. Real positive unified snapshot and successful authorized UAE→PIE research sample remain to be demonstrated.
4. Group C's standalone WSGI HTTP/Studio requires separately provisioned project IAM, CSRF and DB runtime. The standalone UI files are present but are **not automatically mounted/activated**; the backend C7 receipt is internal, not a public HTTP endpoint.
5. PostgreSQL17 lifecycle must cover exact once delivery, replay, concurrent worker fencing, revocation/erasure, mismatched project/source/policy, correct group C score, final UAE qualification and UAE→PIE signed dataset.
6. Confirm existing main's upstream QDP/Group A and later SRE/Group B versions before final merge; no fake provider data or automatic paid features.

**Recommended merge order:** finish review of PR #40 UAE→PIE, then review this stacked Group C→UAE branch; before a final merge to main, run local QA against the combined tip. Do not merge both standalone UAE and Group C branches over the integrated tree.
