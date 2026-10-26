# Group C — Integration Boundary v0
Status: GC-MOD-08 ports, PG17 schema, QDP receiver, D1/C0 readers, isolated Decimal math and trusted D1 publication-time evidence CODE IMPLEMENTED. CI PG17 / external producer E2E still NOT VERIFIED; no approved C7 worker, persistence or Analytics publisher.

## Ownership and canonical identity
- D1 videos.id is the only publication ID; source_id TEXT and project_id 32 lower-hex. QDP UUID identifies a preparation run, not a publication ID.
- metric_snapshots is canonical append-only C0 fact storage; metric snapshot ID and exact source_metric_definition + source_metric_version are mandatory.
- Metadata published_at must be tied to source_video_metadata.id; creation/discovery time cannot substitute it.
- Metric config IDs, frozen comparison cohort IDs and human approval evidence are mandatory for C7 complete.
- Group C uses calculated_metrics already defined by 003/004; it must not duplicate the table or rewrite existing applied SQL.

## Incoming ports
- QDP: C readiness/preparation pointer, exact project/video/source/snapshot SHA; durable inbox ACK only after transaction.
- D1 metric reader: real views, interactions, observed_at, definition/version, quality, expiration, prior observation selection. No source-provider requests inside C.
- Cohort builder: creator/niche/engagement_prior peers are compatible, frozen, and exclude target post; min sample reviewed.
- Config approver: alpha>0, Bayesian k>0, prior rates, provenance, 48h half-life, immutable C7 weights must be reviewed; unknown leaves pending.

## Outgoing port
- To Unified Analytics: persisted calculated_metrics ID, snapshot & previous IDs, publication metadata, config/cohort IDs, score_status, four component contributions and source-policy version.
- Analytics PR23 currently contains a pure C scorer; after cross-branch integration it must delegate to Group C rather than silently maintain competing scoring implementations.

## Mathematical constraints
C1=(V+alpha)/(creator_median+alpha); C2 niche optional; C3=(V2-V1)/elapsed_hours; C4 raw engagement = (L+C+S)/V and weighted=(L+1.5C+3S)/V; C5 smoothed Bayesian components; C6 freshness 48-hour half-life; C7=.45*C1+.30*C3+.15*C5+.10*C6.
Breakout Score is NOT normalized or bounded by 100; niche does not contribute. Saves not included in weighted L/C/S. Unknown values NULL, never fabricated 0.

## Source/revision semantics
Same post/source/platform/content type/source_metric_definition+version; increasing actual observation timestamps; publication_at anchored. No self in cohort peers, no stale/unapproved prior or method.
Current source-policy/retention/expiry checked at calculation AND read; corrected/retracted observations invalidate current readable score.

## Integration status
QDP, VC, Analytics remain independently developed; version compatibility must be reverified on each stage, typed unavailable until then.
## GC-MOD-02 concrete interface boundary (still not runtime)

- `intelligence/group_c/ports.py`: `QDPHandoffNotice` is the strict QDP **wire** shape, separate from Group C's `GroupCInputPointer`. A real source/snapshot pointer requires an authoritative QDP + D1 reread.
- `GroupCPorts` requires explicitly injected adapters and a matching typed `IntegrationCapability`. Missing peers => `dependency_unavailable`; wrong port, version or methods => `dependency_incompatible`. Defaults are never treated as ready.
- Seven scoped ports: `qdp`, `d1`, `registry`, `source_policy`, `cohorts`, `config`, `analytics`.
- Adapter interface versions for non-QDP peers are **Group C expected protocols** only, NOT observed or deployed upstream versions.
- `require_all` returns adapters only after version/shape checks; this is NOT a claim of source eligibility, cohort approval, completed processing or durable consumer ACK.
- `AnalyticsPublisher` advertises outbox enqueue of a persisted calculated_metrics ID, never an arithmetic API; production adapters deferred.
- See `docs/GROUP_C_STAGE02_PORTS.md` and `tests/test_group_c_ports.py`.

