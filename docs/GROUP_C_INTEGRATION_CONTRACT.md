# Group C — Integration Boundary v0
Status: GC-MOD-26 standalone project-scoped authenticated Group C WSGI HTTP surface CODE IMPLEMENTED; exact-head PG17/CI NOT VERIFIED; production IAM+CSRF, TLS/server mounting, DB role hardening, external Analytics PR23 scorer replacement and E2E BLOCKED_EXTERNAL.

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


## GC-MOD-09 source-compatible peer strata (unapproved candidates)

`intelligence/group_c/cohort_rules.py` provides immutable `CohortRules`
(`group-c-cohort-rules/v1`), pure
`select_compatible_peers(rules, rows) -> CohortSelection` and the strictly
read-only `PostgreSQLCohortCandidates.select` DB integration.
`CohortSelection` has `status=candidates_only, approved=False`; it contains
admitted *exact* video/snapshot/metadata identifiers and typed reasons for
rejected candidate observations. It contains NO medians/priors/approved scorer
input, cannot automatically freeze or persist a comparison_cohorts row.

All creator/niche/engagement-prior cohorts must explicitly specify
platform/content type, project, creator and/or topic stratum, exact source
and policy revision, exact `platform_post_views` definition + metric
**version**, UTC publication/observation cutoff, explicit publication time
and observation-age windows and bounded candidate population. Target self
is excluded twice (SQL and pure), project association
`sre_project_videos` must be authoritative, and latest raw snapshot is
chosen *before* testing quality/metric version (no silent older good
fallback). Canonical D1 source rights and metadata/metric retention are
rechecked per selected peer within one `REPEATABLE READ, READ ONLY`
transaction; selection never starts provider calls or modifies D1.

Historic `comparison_cohorts` lacks version/policy/project lineage;
Stage10 MUST add additive persistence and freeze immutable exact
membership; Stages11/12 MUST require human approval before complete.
Neither Stage09 nor future math stage may silently treat historic cohorts
as valid solely because their purpose/platform match.
See `docs/GROUP_C_STAGE09_COHORT_RULES.md`. CI PG17 status pending.


## GC-MOD-10 immutable frozen cohort parent + exact D1 member provenance

Migration `057_group_c_cohort_freezes.sql` follows 056 and creates
`group_c_cohort_freezes` with exact project, legacy comparison_cohorts ID,
target video/snapshot/metadata, source/policy revision, metric definition +
**metric version**, rules/selection/evidence hashes, estimator version,
member count and immutable frozen_unreviewed state.
`group_c_cohort_freeze_members` stores each exact member
video/snapshot/metadata ID with composite D1 FKs, original observed/published
times, raw views/likes/comments/shares, source/policy/metric definition/version,
and raw evidence digest. Both new proof relations have UPDATE/DELETE guards
and a deferred COMMIT trigger requires the old comparison_cohorts parent
is_frozen=TRUE and every legacy member pair to match counts/exact references.
A new BEFORE INSERT guard on group_c_cohort_freeze_members rejects
post-seal member appends; initial inserts require the legacy parent to still
have is_frozen=FALSE before the final transactional seal. The deferred trigger
at commit then proves exact membership counts and parent seal.
Historical 003/004/056 migrations are unchanged.

`PostgreSQLCohortFreezeWriter.freeze` requires an independent autocommit
connection, owns a SERIALIZABLE transaction, and calls GC-MOD-09
`PostgreSQLCohortCandidates.select_locked` to re-check exact candidate
membership, current source rights/TTL, target age and source/metric version
*inside the same transaction as writes*. It inserts parent + old members +
057 proof rows then marks the old cohort frozen. Replays with identical
rules and evidence return the exact prior freeze; changed selection/evidence
for the same rules hash blocks as `cohort_selection_conflict`.

Pure `build_frozen_evidence` computes only observed median views for
creator/niche and empirical count-pooled like/comment/share rates for
engagement_prior, quantized to NUMERIC(30,12). It never substitutes missing
observations, alpha, k or default priors. **Frozen is not approved**:
`CohortFreezeReceipt.status='frozen_unreviewed', approved=False`, and no
Group C C7 result is written. GC-MOD-11 must add human reviews of exact
frozen cohort evidence, while GC-MOD-12 activates the reviewed config.
Future Stage24 owns lawful retention/erasure paths for otherwise immutable
cohort proofs. See `docs/GROUP_C_STAGE10_FROZEN_COHORTS.md`.

