# Project Source Intelligence — Stage 12: Evidence-aware RTB Integration

Stage12 extends the existing flat Taxonomy Builder. The established questionnaire-only mode remains the default, with unchanged historical runs, snapshots, publications, Review controls and database rows. It does **not** merge this PR into main or implement the Stage13 UI.

## Entry points and intentional cost controls

1. Assemble and approve a Stage11 immutable ProjectContext (decision `approved` or explicitly `approved_partial`). Grounded Evidence must already have its **own** Stage10 `approved` review decision, distinct from approving the Context container.
2. Approve the unchanged current questionnaire and Pipeline Studio model configuration as usual. The current RTB still uses legacy Health & Fitness research objective `maximize_views` by design; user business `goal` is retained as context, not silently substituted into the research objective.
3. `POST /api/projects/{project_id}/taxonomy-builder/cost/quote` with body:
   `{"pipeline_id":"<approved pipeline UUID>","context_id":"<approved context UUID>","token_budget":1200,"force_new":true}`.
   No `context_id` means the previous text-only mode; existing clients are unaffected.
4. The quote includes `input_mode=approved_project_context_v1` and `context_binding` with `context_id`, immutable context SHA, approval ID/decision, deterministic Evidence selection SHA, selector version, selected Evidence IDs, count/excluded IDs and token budget. This is an indicative price scenario including actual **selected evidence text and citations**, not raw document/media or Vision fees incurred in earlier stages. Costs are not a hard provider spending cap.
5. Existing explicit `POST .../cost/consent` requires authorization for **this** quote and is single-run. Follow with `POST .../runs` supplying the exact quote ID, consent ID and idempotency key.
   Before quote/consent/run creation the Context must still be current. **After a run has been authorized**, its subsequent LLM stages and human reviews replay the exact immutable snapshot pins even if newer project sources are added; historical results are never silently rebound to the new profile.
6. Each manual `POST .../runs/{run_id}/advance` revalidates the immutable quote→run→context chain, Stage11 output/quote/locator pin hashes, current project profile, and deterministic Evidence selection before authorizing the **single** model request. Unknown billing outcomes do not trigger automatic retry.
7. In Context mode, facts use schema `facts-context-v1`: `kind`, `text`, `source_evidence_id`, `source_quote`. The server checks the supplied ID against only the chosen immutable Evidence rows and requires an exact case-sensitive substring. Invalid/duplicate claims go to `excluded_facts`; if none is valid the model stage fails instead of allowing human approval of fake evidence.
8. Human Fact Review requires **every** candidate to be approved or rejected. Context mode deliberately prohibits ungrounded `add` and unverifiable `edit`. Approved records preserve evidence kind, trust, original source ID/version, quote, citation and knowledge scope; generation reuses exactly those reviewed UUIDs.
9. Optional creative references can be supplied to taxonomy generation for format inspiration, **not as proof** of product functions or performance. The existing strict taxonomy validator, human topic review and immutable publication remain unchanged.

## Scope and source trust

- Stage12 restricts business `function`/`product_type` evidence to **human-approved user claims in operational-product scope**.
- Human-approved external claims with scope `operational_product` or `general_research` can generate **reviewable** audience/problem candidates only. External text never automatically verifies product capabilities.
- Vision/OCR observations, model inferences and video references may inform creative analysis but cannot certify sales, reach or product capabilities.
- OpenRouter Sonnet-reported web page text remains `model_reported_unverified`; a matching quote proves only that the phrase occurred in the **stored model-produced text**, not in independently fetched HTTP bytes.
- Only fragments chosen by the bounded selector enter LLM requests; a context approval does not guarantee that every uploaded page or video scene was included. `selected_evidence_ids` and exclusions are recorded in the quote and verified at execution.
- Stage11's deterministic selector uses a **character heuristic**, not exact tokenizer counts. Before each paid dispatch the existing provider adapter checks a conservative context-window estimate and reserves estimated maximum charge inside the authorized run budget.
- User goal and research objective remain separate; a generalized goal-driven Builder is not silently introduced into the legacy version.
- Source material is untrusted prompt data and no untrusted instructions are executed as tools. Citation and UUID validation is deterministic and server-side.

## Persistence

Migration `041_tb_approved_project_context.sql` adds `tb_context_quote_bindings` and `tb_context_run_bindings` with strict project-scoped composite foreign keys and update/delete rejection triggers; neither copies raw media nor changes existing migration checksums. Explicit link and hash checks fail closed if the approved context or current project inputs change. Historical selected Evidence remains referenceable via Stage11 immutable pins. Legacy mode has no binding row and retains previous handling.

Relevant code: `intelligence/taxonomy_builder/context_binding.py`, `run_contracts.py`, `provider.py`, `run_repository.py`, `cost_repository.py`, `cost_estimator.py`, and the additive `app.py` quote argument.

## QA / limitations

Offline tests: `tests/test_rtb_context_stage12.py`, `tests/test_rtb_context_stage12_db.py`. PostgreSQL17 integration includes real immutable context approval, reviewed Evidence selection, cross-table FK failure and stale profile rejection. Standard Linux/Windows CI runs migrations and full Python regression; provider calls remain mocked/uninvoked.

Not included: Stage13 dedicated Context-mode user interface, real paid OpenRouter experiments, semantic embeddings, human fact amendment workflow, new goal semantics, generalized non-Health & Fitness taxonomy, or actual media download in CI. Do not represent queued analysis or model-reported text as independently verified external evidence.
