# PIE-MOD-32 — Human Review, Feedback, Appeals & Calibration v1

**Branch**: feature/patterns-insights-full-module only; main/UAE/QDP/Group A/B/C/SRE untouched.  
**Wire**: pie-human-review-event/v1, host grant pie-human-review-authorization/v1.  
**Module**: intelligence/patterns_insights/research/human_review.py.  
**Migration**: intelligence/migrations/206_pie_human_review_events.sql.  
**Tests**: tests/test_pie_human_review.py (**39 authored unittest methods**, synthetic and opt-in disposable PostgreSQL17).  
**Release status**: candidate-only private review, NO public Insight, no automated publishing, no live reviewer identity connected or independently proven.

## Why a separate review ledger

The older foundation migration200 includes pie_insight_reviews, linked to a different legacy pie_insight_versions/pie_pattern_candidates model. The current immutable research chain uses stage25 versioned Pattern Registry, stage30 candidate Insight generations and stage31 verification. This stage creates a **new, explicitly scoped** pie_candidate_insight_review_events ledger; it never mutates the old legacy review table, stage30 generation JSON or model-authored text.

Human review is for interpreting **descriptive and unverified candidate associations**, NOT declaring a causal effect, authenticating external source rights, accepting calibrated inferential confidence or approving a public report. Internal code review_candidate_internal is NOT approve_for_release. Even a curator's unanimous internal calibration label is not a scientifically validated gold standard or publication right.

## Host authentication, owner separation and source freshness

The caller supplies:
- an exact stage26 RegistryQuery identifying the **original current project source owner** (not the external reviewer);
- a requested series_id, generation_sha256 and immutable ReviewAction with unique caller-generated UUID request_id;
- actor_id distinct from the original source owner for reviewer, adjudicator and curator actions; only the original owner may appeal;
- a **trusted host** CurrentReviewAuthority adapter, which must authenticate the actual user and return a fresh, project/actor/role/generation-scoped CurrentReviewGrant, expiry and cryptographically grounded host grant digest;
- the same stage26 current owner/project, UAE reader and source-rights gate and cursor signer collaborators required by stage31.

The code checks the grant *before* reading database source/generation. A mere Python dataclass or fake test authority is **not** evidence of a real login, identity, HR policy, or valid signed permission. Hosts must connect their own authenticated identity/authorization infrastructure before live use. No automatic adapter probing, discovery or connection is performed.

On **every append, including exact idempotent replay**, the function refreshes stage31 verification within a project advisory transaction lock. This itself re-checks current stage26 project/owner/qualified UAE and all source-row rights, current source tombstones, original stage25 pattern latest version and stage30 immutable generation. A missing source, revoked rights, old revision or inaccessible verification is denied. Even a successful stage31 check only returns candidate_checked_for_review, not legal consent or validation.

After the stage31 preflight, a generation-specific PostgreSQL transaction advisory lock serializes review events. All prior events must be an exact ordered, SHA-bound canonical chain with no duplicates. Appending requires event_seq = previous+1, parent_event_sha256 equal to the exact prior digest and the same verification_sha256. Any malformed chain fails closed.

## Supported immutable events and feedback

Only enumerated human action codes are permitted; there are no user/LLM-controlled raw paragraphs to inject into an Insight.

| Event | Actor | Restricted action |
|---|---|---|
| review_decision | reviewer | review_candidate_internal / reject_candidate / request_changes |
| change_suggestion | reviewer | clarify_uncertainty / clarify_scope / correct_source_unit / add_caveat / retest_hypothesis |
| appeal | original source owner (appellant) | appeal a specific prior reject_candidate or request_changes |
| adjudication | independent adjudicator | uphold_rejection / return_to_review, targeting the appeal |
| calibration_vote | independent reviewer | candidate_consistent / candidate_contradictory / uncertain |
| calibration_gold | curator independent of voters | freeze the *internal* agreed label only after two or more distinct, unanimous blind reviewer votes |

Reasons are bounded enumerations. A proposed edit receives a strictly increasing review_edit_version per generation and is stored in the immutable review ledger. It **never modifies** the original candidate text or scientific numbers; any substantive source correction must produce a new version of the underlying pattern and a new generated artifact with fresh verification.