CI upgrades now install both 056 and 057 into disposable PostgreSQL17 and
run the new Stage10 tests; status NOT VERIFIED until completed CI evidence.


## GC-MOD-11 exact signed reviewer requests / decisions

Migration `058_group_c_human_reviews.sql` extends 057 additively with
`group_c_review_requests` (exact project, video, source/policy,
source metric version, config id/version/alpha/k, reviewed creator/niche/
prior freeze IDs, reviewed sample minima, canonical review subject SHA/payload)
and `group_c_review_decisions` (append-only explicit `approved/rejected`
decision, human reviewer ID, external identity provider, unique signed
authentication event, attestation digest and reviewer note). Both relations
reject UPDATE/DELETE. DB insert triggers verify exact immutable config input
and all required frozen cohorts, project/source/version, member counts and
request/decision binding; neither invokes config activation nor score writes.

`PostgreSQLHumanReview.create_request` creates an idempotent
`pending_human_review` record, checking current D1 source and member rights
under a serializable transaction. Pure `build_human_review_subject` includes
**all** calculation configuration inputs: exact version and positive alpha/k,
fixed C weights/48h half-life, unactivated flag, prior_source_policy,
cohort_selection_rules and growth_policy JSON, each frozen cohort's rules/
selection/evidence SHA, original empirical stats, sample count/minima,
source policy/metric version and target. Changing the subject requires a
new review, never inheriting a prior approval.

`PostgreSQLHumanReview.record_decision` revalidates current D1 rights
and exact payload SHA and mandates a trusted external
`HumanReviewerVerifier.verify(...)` for a signed, project-scoped *human*
reviewer action. There is **NO** built-in provider, fake reviewer or
autoapproval. Same signed decision replay is idempotent; any different
outcome or attested identity/event/note is a conflict. `read_status`
reports whether human decision evidence exists, but *does not* activate
config or independently authorize `calculated_metrics.score_status=complete`.
A production IAM signature verifier and restricted SQL write privileges
remain external deployment gates and must not be claimed as implemented.
See `docs/GROUP_C_STAGE11_HUMAN_REVIEW.md`. GC-MOD-12 activation must
revalidate signatures, exact human decision and current source/cohort policy.


## GC-MOD-12 reviewed config activation / global one-active invariant

`metric_calculation_configs.is_active` remains a globally unique boolean
under historical `003` (not project-local). Stage12
`PostgreSQLConfigActivation.activate` requires canonical project/review UUID,
expected config ID and **explicit** `replace_active_config_id` matching the
current global active version, or NULL if none is active. It reconstitutes
the exact Stage11 human-reviewed config and frozen cohort evidence SHA,
checks D1 source rights/retention for target and every frozen member and
mandates a trusted external `HumanApprovalAttestationVerifier` to
re-verify the prior signed human IAM decision/grants. Absent verifier,
unapproved/rejected request, changed config, changed cohort evidence or
revoked provider permissions => no activation.

The one `SERIALIZABLE` transaction updates ONLY `is_active` on old/new
configs and writes `group_c_config_activation_events` immutable
`superseded`/`activated` audit records. Additive 059 guards on the
historic config table require matching *current transaction XID*
approved review events for **any** active toggle or initially-active
config INSERT. Audit events cannot be UPDATEd/DELETEd. An identical
review may be replayed only while it remains the current globally active
version; a superseded version cannot be reactivated using old approval.

Stage12 makes no C7 calculations and offers no automatic fallback alpha/k,
weights, time half-life, peer cohort or config version. Read-only
`read_approval` returns historical journal status only; current IAM,
D1 and Stage13..21 readiness checks are still mandatory for runtime
scoring. Restrict database roles from direct audit inserts/trigger bypass;
real IAM attestation verifier is not included in this branch.
See `docs/GROUP_C_STAGE12_CONFIG_ACTIVATION.md`.

