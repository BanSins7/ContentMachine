# SRE M17 — ALL-LEAF Local Similarity Ranking / Independent Exclusions

**2026-10-26** · Branch: \`feature/sre-production-v1-20261023\` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Implementation:** code committed. **QA:** fast unit and PostgreSQL17 test sources committed; GitHub Actions not yet observed SUCCESS. **Real semantic acceptance remains BLOCKED** until independently pinned M16 pretrained ONNX weights and tokenizer actually run in the controlled CPU environment. No cloud models, paid provider calls, unapproved taxonomy, main merge, or DB write.

## Scope

M17 ranks **every** hypothesis leaf in the exact, H1/H2-reviewed C9 deep publication, using D1/S3 media content with valid source permissions and a **real verified local M16 ONNX session**. The ranking is a navigation hint for later live classifier stages; it is **not** a verdict, nor an instruction to omit a category. \`match\`, \`no_match\`, and \`uncertain\` are reserved for later verified classification. All rows remain \`classification_state="not_evaluated"\`, including negative cosine, irrelevant, or excluded-looking leaves.

New module \`intelligence/relevance/live_leaf_ranking.py\`:

- \`prepare_c9_leaf_features(approved_c9)\`: pure **shape-only** derivation from the **complete** 11-field approved snapshot. The sole runtime authority comes from \`read_authorized_taxonomy(conn, project_id, exact_publication)\`, with the S2 PostgreSQL H1/H2 lineage and real SRE mirrored leaf keys. A self-signed C9 JSON bundle cannot authenticate a publisher or confer rights.
- \`prepare_d1_video_text(content)\`: exact observed D1/S3 title, description, hashtags, transcript, and source-backed scenes only where the snapshot has explicit rights; no invented transcript, video caption, missing-media inference, or search-query shortcut. Oversized texts **fail closed** instead of truncating source facts/segments silently.
- \`rank_all_leaf_vectors(features, video_vector, positive_vectors, exclusion_vectors, expected_hypothesis_keys=..., model_identity_sha256=..., content_sha256=...)\`: pure **transport mathematics** for 1–48 leaves. It checks the complete **independently supplied** C9 expected set, exact positive set, exact exclusion-feature set, numeric finite vectors, stable tie break, and zero missing/extra pairs; it does not claim that the test vectors came from a real model.
- \`rank_live_run_item(conn, project_id, run_id, item_id, model_directory=..., model_pins=...)\`: **trusted server entrypoint**, requiring separately authenticated project owner and a consistent DB snapshot managed by the caller. Reads only the matching project/run/item from LIVE tables, verifies M12 immutable manifest/hash, publication, taxonomy/pipeline/context pins and expected leaf set, rechecks exact C9 approval and D1 source current rights/content version/source policy/observation. **Only after successful DB verification** loads a SHA-verified real CPU ONNX M16 model from operator-provisioned local weights. No fake vector fallback, synthetic scoring, OpenRouter, HTTP or persisted classification.

## Positive meaning, exclusions, hierarchical context

For each C9 leaf, a **positive** text is constructed from the ordered root \`direction\` → optional \`branch\` → \`hypothesis\` nodes (the exact names, definitions and include rules), plus **all explicitly approved source facts** referenced by their ancestry. This preserves the full deep hierarchy and direct/branch route.

An independent **exclusion text** uses the explicitly reviewed \`exclude\` rules of the same nodes. Exclusions are **not concatenated into positive semantic meaning**; instead M16 produces a second embedding if and only if exclusion rules exist. No negative text means \`exclusion_similarity = null\`, not fabricated zero evidence.

Per hypothesis:

\`\`\`text
positive_similarity  = cosine(real_video_vector, real_positive_context_vector)
exclusion_similarity = cosine(real_video_vector, real_exclusion_vector) | null
exclusion_penalty    = 0.25 * max(0, exclusion_similarity)   (only if exists)
rank_score           = positive_similarity - exclusion_penalty
\`\`\`

The coefficient 0.25 is a **fixed deterministic retrieval heuristic**, **not** a calibrated classifier threshold; it never gates, excludes or declares a category irrelevant. Actual calibrated relevance and evidence evaluation are separate stages. Ranking sort is \`(-rank_score, category_key)\`: ties resolve by ascending canonical approved C9 UUID, independent of input order. No \`top_k\`, min_similarity, global cut-off, truncate-to-N, candidate-drop mode or \`no_match\` coercion.

Returned \`sre-all-leaf-ranking/v1\` includes project/run/item and provenance pins, model identity SHA/revision (via M16), content SHA, approved C9 bundle/context hash, result hash, the rank of **every leaf** and its positive/negative scores, ancestry IDs, context SHA and \`classification_state="not_evaluated"\`. There is no raw D1 text in the returned ranking. \`dropped_count=0\`, \`candidate_count=expected_leaf_count\`, \`dispatch_allowed=false\`, \`paid_calls=0\`.

The backend chunks real inference inputs into groups of ≤32 as required by M16 and checks model identity and per-chunk input/vector SHA against each return; the results must contain exactly one vector for every positive/negative part, including 48-leaf maximum. A mock or other adapter cannot pass the strict backend type check. Failure to load genuine local model **BLOCKS** the entire ranking, rather than scoring via keywords.

## QA and acceptance truth

- \`tests/test_sre_live_leaf_ranking.py\`: no-provider/no-real-model **pure ranking math** with the repository's approved C9 fixture: 3 leaves and 48-leaf stress; full ancestry and fact/negative separation, ties by UUID, all low/negative cosine candidates retained, no \`no_match\`, fail-closed incomplete/duplicate/foreign C9 and vector matrices, malformed/oversized D1 fields, tenant denial before attempting any model load.
- \`tests/test_sre_offline_pg.py\`: **actual disposable PG17** two-human-approved C9 and project-scoped D1; with no real M16 weights, the only accepted outcome is \`local_model_blocked\` after valid authority checks; foreign project or revoked D1 source **must fail before** model load; no classification or paid attempt created.
- \`tests/test_sre_live_leaf_ranking_real.py\`: separate genuine ONNX ranking test, requires \`CM_SRE_M16_REAL_MODEL_REQUIRED=1\`, actual locally pinned M16 files and optional CPU runtime; builds a real C9 fixture and actual model-derived vectors for **all** approved leaf contexts; tests reproducibility, no drop and no false semantic decisions. If assets absent, it skips with explicit **BLOCKED** message, which **does not count as passing the real M17 DoD**.
- \`.github/workflows/sre-contracts.yml\`: fast unit Linux/Windows, PostgreSQL17 negative rights gate; actual ONNX acceptance requires the controlled provisioned model environment and is NOT falsely declared passed by ordinary CI.

Run fast tests (only ranking math/authority contracts):

\`\`\`sh
python -m unittest discover -s tests -p test_sre_live_leaf_ranking.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

Run **required real M16/M17 acceptance** only on an offline controlled CPU host with operator-pinned pretrained model (see [M16 runbook](SRE_M16_LOCAL_EMBEDDING.md)):

\`\`\`sh
CM_SRE_M16_REAL_MODEL_REQUIRED=1 \
CM_SRE_M16_MODEL_DIR=/trusted/absolute/local-model \
CM_SRE_M16_PINS_FILE=/trusted/model-pins.json \
python -m unittest discover -s tests -p test_sre_live_leaf_ranking_real.py -v
\`\`\`

Record model name, revision, hashes, actual test results and exact SRE branch SHA before marking the real-semantic DoD completed. Pure mock vectors, skip, or queued GitHub Actions are NOT actual semantic inference.

**Result:** code implemented, source-level unit/PG tests integrated. M16 and M17 genuine pretrained ONNX CPU verification **BLOCKED pending independently approved files**. No historic SQL edited and no paid calls.

**Next M18 (only after another user request):** project/model/taxonomy/content pin-specific embedding cache, race-safe deduplication and source-policy revocation. Any cache hit must remain subject to the current authoritative D1 rights gate and actual model identity, never resurrecting rights-expired vectors.