A human author cannot submit their own reviewer, curator or adjudicator decision. The original author may appeal *one specific denial* once. The adjudicator must differ from **both** the author who appealed and the human reviewer whose decision is challenged. The same appeal cannot be adjudicated twice. Disagreement among different reviewers is recorded as disputed_internal (derived from each reviewer's latest vote), and cannot become a majority-vote public release. Unresolved appeals remain appeal_pending even when later unrelated calibration or feedback events appear.

Calibration votes are immutable and one-per-reviewer per candidate generation, with no curator gold label until at least two distinct independently recorded voters agree unanimously. A curator cannot relabel their own prior vote, and all calibration votes freeze after gold is recorded. Conflicting labels remain without gold consensus; they are internal calibration disagreements, not a claim of measured correctness on an independent dataset.

## Policy of release

There is **no supported/public read API** or automatic release path. All persisted events and receipts explicitly carry:

    may_publish = false
    may_public_read = false
    human_release_approved = false
    original_insight_modified = false
    model_proposal_is_review_decision = false

These states cannot be elevated through this module even with a positive review decision, two agreeing reviewers, appeal reversal, or internal gold label. Reviewer feedback is an internal annotation only. Future publisher modules must independently verify current project rights, factual scientific/holdout evidence, source revocations, distinct human release authorization and export rules; this version deliberately cannot satisfy those conditions.

## Append-only SQL and tamper/tenant safety

The additive SQL206 creates pie_candidate_insight_review_events with:

- Composite (project_id,generation_sha256,event_seq) PK, unique project/event SHA, unique project/generation/request SHA for retry-safe idempotency, composite FKs to stage30 generation and stage31 verification.
- Exact immutable SHA-linked parent event and request UUID-derived identity; CHECK enforces strict event-kind/role/decision/edit/label/target combinations, allowed reason codes, edit version boundaries, all source/actor/action fields reflected in JSONB and explicit no-publish/no-public-read/no-generation-modification flags.
- A transaction-serialized PL/pgSQL BEFORE INSERT guard independently verifies the source is the latest Pattern Registry revision, has no stage27 input source tombstone, exact stage31 verification belongs to this generation, and chain parent/sequence/verification SHA is consistent.
- SQL guard also rejects self-review, approval of a quarantined candidate, false appeal targets or cross-project appeals, adjudicator self-review of contested decision, unconsented calibration gold, late calibration votes and duplicate gold labels.
- Partial unique indexes forbid more than one appeal of the same decision, more than one adjudication of an appeal, or repeated calibration votes by one reviewer. A BEFORE UPDATE trigger rejects historical edits.
- No stored raw media IDs, creator IDs, original video row SHA, LLM output text, arbitrary raw reviewer notes, emails, tokens, spending amounts or public outbox events.

The DB cannot prove the actor's external identity or source licensing; only the authenticated host authority and fresh stage26 controls can do that. A direct database writer can never use this review ledger to grant public publication or edit stage30 model proposals.

## Replay, disagreement and adversarial QA

tests/test_pie_human_review.py has **39 authored unittest methods** covering:

- positive bounded internal review and quarantined refusal of positive approval;
- versioned non-executing edit suggestions; immutable parent SHA and exact byte/hash replay; idempotent UUID request replay with renewed rights and grant checks;
- distinct reviewer disagreement; one reviewer revising their own opinion with append-only history; positive review still never public;
- author appeals only a specific rejection, duplicate appeals blocked, adjudicator distinct from original reviewer and appellant, one-time adjudication; unresolved appeals persist across unrelated events;
- one-per-reviewer blinded calibration vote, two separate matching votes before curator consensus; conflicting labels and late-vote/curator re-labeling blocked;
- forged source/right/auth/grant, expired role, owner self-review, changed chain SHA, modified parent/sequence, invented model/citation/free text and unbounded edit IDs denied without public release;
- optional disposable PostgreSQL17 migration discovery, immutable + insertion triggers, cross-project/nonexistent generation foreign key refusal. These DB cases skip unless CM_TEST_PG_DSN is explicitly set to a database name containing test.

All positive current rights, verification and human role grants are **MOCKED** in the synthetic tests. Neither a real human reviewer, real authenticated UAE source, original source rights, independent holdout, scientific approval nor live PostgreSQL integration is claimed here. No real paid OpenRouter request is made.

To run on the user's full checkout:

    python -m unittest discover -s tests -p 'test_pie_human_review.py' -q
    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_human_review.py

Optional CM_TEST_PG_DSN should reference **disposable PostgreSQL17** (fresh and upgrade migration QA) only. Local unittest, full suite, compileall, PG17 fresh/upgrade/cross-project concurrency and live source/identity adapters **NOT RUN/NOT VERIFIED** when this stage's code was committed. CI queued is not green.

## Integration seam and next stage

Host must provide verified CurrentReviewAuthority identity/role grants, stage26 project/owner/UAE rights, stage27 invalidation and stage31 verification collaborators, and a PostgreSQL17 connection with migration206 applied. No main/UAE/Group A/B/C/QDP/SRE code is edited.

**NEXT PIE-MOD-33**: orchestrator, leased workers and queue retry policy with persisted fencing, cancellation, safe deterministic retries and **never retry a potentially billable stage29 model attempt**. Review events remain a separate immutable internal pipeline.