## Next contracts still unimplemented

GC-MOD-03 owns schema/inbox/outbox evolution; GC-MOD-04 must implement exact QDP delivery/inbox/ACK in a transaction; GC-MOD-05/06 must implement actual D1 provenance reads; GC-MOD-09..12 must prove approval and frozen peer eligibility; GC-MOD-20 persists calculated metrics; GC-MOD-25 transfers them to Analytics. **No code in GC-MOD-02 performs these operations.**

## GC-MOD-03 durable schema (still no operational endpoints)

Migration `056_group_c_runtime.sql` adds `group_c_inbox`, `group_c_inbox_conflicts`, `group_c_runs`, `group_c_jobs`, `group_c_revisions`, `group_c_outbox`. The inbox requires exact project/video/group C/QDP schema/digests; its accepted state is not source permission approval. A run can be inserted only after an accepted inbox state and exact QDP hash, and jobs are linked to the same project/run/video with lease fencing fields. The outbox permits only persisted matching complete `calculated_metrics` output and immutable payloads. This is a **database structure**, not implementation of QDP ACK, real score computation, worker scheduling or Analytics delivery.

Do not interpret schema objects as live capabilities. `GroupCPorts` defaults to unavailable until real adapters and current source-policy checks are shipped. DB-only PG17 tests are wired into `.github/workflows/group-c-module.yml`; see `docs/GROUP_C_STAGE03_RUNTIME_SCHEMA.md` for exact migration and QA instructions.


## GC-MOD-04 real consumer contract with external producer blocker

`intelligence/group_c/qdp_intake.py` implements `QDPCurrentPreparationAdapter` and `QDPGroupCConsumer` with strict `qdp-handoff/v1` event validation, authoritative QDP `qdp_outbox` existence, `qdp_group_current_preparations` verified C pointer, D1 exact source/time/view-version/policy checks, and atomic inbox/run/job storage. It returns `ack=true` **only after** its PostgreSQL transaction commits successfully on `autocommit=True` connection, and `ack=false` with durable conflict evidence for a mismatched previously accepted event. Same event replay is rechecked against current source rights; no stale ACK. Caller/QDP dispatcher remains responsible for actual upstream delivery receipt **after** successful return. The consumer never updates QDP outbox deliveries itself.

QDP current branch has prepared-view support but a live `preparation_ready` outbox producer to Group C is not yet verified. Until it emits schema-compatible events and owner validates current cross-branch schema, the real integration remains `dependency_unavailable/BLOCKED_EXTERNAL`. See `docs/GROUP_C_STAGE04_QDP_INTAKE.md` and disposable PostgreSQL regression `tests/test_group_c_qdp_intake.py`. No claims of C7 scoring or live source runs.


## GC-MOD-05 canonical D1/project rights readers

`intelligence/group_c/d1.py` contains physically-schema-probed read-only adapters:
`PostgreSQLD1FactsReader`, `PostgreSQLCreatorTopicRegistry`, `PostgreSQLSourcePolicyReader`.
`local_d1_ports()` explicitly injects these into existing `GroupCPorts`. A
`read_scoped_d1_bundle()` requires exact `sre_project_videos` membership
and project_registry identity, `videos.id` publication / creator platform,
an explicitly classified `videos.c_content_type` compatible with known raw
`videos.content_kind`, exact `source_video_metadata.id`, source-policy
revision, source rights and retention, and exact `metric_snapshots.id`
membership / quality / non-assumed observation time. Return state
`source_evidence_verified` is **not** an approved C score.

Stage05 does **not** mutate 001/002/003/004 facts, pick replacement sources,
guess missing views/creator/topics, infer C type from legacy content_kind,
or implement C0 arithmetic quality checking. Stage06 owns C0. Nonempty
peer cohort IDs intentionally block `assert_eligible` until Stage09/10 peer
rights and frozen membership are implemented. See
`docs/GROUP_C_STAGE05_D1_PROVENANCE.md`.


## GC-MOD-06 C0 raw observations (no downstream score yet)

