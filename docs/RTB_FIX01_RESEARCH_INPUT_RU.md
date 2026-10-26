# RTB-FIX-01 — Frozen Research Input A8+B9 (read-only)

Repository: BanSins7/ContentMachine
Working branch: feature/rtb-module-v2, PR #32 (Draft).
Code ownership: intelligence/taxonomy_builder/research_input.py. Main, Knowledge Extraction, Project Source Intelligence, Search Planner and SRE are not edited.

## 1. Intent and immutable authority

This stage creates a repeatable, **unpaid read-only** Research Input for Deep V2. It does NOT create a paid run, save an RTB C session, dispatch a model, modify a human approval, or publish a taxonomy. In FIX-02 the exact research_input_sha256 must be persisted in the C-run/quote; no implicit recomputation from "latest" data after a run has started.

Upstream authoritative interfaces (already merged into main):

- B9: KnowledgeSnapshotRepository.export_by_id(project_id, snapshot_id, reviewer_id) replays and verifies B8 human decisions and stored knowledge-snapshot/v1; returns exact UUID, SHA and JSON. It revalidates reviewer authorization.
- B9: KnowledgeSnapshotRepository.materialize_c_handoff(project_id, session_id, reviewer_id) validates the already persisted knowledge-c-handoff/v1. The service **does not** call prepare_c_handoff, so a missing handoff cannot be automatically created.
- A8: PostgresAResolver(connection_factory, require_current=False, verify_local_files=True).resolve_context(project_id, context_id) resolves a user-approved immutable A8 context, rechecks registered source/output/evidence pins and the original SHA.
- A8: PostgresAResolver.resolve_video(project_id, context_id, VideoAnalysisPin) reads exact output ID, source and source-version tuple, schema version and output_sha256; refuses a missing/mutated original.
- RTB: checked_b9_mapping() verifies the exact deterministic B9 handoff and recalculates every approved B9 SHA fact identity from its human review/source group hashes.

The resolver has require_current=False **intentionally**: an explicitly pinned historical approved A8 context remains available when a project later changes sources or intake, provided all immutable pinned materials and original reviews pass verification. There is no latest-snapshot fallback.

## 2. Research Input contract

Schema: rtb-research-input/v2; builder: rtb-research-materialization/1.0.0.

- project_id, input_mode=knowledge_snapshot_v1
- B9 immutable snapshot ID/SHA, saved handoff ID/SHA
- A immutable context ID/SHA and explicit approval decision
- b9_fact_sha_to_sre_uuid (project-specific deterministic UUID5)
- video_analysis_refs: original, typed five-field A output pins
- c1_approved_facts: ALL approved B9 reviewed facts, exactly preserving reviewed_text, kind, source_role, subject_scope/ID, signed group IDs and citations; field may_support_own_product_claim is true only for function/product_type of own_product/own_product
- c2_original_video_observations: all original A evidence entries referenced by their immutable output_id::evidence_id locator, exact original_evidence, original timing and trust. Video evidence is explicitly NOT B9-approved product truth
- deterministic research_input_sha256 = canonical SHA of every above field except the hash itself
- independent execution flags: paid_execution_authorized=false, provider_calls=0, research_goal=null; the B9 approved facts are never reconverted to a MiMo source

A8 and B9 video pin sets must be exactly equal. Missing or extra outputs, changed hashes, cross-project identities or unapproved context block materialization.

No provider call is made from a GET/preview. The service does not mutate upstream records or include transient timestamps in its SHA.

## 3. Fail-closed limits (no silent truncation)

MAX_FACTS=400, MAX_VIDEOS=24, MAX_OBSERVATIONS=2400,
MAX_OBSERVATION_BYTES=48000, MAX_INPUT_BYTES=1200000 (UTF-8 canonical JSON).

If a perfectly valid but larger B9/A dataset arrives, the service returns a stable error, **not** an incomplete Research Input. These are preliminary RTB context constraints; later FIX-02 must prove provider context window safety against the exact full frozen input and require explicit rerun/new consent if configuration changes. They are not claims about universal OpenRouter token limits.

No sampling, summarization, sanitizing, deduplicating or translating input evidence occurs here.

## 4. Entrypoints

- RTBResearchInputService.preview(project_id, snapshot_id, reviewer_id) is the trusted internal (server-side) entrypoint. Project and signed snapshot ID are explicit and required.
- build_research_input(snapshot, handoff, handoff_id, handoff_sha256, context_envelope, resolved_videos) is a deterministic pure builder for verified inputs. It is **not** a public authorization method: unsigned request JSON must never call it directly.
- Actual API route/UI action will be introduced only after the paid C-run service and server-authenticated access paths are defined. No premature unauthenticated HTTP endpoint is added.

## 5. Tests and CI

Offline tests:
- tests/test_taxonomy_c_corrector_contracts.py (real B9 build_snapshot two human-approved groups + signed A evidence fixtures)
- tests/test_taxonomy_research_input.py: exact pins, SHA determinism, source immutability, 0 videos, foreign project, stale content, forged video, oversize, missing persisted handoff, fake service and nonpaid preview.

PostgreSQL17 test:
- tests/test_taxonomy_research_input_db.py: sets up real B9 publication/handoff through existing Stage13 synthetic fixture, exercises RTBResearchInputService on real database reads and confirms no C/legacy/Deep paid-run records are inserted; denies cross-project/revoked reviewer.

CI:
- .github/workflows/rtb-module-contracts.yml: Linux/Windows offline tests, Linux PostgreSQL17 integration and Deep V1 compatibility.
- .github/workflows/tests.yml: all Python regressions, PostgreSQL17 migrations, Node UI, compile check.
- .github/workflows/ke-stage14-fast.yml: prior B9/C flat compatibility.

As of authoring, GitHub workflows may remain queued; do not label full QA PASS until the actual conclusion is successful on the latest commit. No live paid OpenRouter calls are part of FIX-01.

## 6. Exact remaining integration work

FIX-01 stops at verified read-only frozen output. FIX-02 introduces a **separately authorized** Deep V2 quote/consent/run, immutably ties the run to the exact research_input_sha256 and binds model/prompt/config and all paid stages. FIX-03 validates 14-field output and source observation references. FIX-07 publishes V2 atomically, reuses SRE facts, and exposes the 11-field core. FIX-08 serves downstream Planner via RTB-owned versioned API/contract.

No changes to main have been made for FIX-01.