Stage12 global activation **additionally** requires a separate `signed_activation_assertion` and a trusted IAM verifier that checks capability `group_c.activate_global_config` and exact old/new config IDs. Project-local reviewer approval alone cannot authorize this global operation. The immutable activation journal pins `activation_assertion_sha256` with a partial unique index preventing reuse of a signed global action across activated configs; both events in one switch reference the same action SHA. The external IAM adapter remains BLOCKED_EXTERNAL; a synthetic verifier is used in disposable tests only.


## GC-MOD-13 exact creator comparison input and unpersisted C1

`intelligence/group_c/creator_ratio.py` introduces pure
`compute_creator_ratio(views,creator_median_views,alpha)` with exact
`(views+alpha)/(median+alpha)` Decimal, NUMERIC(30,12) HALF_UP;
nonnegative BIGINT views, nonnegative finite median, positive finite explicit
alpha. Floating/bool/missing input and unsanctioned rounding of the
approved median/alpha are rejected; numeric overflow is not clamped.
A raw zero-view C1 ratio can be calculated alone but is not C7-ready
because the later engagement-rate denominator requires positive views.

`PostgreSQLCreatorRatio.derive` performs one independent top-level
REPEATABLE READ / READ ONLY source verification: canonical D1 project+video,
current source permission/TTL, exact pinned snapshot+metadata and newest RAW
observation, Stage06 C0 metric definition/version/quality, same platform
and content type, exact D1 creator ID for target and **every frozen creator
peer**, exact target exclusion, old immutable cohort parent/membership and
recorded raw view/metadata evidence. It requires the *same* creator freeze,
prior/niche evidence and approved Stage11 request subject SHA, currently
globally active Stage12 config with matching immutable activation event,
pinned alpha and full reviewed config JSON, as well as a *current trusted*
`CreatorRatioApprovalVerifier.verify_c1_use` that validates external
human/global IAM authorization (not provided in this feature branch).
Reuses the existing D1 raw timeline adapter with its documented minimum
limit=2; no last-good fallback.

Returns an immutable `CreatorRatioResult` with project/video/snapshot/
metadata, exact source/policy/metric version, creator, config, freeze,
review decision+activation IDs and an input evidence SHA. Always
`status=c1_derived_unpersisted`, `ready_for_c7=False`: no persistence,
C2–C7 execution, QDP ACK, provider call or Analytics publisher.
This is NOT a production scorer. The Stage07 pure full-math implementation
remains isolated and is NOT implicitly invoked.

See `docs/GROUP_C_STAGE13_CREATOR_RATIO.md`. CI on exact commit and
real IAM connector/source deployment remain NOT VERIFIED/BLOCKED_EXTERNAL.


## GC-MOD-14 необязательное сравнение с нишей

`intelligence/group_c/niche_comparison.py` реализует отдельную
`compute_niche_comparison(views,niche_median_views,alpha)`:
`(views+alpha)/(niche_median+alpha)`, точный Decimal NUMERIC(30,12),
ROUND_HALF_UP. Если **в одобренной конфигурации** вообще нет ниши —
результат `None`/SQL NULL, не 0 и не придуманная медиана.
Ошибка доступа, несовпадение source/metric version, topic или ID
указанной замороженной группы => `ContractError`, **не NULL**.

`PostgreSQLNicheComparison.derive` использует собственную
`REPEATABLE READ, READ ONLY` транзакцию и повторно проверяет
канонические D1 права/TTL, последний сырой снимок, точный
`platform_post_views` metric version и quality, stage11/12
одобренный subject SHA и текущую глобально активную конфигурацию.
Ниша, когда включена, должна быть той же `niche_freeze_id` из
review, с активной канонической `topics.id`; сравниваются
`creators.topic_id` исследуемого автора и **каждого** участника.
Разные авторы одного topic ID разрешены, но target-self, иной topic,
платформа, content kind, проект, источник, ревизия policy,
вид/версия счётчика или snapshot lineage запрещены. Перед выдачей
результата нужен внешний доверенный
`NicheComparisonApprovalVerifier.verify_niche_use` для повторного
подтверждения подписанного human/global IAM; в ветке нет
продакшн IAM провайдера, в disposable тестах лишь имитатор.