`intelligence/group_c/c0.py` defines immutable `C0Snapshot` / `C0Pair`, pure
`validate_c0_snapshot` and `validate_c0_pair`, plus read-only
`read_c0_pair` under a top-level PostgreSQL repeatable-read, read-only
transaction. Validates exact positive IDs and same video/source/policy/view
definition+version; raw nullable PostgreSQL BIGINT counters (NULL != 0);
precise and non-assumed timestamp (observed <= collected <= now), live expiry,
strictly increasing observations and non-decreasing views. Raw views=0 is a
legitimate observation but cannot be used as an engagement denominator.

D1 port is extended with `read_recent_raw_observations`, selecting **latest
unfiltered source observations**; a new corrected/retracted/provider-version
observation cannot be silently hidden to reuse an older valid QDP pair.
The source still must be eligible per Stage05. Stage06 `C0Pair` is NOT an
approved `complete` C7 score: publication evidence (Stage08), approved
peer cohorts/config (09–12), pure decimal scorer (07,13–19), and persistence
(20) remain separate. See
`docs/GROUP_C_STAGE06_C0_SNAPSHOT_VALIDATION.md` for raw fields, statuses,
unit/PG17 evidence and source-view semantics.


## GC-MOD-07 pure arithmetic ownership, NOT runtime enablement

`intelligence/group_c/pure_math.py` now owns the future Group C raw-formula
calculation through `compute_c_metrics(current,previous,publication,config,creator,prior,niche=None)`.
This is the port of the Analytics branch reference `intelligence/analytics/metrics.py`,
with frozen 27-field parity, decimal 12-place `ROUND_HALF_UP`, exact microsecond
timedelta, explicit alpha/k/priors, fixed 0.45/0.30/0.15/0.10 weights and 48h
half-life, plus NUMERIC(30,12) overflow rejection. C2 niche is optional and does
not contribute to C7; C7 is unbounded RAW composite, never normalized or clipped.

**Important:** the pure return fields `score_status=complete` and
`calculation_status=complete` are mathematical outputs only. This function
does NOT validate trusted frozen cohort/approval records, QDP/D1 currentness,
publication metadata authenticity, source retention or transactional writes.
It is deliberately NOT exported as a root-level `intelligence.group_c.compute_c_metrics`,
nor invoked by any Group C worker in Stage07. Only GC-MOD-08..21 can introduce
the proof+pipeline wrappers; external Analytics must delegate to the Group C
implementation after coordinated merge (GC-MOD-25) to remove duplicate
production scoring code. See `docs/GROUP_C_STAGE07_PURE_DECIMAL.md`.

Existing `003/004` historical SQL and Stage03 `056` runtime SQL remain
unaltered. No new migration, provider call, worker or external branch edit.


## GC-MOD-08 pinned source publication-time evidence

`intelligence/group_c/publication_time.py` adds the pure
`validate_publication_time`, the transactional PostgreSQL
`read_publication_time`, and the immutable `PublicationTimeEvidence`
(`group-c-publication-evidence/v1`). It requires exact canonical
`source_video_metadata.id` matching D1 `video_id` / `source_id` /
`source_policy_version_id`, `is_current`, nondeleted, nonexpired,
timezone-aware `published_at`, source `fetched_at` and strictly valid
observation interval. It never synthesizes published_at from
`videos.published_at`, fetched_at, discovery time or wall-clock `now`.

Age is pinned to `current.observed_at-published_at`, not the read time;
microsecond-accurate stable Decimal hours and provenance SHA are provided
for future C6. The current database reader uses one independent
`REPEATABLE READ, READ ONLY` transaction, reusing GC-MOD-05 project/source
permission and retention checks. The pure test clock can be injected,
but production DB calls always use a current UTC clock and reject an
external historical clock override. This is *evidence*, not a C6 score
or authorization to publish C7. See
`docs/GROUP_C_STAGE08_PUBLICATION_TIME.md`.

No new tables, no edits to applied migrations or peer branches.
