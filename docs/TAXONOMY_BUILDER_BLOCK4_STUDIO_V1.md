# Taxonomy Builder — Block 4: Pipeline Studio UI

Status: implemented in draft PR #7. No estimates, payment consent, or LLM execution in this block.

## What the editor does

After the project questionnaire is confirmed, Taxonomy Pipeline Studio opens with a live model/catalog connection. It replaces the earlier static preview.

The left sidebar contains Fact Extraction and Taxonomy Generation (mandatory, with mandatory human fact review between them), plus optional Taxonomy Critic. The right panel edits each LLM stage independently: OpenRouter model, reasoning mode/effort, temperature when supported by that model and reasoning choice, maximum output tokens. Prompts are pinned to stage-specific versions. Human review and deterministic validation cannot be disabled.

All twelve requested model candidates appear in the list. An explicit "Обновить каталог моделей" button fetches public OpenRouter metadata (not completions). Missing, stale, incompatible or unverified entries stay visible with explanations but cannot be approved. When refreshed catalog IDs change, the UI does not silently overwrite models selected in a saved draft; reselect and save is required.

## Persisted operations and honest flow

- Save draft calls POST /api/projects/{id}/taxonomy-builder/pipeline/draft with expected_draft_revision; server revalidates all settings and active intake.
- Approve calls POST /api/projects/{id}/taxonomy-builder/pipeline/approve only for the saved revision; the result is immutable.
- Any edit makes the state unsaved and disables approval until saved.
- Approved versions can be copied to a new, independently reviewed draft; history is not overwritten.
- The UI warns before leaving the Studio with unsaved changes through project navigation actions. A 409 version conflict never auto-overwrites server data.
- After approval the screen says that the price quote follows in Block 5. There is no fake price, consent button or launch action in Block 4.
- Source profile edits require new confirmation; old configuration remains tied to its original profile revision.

## Files and test surface

- site/index.html — new stage settings, accessible form labels, history area and confirmation controls.
- site/taxonomy-studio.js — isolated controller using the existing backend API.
- site/projects.js — passes the current project into Studio and checks unsaved changes on project navigation.
- site/style.css — neutral dark workspace, responsive stage column and editor, focus-visible.
- app.py — serves taxonomy-studio.js as a static resource.
- intelligence/taxonomy_builder/pipeline_repository.py and intake.py — show actual Studio availability.
- tests/test_taxonomy_studio_ui.cjs — Node.js behavioral UI test with a fake local API, including per-stage model selection, supported temperature, save/approve/version-history, and a guard against paid calls.
- .github/workflows/tests.yml — runs UI test and JS syntax checks as well as PostgreSQL/Python tests.

## Manual local verification

1. Start ContentMachine with PostgreSQL enabled and migrations applied.
2. Create or open a Health & Fitness project, complete functions and pains, and approve its questionnaire.
3. Open Taxonomy Pipeline Studio and explicitly refresh the OpenRouter catalog (requires external metadata access). If all candidate models are unavailable, do not treat that as a working LLM setup.
4. Select a verified model on each mandatory stage, vary reasoning, adjust temperature only when enabled.
5. Optionally enable Critic, choose its own model/settings.
6. Save the draft then approve. Check immutable history.
7. Change one setting, verify that approval is invalidated for the new unsaved draft.
8. Open a previous approved version to copy its settings. Editing the source questionnaire must require renewed confirmation.

Automated tests cannot prove pixel-perfect rendering on the user's computer. No LLM costs occur during this block.

Next: Block 5 (quote, consent); Block 6 (provider calls and actual Taxonomy Builder). Stop after Block 4 and wait for the user's instruction.