Выход `NicheComparisonResult` иммутабелен, содержит идентификаторы
источника/снимка/review/activation и точную медиану, alpha и
`evidence_sha256`. Статусы: `niche_derived_unpersisted` или
`niche_not_configured`. Инварианты:
`contributes_to_breakout=False`, `ready_for_breakout=False`
**всегда**. Не изменяет конфигурацию, QDP, Analytics и
`calculated_metrics`. `docs/GROUP_C_STAGE14_NICHE_COMPARISON.md`
содержит полный контракт. CI PostgreSQL17 и живой IAM E2E
остаются NOT VERIFIED/BLOCKED_EXTERNAL.


## GC-MOD-15 Скорость роста просмотров

`intelligence/group_c/views_growth.py` provides isolated pure
`compute_views_growth_per_hour`:
`(current.views - previous.views)*3600000000/elapsed_observed_microseconds`,
exact Decimal NUMERIC(30,12), ROUND_HALF_UP, no
`timedelta.total_seconds()`/float, no wall-clock based delta, canonical
timezone-aware UTC timestamps. Strict nonnegative BIGINT raw counters;
earlier and later timestamps must be strictly ordered; corrected/decreased
view counters explicitly fail `counter_decreased` — **never become zero
growth**. Extra small intervals overflow rather than clamp output.

`PostgreSQLViewsGrowth.derive` REUSES the canonical Stage06
`read_c0_pair` (`REPEATABLE READ, READ ONLY` D1
project/video/metadata/current-source/TTL and exact two *latest raw*
source snapshots). The Stage06 API gained a backward-compatible
`require_score_fields=True` default; Growth opts into `False`
to calculate valid views-only rates without requiring currently absent
likes/comments/shares or positive view denominators. Other Stage06 and
engagement/Breakout consumers keep their strict defaults unchanged.
Exact snapshot/video/source/policy/metric-definition/version IDs,
observation times, original counters, elapsed microseconds and
`views_growth_per_hour` are captured in immutable
`ViewsGrowthResult` with canonical `evidence_sha256`.

**Not approved for Breakout:** output status
`growth_derived_unapproved`, `ready_for_breakout=False` unconditionally.
No human IAM approval is attempted at this isolated diagnostic stage,
no config is activated and no `calculated_metrics`/QDP ACK/
Analytics event/Breakout Score is produced. Future worker/persistence
stages must revalidate current human IAM, reviewed config/cohorts and
source evidence before producing an approved complete score.
See `docs/GROUP_C_STAGE15_VIEWS_GROWTH.md`; Stage15 pure+PG17
tests authored, PG17/final CI NOT VERIFIED.


## GC-MOD-16 raw C4 engagement
intelligence/group_c/raw_engagement.py computes Decimal fractions for likes, comments, shares, raw engagement and weighted engagement. Named percent display is separate, saves are excluded, zero views/NULL/overflow blocked. PostgreSQLRawEngagement.derive rereads exact latest raw D1 pair, canonical project/source/version/policy/metadata, rights and retention in a read-only transaction. Output is immutable with provenance SHA and unapproved diagnostic status; no calculated_metrics, QDP ACK, or Analytics emission. See docs/GROUP_C_STAGE16_RAW_ENGAGEMENT.md.


## GC-MOD-17 C5 Bayesian source/review boundary
intelligence/group_c/bayesian_engagement.py owns precise Decimal Ls=(L+k*pL)/(V+k), Cs, Ss, weighted Ls+1.5Cs+3Ss. Values k and all three priors must be non-null, explicit NUMERIC(30,12) and reviewed; they are NOT configured by a default provider or guessed from C4. PostgreSQLBayesianEngagement.derive checks the active config, signed review subject and global activation event, C0 latest raw snapshot and D1 project/source/retention, original frozen engagement_prior legacy cohort members/counters/statistics, and injected BayesianEngagementApprovalVerifier. Diagnostic immutable receipt only, ready_for_breakout=False, no writes/Analytics/QDP output. Current IAM provider is unavailable until owner integration. See docs/GROUP_C_STAGE17_BAYESIAN_ENGAGEMENT.md.


