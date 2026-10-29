# SRE M10 — Typed LIVE FSM / DTO and Technical Outcomes

**Date:** 2026-10-26 · **Owner:** SRE only · **Branch:** feature/sre-production-v1-20261023 · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22).

**Implementation:** code and negative unit tests committed. **QA:** GitHub Actions still queued (NOT observed PASS). This is a contract-only milestone: no DB migration, worker, provider call, network, paid consent or billing receipt.

## Frozen boundary

The new module intelligence/relevance/live_fsm.py does NOT change earlier v1_contracts.RUN_STATES, v1_runtime.validate_run_plan or S4 offline_runs/offline_worker. Existing offline_fixture_s4 rows can never silently count as LIVE. Every M10 DTO requires schema_version=sre-live-fsm/v1 and execution_mode=live.

The four frozen Python dataclasses and validators:

| DTO / validator | Identity pins | State and coverage |
|---|---|---|
| LiveRun / validate_live_run | project, run/publication UUID, taxonomy/pipeline/request SHA | expected_items, finished_items, blocked_items; completed requires all items finished and zero blocked; partial means a measured subset |
| LiveItem / validate_live_item | project/run, D1 video, content version + SHA, ordered unique C9 hypothesis UUIDs, lease generation | exactly one of match/no_match/uncertain/not_evaluated per expected leaf; completed requires no missing; partial requires some measured and explicit missing; outcomes immutable |
| LiveBatch / validate_live_batch | project/run/batch UUID, ordered unique item IDs, exact input SHA, generation | independent FSM; settling requires exact coverage |
| LiveAttempt / validate_live_attempt | project/run/batch/attempt UUIDs, request SHA, generation, durable dispatch_intent, optional provider request ID, outcome | unknown means possible provider charge; non-dispatched attempt cannot claim provider receipt |

All DTOs are pure values, NOT proof of DB authorization, human review, tenant ownership or paid budget consent. The future service must verify actual S2 C9, M08 D1 live rights, finance reservation and fencing in durable PostgreSQL. A caller-provided reconciled=True is not a reconciliation receipt; a trusted audited service must be the only source for it.

## State machines

Run states: planned → pending_consent → queued → running, with explicit waiting_source, paused_budget, paused_rights and unknown branches. Item state adds not_evaluated; batches have a separate graph. Terminal run/item/batch states partial, completed, failed and cancelled never reopen. Unknown → running/partial/completed/failed/cancelled is allowed ONLY with reconciled=True and future audited receipt.

Attempt state graph: reserved → dispatch_intent → completed / failed_known / unknown. Before dispatch, reserved may become abandoned_before_dispatch. Unknown may become completed or failed_known only after reconciliation. Unknown → reserved or dispatch_intent is permanently forbidden.

transition_live(kind,current,target, reconciled=False, coverage=None) strictly validates allowed graph edges. Run/item/batch transitions to completed demand exact coverage {expected,evaluated,blocked} and evaluated == expected, blocked == 0. Partial requires 0 < evaluated < expected and no overcount. A synthetic result or absent label must never be silently mapped to a real negative.

## Technical outcome taxonomy

classify_technical_outcome(code, dispatch_intent=bool) yields a frozen TechnicalOutcome with stable category and retry policy.

- Success is only valid when send intent exists and has retry_policy=never.
- Rate-limited, transport error, deadline exceeded or unknown charge AFTER possible send → unknown/reconcile_only, never automatic paid retry.
- These same errors BEFORE send intent can be known_failure/safe_after_new_reservation.
- Revoked rights/expired content → blocked_rights/only_after_new_authorization.
- Budget unavailable → blocked_budget/only_after_new_authorization.
- Lease lost or owner cancellation → stale_lease/cancelled with retry_policy=never.
- Known provider rejection or invalid response → known_failure, never silent post-send retry.

This is a pure technical taxonomy and never fabricates provider usage, charge receipt, classification evidence, or model output.

## QA and next step

New tests/test_sre_live_fsm.py covers every transition in all four FSMs, DTO exact schema and type checks (UUID, integer vs bool, NaN, Inf, SHA, invalid version), immutable result groups, full/partial exact hypothesis coverage, foreign execution_mode rejection, provider receipt spoofing, ambiguous-charge reconciliation and import without optional DB/provider dependencies.

.github/workflows/sre-contracts.yml now executes this unit suite on Linux and Windows and retains its PostgreSQL17 regression workflow. M10 itself requires no database. The current CI conclusion must be checked on the final SHA; queued is NOT a pass.

Read-only local commands:
- python -m compileall -q intelligence/relevance
- python -m unittest discover -s tests -p test_sre_live_fsm.py -v
- python -m unittest discover -s tests -p test_sre_contracts_v1.py -v

**Status:** code implemented, acceptance pending. No paid calls, migration change or main merge.

**Next milestone M11:** separate additive PostgreSQL LIVE run/item/batch tables, immutable pins and project-scoped lease generation, only upon the next user request.
