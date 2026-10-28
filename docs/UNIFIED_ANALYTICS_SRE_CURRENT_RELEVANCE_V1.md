# UA-MOD-06 — SRE Current Approved Relevance Adapter

**Branch:** `feature/unified-analytics-full-module` — UAE only. **Implementation:** `intelligence/unified_analytics/sre_adapter.py`. **Regression:** `tests/test_unified_analytics_sre_adapter.py`. **Status:** code and mock tests committed; executable test/PG17 evidence separate from implementation.

## 1. Upstream audit and crucial release limitation

At audit time SRE branch `feature/sre-production-v1-20261023` HEAD was `fa07200ccbf27c2ad99b9beac59d68baa8c61575`.

The SRE feature branch has:
- `006_discovery_sre_workspace.sql`: project membership, category-approved taxonomy, `sre_classification_runs/results` and `sre_human_labels` with `superseded_at`.
- `016_sre_d1_intake_rights.sql`: exact D1 content/source/policy revision, current rights and `sre_readable_content_versions`.
- `017_sre_offline_run_engine.sql`: explicit `sre_classification_runs.execution_mode` (legacy/offline_fixture/live) and a restricted LIVE results view.
- `054_sre_live_core.sql`: immutable **LIVE run/item identities** bound to approved taxonomy publication, project, source, D1 content version and SHA, manifest and contexts SHA.
- `055_sre_live_batch_coverage.sql`: immutable SRE `sre_live_expected_pairs` registry and settlement fence.
- `intelligence/relevance/ports/outbound.py`: `sre.classifications_ready/v1` *transport shape*, validated statically; the validator does not prove a DB event or publish the event.

**Crucial:** In the observed `055` migration, expected-pair states can only be `not_evaluated`, `blocked_rights` or `blocked_budget`. It explicitly says that authenticated LIVE match/no_match/uncertain decisions and receipts are a future SRE stage. **No genuine fully approved LIVE SRE result can currently satisfy the UAE adapter on the audited peer HEAD**. UAE does not fabricate a `match`, backfill state, accept a synthetic S4 result or interpret a valid JSON event/SHA as an authenticated verified output. The matching mock test simulates a *future* peer schema after authenticated settlement, not a production success.

## 2. Reader contract and strict trust boundary

`SREApprovedRelevancePins` binds immutable `CanonicalLineagePins` from UA-MOD-05, verified taxonomy SHA, D1 content SHA, context SHA, and a nonempty, sorted, unique tuple of canonical human-approved **target leaf** UUIDs. The exact target categories are explicit: the reader never infers category relevance from a loose similarity score.

`SREApprovedRelevanceReader(conn, pins, as_of=UTC)` is injected with a project-owner-authorized `psycopg` dict-row connection and caller-managed repeatable-read transaction. It implements the UA-MOD-02 `SREReader` structural Protocol:
- `capability()` probes actual `sre_live_runs/items/expected_pairs`, SRE classification/human-review tables, readable D1 content and required schema columns. No SRE LIVE schema on standalone `main` ⇒ `PortCapability(unavailable, live_schema_missing)`; mismatched columns ⇒ `incompatible`. Probe SQL returns no provider tokens, and connection failures are sanitized.
- `read_current_relevance(scope)` accepts **only the same pinned project/video/source/policy/QDP scope**, checks LIVE run and item state = completed (not partial/unknown/queued/cancelled), classifier execution_mode = live, no cancellation and complete run coverage.
- Joins current active and approved taxonomy/sha to the same project, exact SRE content version/sha/context/manifest, D1 source metadata observation and reviewed source policy.
- Rechecks `sre_readable_content_versions` and policy/source permissions at read time, including metadata current/nondeleted, retention expiry, explicit semantic classify and media analysis rights, and denial of future-dated source/approval/run/content records.
- Rejects superseded content revision or an existing later completed LIVE run for the same video.
- Fetches **actual peer-owned expected pair rows** for the selected LIVE item, joins model classification results and current (unsuperseded) human label for every leaf; requires exact expected-leaf coverage and current approved taxonomy category.
- A LIVE pair must be in a terminal `match/no_match/uncertain` state **and** its corresponding model decision must agree. Under current 055 all pairs are nonterminal, so UAE refuses them regardless of plausible labels/events/hashes.
- `not_evaluated`, blocked pair, incomplete coverage, `uncertain`, or `no_match` for any selected target leaf is rejected. A `no_match` on a *non-target* leaf is legitimate and may coexist with an approved positive target. This distinction is important: declaring every non-target `no_match` a global failure would incorrectly reject normal classifiers.
- Active human `no_match` and `uncertain` override a model `match`; latest human corrections win by SRE's unique partial index on `superseded_at IS NULL`. Human `match` cannot bless an uncompleted LIVE pair or fabricate missing source-backed evidence; future-dated reviews are denied.
- Compares the full **effective** decision digest, including the effects of human review, to the pinned SRE digest. Changing any effective leaf decision requires a new lineage fingerprint.
- Returns `EvidenceRead(MISSING, reason_code=...)` for denied/incomplete peer evidence or `EvidenceRead(UNAVAILABLE,...)` if SRE LIVE is absent. Only after all checks may it return a typed `EvidenceRef` with `producer_schema=sre.classifications_ready/v1` and a deterministic payload digest.