## GC-MOD-18 C6 freshness source/provenance handoff
`intelligence/group_c/freshness.py` calculates exact historical 100 * 2^(-(current.observed_at - canonical source_video_metadata.published_at)/48h) with Decimal microsecond precision; only output is rounded to 12 fractional digits. GC08 `PublicationTimeEvidence` and its SHA are independently recomputed within the **same** repeatable-read read-only D1/C0 transaction, with project/source/metadata-policy/retention, view-definition+version, current source rights and latest two unfiltered raw observations. Legacy videos.published_at, wall-clock/collected/fetched dates, assumed observation times, stale corrected snapshots and missing publication dates are rejected as alternatives. Diagnostic `FreshnessResult` pins exact source/version/snapshot+metadata IDs and immutable SHA; not a complete C7 score, no approval or persistence. See `docs/GROUP_C_STAGE18_FRESHNESS.md`; full production IAM/QDP E2E unverified.


## GC-MOD-19 C7 arithmetic/preview integration contract
`intelligence/group_c/breakout_score.py` exports `compute_breakout_components` and `assemble_breakout_preview`. C7 = .45*verified C1 + .30*verified C3 + .15*verified C5 + .10*verified C6, each contribution quantized independently to NUMERIC(30,12) HALF_UP before sum. No C2 niche term, normalization, cap or defaults. Frozen `BreakoutComponents` includes four score fields and final score; `BreakoutPreview` pins exact C0 D1 project/video/current+previous snapshot, metadata/source policy/view version, config+creator/prior freezes, original human decision/activation and all upstream SHA256. Inputs must be typed GC13/15/17/18 diagnostics and recompute exactly with matching evidence hashes. This *does not* prove all four separate upstream DB readers share one snapshot, continued source rights or external IAM approval. `status=breakout_preview_unpersisted_unverified`, `score_status=pending_review_and_atomic_revalidation`, `ready_for_persistence=False` universally. **GC-MOD-20** must fresh-check everything and persist the canonical independently recomputed score in one atomic read/write transaction; never mark these preview receipts `complete` or use them as permission to publish to Analytics. See `docs/GROUP_C_STAGE19_BREAKOUT_SCORE.md`.


## GC-MOD-20 atomic C7 persisted handoff
`PostgreSQLC7Persistence.persist` independently rereads the latest D1 raw facts and source rights, actual publication time, every original frozen creator/prior/niche peer, exact signed human review and currently active global config and requires a trusted external `C7PersistenceApprovalVerifier`. All C1/C3/C4/C5/C6/C7 fields are recomputed and saved to the existing 003/004 complete `calculated_metrics` row together with a new 060 immutable `group_c_calculation_receipts` evidence-SHA/audited-approval row **in one SERIALIZABLE write transaction**. Unique version lineage plus all-field SHA comparison makes retries idempotent or explicitly conflicting. A receipt with `score_status=complete` means persisted score, NOT Analytics delivery or durable QDP ACK. Production database roles must disallow direct unauthorized complete score writes; no production IAM adapter exists in this branch. See `docs/GROUP_C_STAGE20_C7_PERSISTENCE.md`.


## GC-MOD-21 durable job/score handoff
`PostgreSQLGroupCWorker` claims only due `job_kind=score` jobs from the existing GC03 `group_c_jobs`, with project+global concurrency budget and PostgreSQL `FOR UPDATE OF j SKIP LOCKED`. Exact lease ownership is `project_id/run_id/video_id/job_id/worker_id/lease_generation/lease_token`; heartbeat, failure, requeue and cancellation require current fence and DB lease deadline. Expired leases are recovered or failed with bounded deterministic exponential backoff. Worker `execute_score` locks job then run within a single SERIALIZABLE transaction, calls GC20 `persist(transaction_managed=True)` with the SAME DB transaction and current externally injected IAM, then marks run+job completed with the same committed `calculated_metrics.id`. Any stale/expired token, source/reviewer mismatch or SQL error rolls back the entire scoring transaction; no separate GC20 commit can slip past a stolen lease. Work is caller-driven (one `run_once` call) and has NO implicit scheduler, provider calls, QDP ACK or Analytics outbox. Production IAM and PG17/disposable QA still BLOCKED. See `docs/GROUP_C_STAGE21_DURABLE_WORKER.md`.


