# Content Machine B · Stage13 — B9 KnowledgeSnapshot + C handoff

Status: versioned **immutable KnowledgeSnapshot**, replayed against historical A and explicit B8 human decisions, plus **non-paid preparation adapter** for existing Group C taxonomy-generation contracts. There is NO automatic paid C run, quote, consent, legacy RTB mode switch, UI/API, B9 auto-publish from B8 or B3 MiMo re-extraction.

## Pipeline

A approved immutable Context → B1/B2 verified Full/Multi text and video pins → B3 paid MiMo proposed facts → B4 normalize → B5 independently reopen A original text and quote → B6 exact dedup/conflicts → B7 Quality Gate → B8 **explicit human reviewer action + final submission** → **B9 explicit publish** → **knowledge_snapshot_v1 immutable C input preflight** → NEW independently quoted/authorized C taxonomy-generation workflow (not activated by Stage13).

## B9 publish rules

`KnowledgeSnapshotRepository.publish(project_id, B8_session_id, authenticated_reviewer_id)`:

1. Replays B8 submission (including immutable SHA event chain) and B7 original A context, B1 bundle, B4/B5/B6 source quotations and prior completeness decisions. A `blocked` quality gate, open/revoked reviewer, zero approved human facts, unsubmitted review or failed source SHA blocks publication.
2. Preserves only the **human-approved groups**. Explicit rejected groups, quarantined B4 claims and B5-invalid citations cannot be promoted; they are not extracted again by a model. Separate reject group IDs, reviewer conflict decisions and original reviewer audit SHA survive in the snapshot.
3. Each approved fact preserves group ID, original model text, human-reviewed text, human-edited flag, original role/subject, every distinct B5 source quote + original A source version/output/locator SHA, trust label, individual proposal ID and reviewer event SHA. A changed human text does NOT change an original quote.
4. Every pinned A `video-intelligence/v1` source/version/output/SHA from the B1 approved bundle is checked against the historical A Context. Video pins survive only as original refs; NO MP4, ASR, OCR, Vision, Fusion frame text, or generated transcript is passed to MiMo or artificially treated as B3 evidence.
5. The complete canonical JSON receives `knowledge-snapshot/v1`, builder version, project/run/batch/B8 submission/A context SHA, original B1 bundle SHA, reviewer and facts and video refs. DB stores immutable snapshot JSON + SHA, one per B8 submission/run, with project-scoped composite FKs and UPDATE/DELETE guards.
6. Repeated publish and read independently re-run historical A→B8 integrity; only identical snapshot returns the same ID. No automatic B8→B9 transition or publication to external systems.

A literal quote in Sonnet-reported website text remains `model_reported_unverified` and is NOT independent verification of a real website or objective factual truth. The human asserts relevance and support; `objective_truth_guaranteed=false`.

## Group C integration boundary

`KnowledgeSnapshotRepository.prepare_c_handoff(...)` writes a separate SHA-pinned `knowledge-c-handoff/v1` record in a second immutable table, with `input_mode=knowledge_snapshot_v1`. It never creates a C quote/consent/run or calls OpenRouter. `materialize_c_handoff(...)` verifies exact B9 snapshot/handoff SHA and schema.

`intelligence.taxonomy_builder.knowledge_binding.preview_knowledge_for_c(...)` loads verified B9 + C handoff and builds an input to **existing RTB `taxonomy_generation` `taxonomy-v2` prompt/schema**, with stable B9 fact IDs and approved texts. The legacy RTB supports four fact kinds (function, problem, audience, product_type), so other approved B9 fact kinds remain intact in B9 but are excluded *only from this legacy taxonomy-generation adapter*, and explicitly counted. The model is never asked to perform `fact_extraction` a second time on B output.

Each C handoff explicitly declares:

- `independent_c_quote_required=true` and `independent_c_consent_required=true`: budget approval for B3 or legacy TB cannot authorize a new C call.
- `execution_status=not_started`, `paid_provider_calls=0`, `execution_authorized=false`. No automatic model, C taxonomy, critic or paid Deep Opus stage is started.
- Historical immutable source/video context SHA and approved B9 facts must be independently validated on every read; references cannot become new own-product claims.
- The **actual production C run machinery does not yet recognize this new mode** in its cost/start/advance state machine. A later integration stage must explicitly wire fresh quote/consent and the verified B9 payload to C run creation; never substitute `approved_project_context_v1` or alter legacy `legacy_questionnaire_v1` implicitly.

This staged boundary gives C an executable, schema-compatible taxonomy prompt **without bypassing billing, roles or B8 approval**, but does *not* claim complete production C operation.

## Database and code

Migration `052_ke_knowledge_snapshot.sql` adds:

- `knowledge_approved_snapshots`: one immutable human-approved knowledge snapshot per session, pinning B8 submission, B7 gate, approved A Context and source/video refs; project scope, counts and SHA.
- `knowledge_c_handoffs`: separate append-only C preflight payload with strong composite FK to B9 ID + SHA, idempotent replay and no billed or authorized calls.

Python: `intelligence/knowledge_extraction/knowledge_snapshot.py` has pure `build_snapshot`, `build_c_handoff` and the repository. `intelligence/taxonomy_builder/knowledge_binding.py` provides the read-only taxonomy-v2 adapter.

## Internal API

    snapshots=KnowledgeSnapshotRepository(connection_factory=...)
    published=snapshots.publish(project_id,submitted_review_session_id,authenticated_reviewer_id)
    historical=snapshots.materialize(project_id,submitted_review_session_id,authenticated_reviewer_id)
    snapshots.prepare_c_handoff(project_id,submitted_review_session_id,authenticated_reviewer_id)
    bound=snapshots.materialize_c_handoff(project_id,submitted_review_session_id,authenticated_reviewer_id)

    from intelligence.taxonomy_builder.knowledge_binding import preview_knowledge_for_c
    generation_input=preview_knowledge_for_c(
        project_id,submitted_review_session_id,authenticated_reviewer_id,connection_factory=...)
    # Prepared taxonomy-v2 messages ONLY. No provider call / C run start.

## QA

    python -m unittest discover -s tests -p 'test_ke_stage13_knowledge_snapshot.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

Pure + PostgreSQL17 tests check rejection of unsubmitted/all-rejected/blocked review, tampered quote/hash/source scope, missing video SHA, duplicate citation IDs, Full/Multi Pack, human edits, stable C taxonomy schema, independent consent declarations, cross-project/reviewer isolation, historical replay after profile revision, idempotency and immutable writes. All model replies in CI are local mocks. No paid calls.

## Stage12 CI caveat / next qualification

Stage12 final Linux CI `40188892099` passed 1380 Python tests and 47 migrations. Its final Windows CI `40188892216` was **cancelled**, NOT passed. This stage's new Linux+Windows CI provides a fresh opportunity to verify Stage12 + Stage13 together; do not claim both OS green until exact final HEAD succeeds.

Stage14 must close release qualification and hook an **independent C quote/consent/start** flow behind an authenticated reviewer UI/server adapter, with no double extraction or automatic paid Opus calls. PR #30 stays Draft, target A branch, not merged into main.
