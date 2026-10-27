# UA-MOD-19 — Read-side visibility: current rights, exact provenance, no side channels

**Branch:** `feature/unified-analytics-full-module` (only UAE files). **Additive migration:** `intelligence/migrations/089_unified_analytics_readside_visibility.sql`. **Python:** `intelligence/unified_analytics/readside_visibility.py`. **Tests:** `tests/test_unified_analytics_readside_visibility.py` plus `tests/test_unified_analytics_readside_visibility_pg17.py`.

## Security model and reality of the current release

**Important:** baseline UAE migration `085` refuses every `qualified` snapshot insert and every effectively checked/complete evidence insert; `084` allows only partial/blocked immutable payloads. **Accordingly there are no permitted visible qualified snapshots today**. The UA-MOD-19 view/reader implement a *fail-closed read architecture*; they DO NOT remove either guard or silently approve any SRE/QDP/Group A/B/C score. A later independently reviewed producer-approval protocol, schema and end-to-end tests are required before positive data can ever be returned.

`uae_readside_visibility_candidates` is a **SECURITY_BARRIER, SECURITY_INVOKER** PostgreSQL 17 view returning only `(project_id,snapshot_id,video_id)`; it grants no SELECT to public roles and contains no scores, bodies, media paths, prompts, models or aggregate count. It is NOT a substitute for application authentication. The caller must hold an authorized project-scoped DB connection; the user/client cannot dictate project permission through a supplied ID.

### Live SQL conditions, re-evaluated on each statement

The view joins an exact project/video membership, request+QDP scope, immutable snapshot + matching payload, source/policy, current metric observation and SRE project's active taxonomy. Its exclusion predicates cover:

- Status exactly `qualified`, request `resolved`, matching identity/source/QDP/versions, matching immutable payload fields, seven `complete/checked/is_current` evidence ports.
- An enabled source with affirmative source AND reviewed policy permissions, uniquely active policy, canonical project/video membership, positive bounded approved derived retention, current time not beyond observations/derived retention, exact metric source/version/quality/precision/expiry.
- Exact A/B/C QDP handoff receipts at the pinned run+project+video; these are **necessary but NOT proof** of complete peers.
- A current matching SRE taxonomy pin, actual A/B/C stored IDs aligned with the payload, no recorded invalidation, and no newer source/QDP/SRE/A/B/C revision event or rejected human Group C review after snapshot verification. Late source revocation is conservatively excluded even if its event predates verification.
- No joined feature-peer tables are assumed installed. The view can only reduce the candidate pool; it cannot independently authenticate all upstream runtime peer states, media rights or exact effective C review. It is therefore never a final publication view.

No materialized copy is created, and historical rows are not rewritten after revocation/expiry. This view must **NOT be granted** to an end-user role without an independently verified authorization/ownership boundary.

### Internal Python reader

`VisibilityPins(snapshot_id, JoinedCandidatePins)` is a server-provided exact pinned proposal, **not** a user permission. `VisibleSnapshotReader(conn).visible_one(pins)` and `.visible((pins1,...))` accept at most 50 distinct same-project requests; they never enumerate arbitrary projects or return denied counts.

Within a single new PG `REPEATABLE READ` transaction the reader:

1. Looks up only the exact snapshot ID/project/video in the internal SQL candidate view; absent means `None`, not an explanatory user-visible record.
2. Re-runs `SourcePolicyCurrentReader.check_current_rights` using independently supplied exact source **metadata ID, every metric observation ID, and every media ID**, enforcing live source/policy versions, expiry and all required permissions. UAE immutable snapshots do NOT currently store the complete metadata/media pin list; no IDs may be synthesized from a historical score or media count.
3. Rebuilds the **full** `JoinedContextAssembler` selection from real source-backed SRE/QDP/Group A/B/C/D1 readers with exact versions; compares source-rights hash and candidate fingerprint. `QualificationOrchestrator` must issue a *real approved qualified decision*, not `checked=True` from caller JSON. The present UA13 gate always returns unapproved, so the reader returns `None`.
4. Only if effective approval exists, checks canonical scope/decision and payload SHA, exact pinned taxonomy, preparation/run IDs, A/B/C configuration/score IDs and immutable seven-row verified evidence matrix. Missing data, wrong version, stale rights, payload tampering and any invalidation deny the result.
5. Rechecks the invalidation ledger before returning a **minimal `VisibleSnapshot(project_id,snapshot_id,video_id)` pointer**. No raw payload, scores, text, media, user-facing aggregate or Patterns publication is emitted.

**Concurrency boundary:** reads see a consistent authorized PG transaction snapshot. A policy change/producer revision *committed after that snapshot* is not retroactively part of it; a new read requires a new transaction and re-evaluation. UA-MOD-16 revision events invalidate histories, but pending outbox delivery does not authorize any read. Future API/Studio must separately enforce authenticated project membership and appropriate DB permission isolation. Without that, no public endpoint should expose these IDs.

### Verification and remaining blockers

Authored 19 pure/mock/static tests and three conditional isolated PostgreSQL 17 tests. Tests include an intentionally synthetic future-positive fixture **only for validating rejection of malformed provenance**; it does not claim current UAE has valid positive SRE/QDP/A/B/C approvals. PG fixtures confirm partial snapshot visibility stays empty, source revocation and invalidation never unhide, and the UAE85 forged-qualified trigger remains enabled.

```bash
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Real PG17 requires CM_TEST_PG_DSN pointing only to a disposable PG17.
```

Static source checks may pass when conducted, but they are **not** executed Python or PostgreSQL results. Until tests actually run: migration 089 PG17 compatibility, source/media expiry races, pending peer schemas, approved C review, LIVE SRE and Group B qualification, current project-role authorization and future user API privacy must remain separate release blockers. No existing historical `080..088` migrations are edited; main, PR23, peers and public permissions stay unchanged.

**Next planned module:** UA-MOD-20 — descriptive aggregation baseline (must consume only successfully authorized qualified reads, implement sample-size gates, and never publish stats for hidden/partial rows).