**Important:** An `EvidenceRead.LOCATED` remains an adapter claim, not by itself a `QualificationObservation.checked=True` or an end-to-end `qualified` result. UA-MOD-11–13 must enforce the other rights, freshness, QDP qualified membership and A/B/C completion atomically. An authenticated receipt/producer signature from SRE may also be necessary before using a future LIVE result in production; this stage does not invent such a signature. The running UAE worker/service must never trust arbitrary HTTP JSON or SQL writes under an untrusted DB role as equivalent to SRE authority.

## 3. Deterministic failure states

| Condition | UAE `EvidenceRead` |
|---|---|
| Current SRE feature branch is not deployed (missing 017/054/055) | `unavailable/live_schema_missing` |
| Wrong schema/API shape or failing probe | `incompatible/live_schema_incompatible` or `unavailable/probe_failed` |
| Synthetic, legacy or offline_fixture run, partial/unknown/cancelled | `missing/no_completed_live_run` |
| Newer content version or newer completed LIVE result | `missing/superseded_live_relevance` |
| Disabled source, unreviewed policy, expired content, revoked metadata | `missing/sre_rights_expired_or_revoked` |
| Nonterminal pair, e.g. SRE 055 current `not_evaluated` | `missing/unsettled_live_evidence` |
| Category unapproved or wrong classification content/taxonomy | `missing/unapproved_or_unpinned_result` |
| Unsatisfied target leaf after human correction | `missing/not_approved_relevant` |
| Missing evidence, unknown verdict or effective SHA changed | `missing/missing_source_evidence`, `missing/uncertain_result`, `missing/sre_decision_digest_mismatch` |

Missing ≠ nonrelevant: missing/unknown means **unproven**, not a fabricated `no_match`. A model's actual negative prediction also prevents *that selected hypothesis* from qualifying, but an unrelated non-target negative leaf is not mistaken for a universal veto.

## 4. QA implemented and pending

`tests/test_unified_analytics_sre_adapter.py` has 17 unittest methods, including mock-positive *future* settlement, missing peer tables, protocol handshake, wrong schema, mismatched project/content/taxonomy, offline run, run state incomplete, negative/uncertain/not_evaluated, revoked human review, expired rights and malformed retention, later superseding content, missing evidence, wrong digest and sanitized DB errors. The conditional PG17 test installs **only standalone UAE/main migrations** and verifies absent SRE 017/054/055 is reported unavailable — it does not create real LIVE classifications or test a live LLM.

`python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v`

`python -m compileall -q intelligence/unified_analytics`

The existing workflow `.github/workflows/unified-analytics-module.yml` will pick up these new tests on Windows/Linux plus disposable `postgres:17`. Static source checks and committed tests are **not** a substitute for observed Python/PG17 PASS. No production or peer DB was written.

## 5. Next stage

**UA-MOD-07 — QDP handoff & qualified set verification.** Require source-backed current membership and exact group A/B/C preparation digests/rights, reject QDP ACK/ready-but-unqualified, incompatible QDP run, source revocation and stale input. Maintain independent SRE blocker state; do not merge feature branches without user approval.
