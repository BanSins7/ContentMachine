# Content Machine B · Stage12 — B8 Human Knowledge Review

**Implementation:** internal, trusted-server Python review service, immutable PostgreSQL audit chain, no paid LLM, no inference about factual truth and no automatic B9/C publication. Not an externally exposed, authenticated HTTP endpoint or finished browser UI.

## Input handoff and trust model

B8 begins **only** from a persisted, historically reverified B7 Stage11 decision `ready_for_review` or `partial_ready`. A B7 `blocked` decision cannot create a review session. Before opening, inspecting, acting or submitting, B8 replays the original A → B1 → B2 → B3 → B4 → B5 → B6 → B7 chain; a changed immutable A source, missing provenance or altered report blocks the action. B8 never uses video observations as B3 fact input, never upgrades Sonnet-reported website truth, and never auto-approves a citation.

### Authorization boundary

B8 expects the calling backend to supply an **already authenticated** reviewer identity. Reviewer strings are **not authentication credentials**. Until a real authentication/authorization transport binds platform identity to B8, no public HTTP routes should be added. The server also checks `knowledge_fact_review_authorizations`: a reviewer must be explicitly enabled for the exact project. Grants must be provisioned by privileged project administration, not by untrusted users or the model. Another project's grant is insufficient. One review session is pinned to one reviewer to prevent silent takeover; disabled grants stop further operations.

No model-generated content, source prompt injection or end-user supplied display data can grant reviewer permissions.

## Reviewer packet

`FactHumanReviewRepository.inspect(project_id,session_id,authenticated_reviewer_id)` returns:
- Original B7 decision, every B7 warning and blocker count, exact B7 gate SHA, original A video export audit (references only).
- Every B6 exact group, representative fact text, kind, role, subject and **every** candidate/membership (including original MiMo text, literal quote, B5 original A provenance and B5 receipt SHA); no merged-away citations.
- All B6 *potential* conflicts, with their two stable group IDs and rule.
- Separately counted B4 quarantine and B5 rejected members; those rejected proposals never appear as approvable groups.
- Session status, monotonically increasing CAS revision and current human actions. Display with distinctions between user-supplied profile, original parsed documents and `model_reported_unverified` Sonnet site content.

This structured packet can back a future authenticated reviewer UI without copying source text into an untrusted record.

## Human actions

`record(project_id,session_id,reviewer_id,expected_revision,target_kind,target_id,decision,...)` appends **one immutable event** in a serialized SQL transaction and advances the session revision exactly once. An old expected revision fails (`b8_cas_revision_mismatch`), preventing lost updates. No update to B4/B5/B6 original facts is ever performed. Every event is SHA-chained to its predecessor with immutable reviewer, group/conflict/gate target, decision, note, original/restated text and explicit assertions.

Group decisions: `approve` or `reject`. Approval requires the human to explicitly affirm that the reviewed claim is supported by the displayed source, **and** that its subject/scope is correct. Reviewer may provide a corrected statement only with a nonempty audit reason. The original AI-proposed text, quote and all source citations are preserved as separate fields. An edited statement is flagged `human_edited`; the human's assertion is not treated as an objective truth guarantee. Rejected groups cannot be converted to an implicit approval.

Conflict decisions: `select_a`, `select_b`, `reject_both`, `not_conflicting`, `defer`. A final `select_a` requires group A approved and B rejected; B vice versa. `reject_both` requires both rejected. `not_conflicting` preserves each independent group action (including two approvals). A `defer` action remains unresolved and cannot finalize. Each conflict action requires an explicit reviewer note.

Warning acknowledgment: explicit `acknowledge` of `gate_warnings`, with nonempty note. Required whenever B7 has warnings, independent of approving a group. This prevents silently bypassing quarantine, uncertainty, partial source, web-trust or conflicts warnings.

## Finalization and audit

`submit(...expected_revision=N)` succeeds only after **every** reviewable group has a current explicit action, **every** conflict has a compatible resolution, and all B7 warnings were acknowledged. It writes a hash-pinned, immutable `knowledge_fact_review_submissions` and atomically marks the review session submitted. A no-approved-facts outcome is valid human review but `publication_eligible=false`. After submission events cannot be changed/deleted and no further record is allowed. Duplicate submit with identical revision replays the same immutable submission ID.

Submission preserves all approved groups' original + reviewer-edited text, every member's original quote, A source/provenance + B5 receipt, and explicit reviewer assertion/event hash; rejected groups and resolved conflicts remain in the submission. The record states `publication_performed=false` and does not call B9 or C.

`materialize_submission` independently replays historical B7/A source and the entire B8 hash chain and compares the immutable SQL submitted JSON with a deterministic reconstruction. The session/events/submission never become an approved B9 KnowledgeSnapshot automatically.

## PostgreSQL 17 migration 051

1. `knowledge_fact_review_authorizations` — project-specific admin-provisioned reviewer grants, revocable for future actions.
2. `knowledge_fact_review_sessions` — one per project/B7 gate, reviewer, gate/dedup/run/batch immutable pins, CAS revision and open/submitted lifecycle.
3. `knowledge_fact_review_events` — immutable append-only event chain with exact target/action, editor text, notes, source/support and subject affirmations.
4. `knowledge_fact_review_submissions` — one immutable final JSON and canonical SHA per review session.

Composite project-keyed FKs guard all links to B7 and B6. Explicit no-update/no-delete triggers on events and final submissions; submitted sessions cannot be reopened or reassigned. All SQL is testable offline.

## API (internal server-only)

    from intelligence.knowledge_extraction.human_review import FactHumanReviewRepository

    reviews=FactHumanReviewRepository(connection_factory=...)
    session=reviews.open(project_id,completed_b7_run_id,authenticated_reviewer_id)
    packet=reviews.inspect(project_id,session["session_id"],authenticated_reviewer_id)

    # Human clicks approve; the backend confirms authentication and grant.
    next_event=reviews.record(
        project_id,session["session_id"],authenticated_reviewer_id,
        expected_revision=packet["revision"],target_kind="group",
        target_id=packet["groups"][0]["group_id"],decision="approve",
        source_support_affirmed=True,subject_scope_affirmed=True,
    )
    # Repeat for every group, conflict and required warning acknowledgment.
    result=reviews.submit(project_id,session["session_id"],authenticated_reviewer_id,
                          expected_revision=final_revision)
    audit=reviews.materialize_submission(project_id,session["session_id"],
                                         authenticated_reviewer_id)

Do not call the service directly from an anonymous form or expose user-supplied reviewer IDs as authorization. Any production UI/API must enforce session authentication, project permissions, CSRF protections, audit privacy, rate limits and transport validation before calling this trusted internal service. Stage12 ships the stable backend service and reviewer packet, not a speculative insecure route.

## Quality assurance

    python -m unittest discover -s tests -p 'test_ke_stage12_human_review.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

Tests on Linux and Windows/PostgreSQL 17 use only local fake B3 senders. Test explicit approval, unchanged/different human edits, all-rejected outcome, group-conflict compatibility, incomplete reviews, warning acknowledgment, optimistic stale revision, project and reviewer grants, event+submission immutability, idempotence, duplicated source members, original A citation replay and later profile revision. No MiMo/OpenRouter charge.

## Next authorized stage: B9 KnowledgeSnapshot and C adapter

Stage13 must separately verify submission and B7/B8 original A pins, reject publication of 0-approved-facts submissions, carry approved groups and **all** source-citation members plus A VideoIntelligence pins, bind schema version and digest, then integrate with C via an explicitly new input mode. Stage12 neither writes B9 nor enables a C pipeline or new spending consent.