## GC-MOD-22 incremental revision and pending Analytics notification
`PostgreSQLIncrementalRecompute.plan` accepts only a fresh original GC04-accepted QDP C run/job, requires an original certified previous `calculated_metrics` complete result and its original completed Group C run for the same project/video/source, and rechecks latest actual D1 raw source observations, publication, currently approved human review+active config and original frozen peer rights under SERIALIZABLE transaction. One append-only `group_c_revisions` binds prior complete id/SHA to new raw/config/peer/publication/approval/QDP provenance. `determine_revision_kind` distinguishes new_snapshot, config_changed, cohort_changed, publication_correction; no fake revisions from source_revoked (GC24). **Publication metadata is NOT part of 003's unique calculation key**; metadata-only correction with otherwise identical C7 lineage cannot write a distinct result and is explicitly rejected until a new approved snapshot/config/cohort version is available. GC21 worker verifies planned review/source/config before entering GC20 score computation and calls `enqueue_ready` after completing the run and job, all in one fenced SERIALIZABLE transaction. Result-ready outbox is `pending`, with unique revision event key and original/new certified C7 IDs and evidence SHA; actual Unified Analytics delivery/ACK belongs to GC25, and no one should label it delivered here. See `docs/GROUP_C_STAGE22_INCREMENTAL_RECOMPUTE.md`.


## GC-MOD-23 metric-quality gate / no fake score boundary
`metric_quality_gate.py` classifies original C0/D1/peer/config evidence failure into one existing 003 non-complete `calculation_status=score_status`, preserving specific reason_code, but **never INSERTs a fake `calculated_metrics` row** and never overwrites completed C7. An approved exact Decimal `growth_policy.max_views_growth_per_hour` can reject proven anomalous raw growth inside GC20's actual SERIALIZABLE D1/source/IAM transaction; no universal numerical heuristic is invented. GC21 `run_once` stores terminal quality as failed job, blocked unscored run and immutable SHA-bound `group_c_quality_reports` journal (new additive migration `061_group_c_quality_reports.sql`) in a fenced transition. IAM, SQL and worker-token errors are NOT misrepresented as data quality, and no Analytics outbox is emitted for blocked runs. GC24 must independently gate previously complete score reads on current retention/revocation rights; GC23 does not declare historical scores still usable. See `docs/GROUP_C_STAGE23_METRIC_QUALITY_GATE.md`.


## GC-MOD-24 — current C7 rights, irreversible revocation, and erasure coordination
`PostgreSQLScoreRetention.read_current` is the required project-scoped C7 consumption boundary for GC25/GC26: in one read-only REPEATABLE READ transaction it requires an originally certified completed Group C C7 and live D1 permissions/TTL on target source, metadata/raw snapshots, latest unfiltered raw observation pair and **every signed frozen creator/prior/(optional) niche member**. An invalidated, erasure-requested, expired, retracted or superseded result returns `RetentionRead(state=unavailable,breakout_score=None,evidence_sha256=None,reason_code=...)`, NEVER a usable complete score. Cross-project and unknown score both raise `score_not_found`. GC04 QDP ingest now also enforces original metadata `is_current=True` before accepting a preparation. Source/policy failure can produce an immutable `group_c_score_invalidations` record under SERIALIZABLE; an ordinary new raw snapshot only makes an old score noncurrent, not permanently legally invalid. `group_c_erasure_requests` requires separately verified legal IAM/signed assertion, immediately masks the result and blocks new outbox delivery; physical erasure is NOT claimed and is BLOCKED_EXTERNAL because immutable GC11/12/20 review/cohort/receipt material, FK RESTRICT and backups need a separately audited legal executor. Migration 062 also blocks new Analytics outbox leasing/delivery on explicitly invalidated/erasure-requested score IDs. The pending Analytics delivery worker GC25 must recheck live rights at dispatch, not rely solely on audit row. Production consumers must be denied direct `calculated_metrics` SELECT by DB roles/RLS; this code cannot guarantee that role policy today. See `docs/GROUP_C_STAGE24_RETENTION_REVOCATION.md`.


