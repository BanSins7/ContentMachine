# A → B → C compatibility and ownership matrix (B Stage01)

Verified against A head `1970cd0620e826542c4cae70db4d4cf8f824a607`.

| Layer | Real existing location / input | Owner and behavior | New B target | Contract / status |
|---|---|---|---|---|
| A source registry | `project_sources`, `project_source_versions`; stages 030–031 | A: source and version identity, status and metadata | B1 source resolver | Reuse, NEVER modify |
| A document text | `source_processing_outputs`; stage 033 | A: PDF/DOCX/etc extraction | B1 fragment inventory | Resolve pinned output; no reparse |
| A model-reported web | stage 034 `web-source-extract/v1` | A: Sonnet URL processing; model-reported text | B1/B2 text input | Preserve `model_reported_unverified` |
| A media | stages 035–038 | A: platform adapters / transcript / Vision / Fusion | B1 video pin → B9 → C | `video-intelligence/v1`; B3 explicitly bypasses |
| A technical Evidence | `project_evidence` / grounding; stage 039 | A: exact quote, locator, immutable origin, Evidence review | B1 verify, B2 text references, B5 quote check | A Evidence ID ≠ B Fact ID; no semantic truth claims |
| A Context builder | `intelligence/project_sources/context_builder.py` | A: nested `project-context/v1`; limited inline manifest | B1 approved envelope | Real payload does not contain context ID |
| A Context service | `context_service._snapshot` / stage 040 | A: external approval, pinned links, SHA, source versions | B1 source boundary | `context_envelope_v1.json` and `validate_context_envelope` |
| A theoretical DTO | `project_sources/contracts.py:ProjectContextSnapshot` | A: not the published Stage11 shape; includes optional `approved_fact_ids` | None | Compatibility mismatch documented; not accepted as B intake |
| A old RTB Context | `taxonomy_builder/context_binding.py` / 041 | Legacy `approved_project_context_v1`: selected Evidence for RTB | None | Keep old run/history; no change |
| A old fact extraction/review | `run_repository.py`, `run_contracts.py` | Existing RTB: facts-context-v1, quote binding, human review | B3/B4/B5/B8 separate | Reuse provider components only; no double run |
| A model/provider/budget | `provider.py`, `cost_repository.py` | Existing OpenRouter adapter, provider uncertainties, consent | B3 new run/ledger | Separate consent, pinned prices, atomic attempt states |
| B1 | Not yet implemented | B: immutable resolver / readiness / source roles | Stages02–03 | Protocol only in Stage01 |
| B2 | Not yet implemented | B: lossless text packs, deterministic manifests | Stages04–05 | No reuse of lossy A12 Evidence selector for completeness |
| B3 | Not yet implemented | B: MiMo fact proposals from text packs ONLY | Stages06–07 | `facts-v3`, new explicit model snapshot |
| B4–B7 | Not yet implemented | B: Python normalization, grounding, dedup, quality | Stages08–11 | Neither A Evidence nor old RTB validator substitutes |
| B8 | Not yet implemented | B: human-approved facts, immutable decisions | Stage12 | Independent from A Context approval and legacy RTB review |
| B9 | Not yet implemented | B: immutable KnowledgeSnapshot, pinned A video refs | Stage13 | Separate version and SHA |
| C existing Opus | `taxonomy_builder/deep_repository.py` | Existing 2-pass Claude Opus 5.5 + human reviews | Stage13 C adapter | New `knowledge_snapshot_v1`; no duplicate MiMo |
| C existing fixed goal | `taxonomy_builder/run_repository.py` | Legacy fixed research goal `maximize_views` | New input goal contract | Preserve original user goal and explicitly pinned C goal |

## Frozen names

- A payload `project-context/v1` **already exists**.
- B input boundary `ke-a-context-envelope/1`; JSON Schema
  `intelligence/knowledge_extraction/schemas/context_envelope_v1.json`.
- A fusion output `video-intelligence/v1`. Historical generic DTO
  `video-analysis/v1` is NOT a substitute.
- B future pack `evidence-pack/v1`, B future fact proposals `facts-v3`,
  B future snapshot `knowledge-snapshot/v1` (not yet implemented).
- New C input mode `knowledge_snapshot_v1`; legacy modes
  `legacy_questionnaire_v1` and `approved_project_context_v1` remain.
- SQL slots 042+ reserved for future stage migrations; **none in Stage01**.

## Remaining items intentionally deferred

- Actual B1 SQL access, artifact/MP4 hash and current-profile staleness
  checks require Stage02; in-memory validation is not sufficient.
- Text fragment enumeration, document range proof, individual competitor
  `subject_id` and B1 persistence require Stage03.
- Output selection 100% coverage and model token budgeting require Stage04–05.
- Reviewable B Facts and Knowledge snapshots do not exist in Stage01.
- Rights policy conflict requires the owner's separate decision before
  claiming any B-specific rights/revocation enforcement.
- The source plan refers to a separate 746-line detailed B1 spec which is
  not in the provided project attachments; no unseen specifics inferred.