## GC-MOD-25 — persisted C7 only: Analytics transactional adapter contract
Group C GC20 is the ONLY production writer/solver of C7. GC25 `PostgreSQLAnalyticsBridge` publishes the first complete C7 `calculated_metrics.id` with all snapshot+prior/publication/config/creator/prior/niche freeze/review decision/activation/source policy version/metric version lineage and exact SQL Decimal score components in one GC21 fenced SERIALIZABLE commit; GC22 revised C7 keeps its original version-linked old→new event. The `analytics_bridge.load_persisted` function returns actual signed C7 SQL columns after GC24 live D1/raw+all frozen peer rights/TTL check, no score computation. `deliver_one` only processes project-pending `group_c_outbox` via `FOR UPDATE SKIP LOCKED`, validates original immutable SHA/payload against stored C7, repeats GC24 live rights, and invokes required `GroupCPorts.analytics.enqueue_persisted_result(conn,project_id,calculated_metric_id,idempotency_sha256)` in the SAME PostgreSQL transaction. Sink must durably insert exact C7 ID/lineage and return `{accepted:true,project_id,calculated_metric_id,idempotency_sha256,analytics_snapshot_id:positive_int}`; only then outbox becomes `delivered`. Invalid/missing ACK rolls back; rights-expired/revoked/erasure-bound C7 must not deliver. This is NOT a network/remote HTTP outbox worker; remote service needs separate reviewed protocol. 
**Peer integration required; NOT performed:** actual PR #23 branch `feature/analytics-engine-qualified-pipeline-20261023` retains competing `intelligence/analytics/metrics.py.compute_c_metrics` and `intelligence/analytics/service.py._metrics/_store_c/run_qualification` C7 scorer/writer. Analytics owner must replace production C7 scoring with a same-DB adapter using GC25 `load_persisted` and the exact C7 ID, while retaining its independent actual A/B/relevance qualification (no fake readiness); `analytics_visible_snapshots` and aggregates must enforce GC24 live rights/invalidation/erasure instead of only `score_status='complete'`. User authorization/peer-branch code change and full E2E needed. The Group C branch and main are unchanged outside Group C. See `docs/GROUP_C_STAGE25_UNIFIED_ANALYTICS_HANDOFF.md`.


## GC-MOD-26 — Group C HTTP API (independently mountable WSGI)
`intelligence/group_c/http_api.py` exports `GroupCHTTPAPI(connect,access_verifier,allowed_hosts,allowed_origins,human_reviewer_verifier,config_activation_verifier)`. All routes are rooted at `/api/group-c/v1/projects/{32-lowercase-hex}`. An actual externally injected `HTTPAccessVerifier` MUST validate bearer signature/revocation/expiry, exact project/action method/path scope and action-bound `X-CSRF-Token` for POST. Reject unknown Host/Origin, non-HTTPS scheme, untrusted `X-Forwarded-Host`/`X-Forwarded-Proto`, POST cookies, JSON duplicate/unknown fields or oversized bodies, IDs noncanonical and too-large pagination before domain write. No static shared secret/demo authenticator is bundled. API has project-owned GET list+detail of runs, signed reviews, activated configs, immutable cohort freezes, exact certified score, and a `videos/{id}/preview` that reads only already persisted C7 (never a second C7 scorer). Only current GC24-verified scores expose Decimal breakout and four actual GC20 SQL components; expired/retracted/erasure-requested results yield `unavailable` with null numeric/evidence SHA. POST review create, separately human-signed review decision, separately human-signed config activation, fenced run cancel delegate to existing GC11/12/21 methods; no HTTP route auto-approves, writes calculated_metrics, calls provider/API/LLM or starts a scheduler. Response type is `group-c-http/v1`, never floatifies Decimal. External production WSGI/TLS mount, IAM+CSRF, SQL grants/RLS, real provider and Analytics E2E are **not installed or verified**. This branch does not modify app.py, main or peer modules. See `docs/GROUP_C_STAGE26_HTTP_API.md`.
