# SRE M16 — Local Semantic Embedding Model: Pinned ONNX CPU Engine

**2026-10-26 · Branch:** \`feature/sre-production-v1-20261023\` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Implementation:** production local-only backend and strict contracts committed. **Acceptance: REAL-INFERENCE BLOCKED until a genuine, independently pinned pretrained ONNX model is provisioned and exercised in the controlled environment.** Existing GitHub CI is queued; fast tests are NOT real model inference. No cloud embedding, paid provider, hidden download, credential use, worker dispatch, migration or main merge.

## Scope and boundaries

M16 implements **a real local model execution engine**, not TF-IDF, hash-based scoring, simulated classification, or cloud embeddings. M17 will implement all-leaf C9 ranking, M18 tenant-specific embedding cache/revocation, M19 full C9 classifier context and prompt isolation. This stage's numeric cosine helper is **not** a semantic label; it never emits \`match\`, \`no_match\`, \`uncertain\` or drops low-scoring C9 hypotheses. Future retrieval **must** call M06/S2 approved C9 and M08 D1-authoritative rights gates, and read exact pinned live inputs before embedding. M16 never grants permission to share source content externally.

## Implemented files

- \`intelligence/relevance/local_embedding.py\`: \`validate_local_model_pins\`, \`verify_local_model_files\`, \`load_local_embedding_backend\`, \`probe_local_embedding_model\`, \`normalize_real_vectors\`, \`cosine_similarity\`.
- \`intelligence/relevance/local_embedding_health.py\`: offline/local-only CLI; exit **0 only when real verified repeated inference succeeds**, exit 3 with \`status=blocked\` when missing/invalid model, checksum, dependencies or runtime.
- \`intelligence/relevance/requirements-local-embedding.txt\`: optional exact versions of CPU ONNX Runtime, Hugging Face \`tokenizers\` (local JSON parsing, **no hub client**) and NumPy; the base SRE dependencies and default CI aren't implicitly modified.
- \`tests/test_sre_local_embedding.py\`: fast no-optional-dependency negative contracts for model/revision/SHA/extra assets, paths, invalid tensors, nonfinite output, no cloud and fake backend bypass.
- \`tests/test_sre_local_embedding_real.py\`: separate **real inference acceptance**. It requires operator-provisioned pinned pretrained ONNX weights and tokenizer, validates repeated deterministic CPU inference and verifies semantically similar sentences rank above unrelated sentences. An environment without the model is **explicitly skipped and marked BLOCKED**, never accepted as a passing M16 DoD.
- \`.github/workflows/sre-contracts.yml\`: fast unit suite on Linux and Windows, no remote model download or cloud embeddings. Full real acceptance remains independently required.

## Strict model identity: supplied by trusted deployment owner

The operator, NOT an HTTP client or incoming video, must select a real, vetted model repository, a **full immutable upstream 40-character hexadecimal commit revision**, local model ONNX file and tokenizer JSON, and exact SHA-256 of both files. The runtime requires this exact dictionary, with no unknown or missing keys:

\`\`\`json
{
  "schema_version": "sre-local-embedding/v1",
  "model_name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
  "revision": "<replace with ACTUAL vetted 40-char repository revision>",
  "backend": "onnxruntime-cpu",
  "model_sha256": "<replace with ACTUAL model.onnx SHA-256>",
  "tokenizer_sha256": "<replace with ACTUAL tokenizer.json SHA-256>",
  "output_name": "last_hidden_state",
  "pooling": "masked_mean",
  "dimensions": 384,
  "max_tokens": 128,
  "pad_token": "[PAD]"
}
\`\`\`

**Template is illustrative, NOT a verified/pinned model manifest!** Never copy placeholder values to production. Validate ONNX output names/embedding dimension/pooling against that exact pretrained model export; no ad hoc defaults, model auto-resolution or alternative unverified weights are allowed.

The trusted *local* directory must contain **exactly** \`model.onnx\` and \`tokenizer.json\`, with no symlinks, no network URL, and no ONNX external-data sidecar. Both artifacts must pass streaming checksum verification, file-size caps and exact revision/name checks before optional runtime packages are imported or CPU execution starts. SHA file verification establishes integrity relative to the operator's signed/configured expected digests; it does not by itself prove pretrained semantics or historical publication authority.

## CPU deterministic inference

The adapter runs ONNX Runtime with **\`CPUExecutionProvider\` only**, one intra/inter operation thread, sequential execution, graph optimizations disabled. The local \`tokenizers.Tokenizer.from_file\` uses the exact pinned JSON, explicit \`[PAD]\` ID from that vocabulary and bounded truncation/padding. Only \`input_ids\`, \`attention_mask\` and optional \`token_type_ids\` model inputs are permitted. A declared \`last_hidden_state\`/\`token_embeddings\` output uses **attention-mask-weighted mean pooling**; an explicitly declared 2-D \`sentence_embedding\` output uses the model's own sentence pooling. Every output is checked for exact batch/shape/dimensions, NaN/Inf, zero norm, strict float/int (not bool), then L2-normalized.

Up to **32 texts** per batch and 8192 Unicode codepoints per text; Unicode NFC is normalized; no hidden text replacement/fallback. Outputs include \`model_identity_sha256\`, \`revision\`, canonical input/vector SHA, vectors and \`real_model=True\` *only* through a verified loader. Direct public construction of an unverified/mock model session is blocked by a private verified-load constructor guard.

A real preflight runs two repeated small inference batches and compares every normalized component to a deterministic tolerance \`1e-6\`. \`probe_local_embedding_model(...,real_inference=False)\` returns **\`blocked/inference_unverified\`**, not \`ready\`.

## How to finish real M16 acceptance (no paid services)

In an isolated environment with a **real, independently vetted and locally supplied** ONNX semantic pretrained model:

1. Provision exact model revision and both files offline. Record their SHA digests in a trusted JSON pins file; do not place weights/credentials in Git or send them through a model API.
2. Install optional local dependencies in that controlled environment:

   \`python -m pip install -r intelligence/relevance/requirements-local-embedding.txt\`

3. Run real CPU health (must print \`status: ready\` and exit 0):

   \`python -m intelligence.relevance.local_embedding_health --model-dir /trusted/absolute/local-model --pins-file /trusted/model-pins.json\`

4. Run genuine semantic/reproducibility test (explicitly REQUIRES real assets):

\`\`\`bash
CM_SRE_M16_REAL_MODEL_REQUIRED=1 \
CM_SRE_M16_MODEL_DIR=/trusted/absolute/local-model \
CM_SRE_M16_PINS_FILE=/trusted/model-pins.json \
python -m unittest discover -s tests -p test_sre_local_embedding_real.py -v
\`\`\`

5. Record actual model name, exact SHA pins, test runner OS/CPU/runtime versions, exact code HEAD SHA and observed successful result in the tracker. **Only then may the REAL-INFERENCE DoD checkbox be marked complete.** If model absent, dependencies absent, checksums mismatch or a test is merely skipped, keep M16 marked \`BLOCKED\`.

Ordinary zero-cost contract suite (not a substitute for step 4):

\`\`\`bash
python -m unittest discover -s tests -p test_sre_local_embedding.py -v
\`\`\`

## Dependencies and security claims

The executable imports *neither* \`onnxruntime\`, \`tokenizers\` nor any model provider at module import time. All heavy dependencies are lazy after checksum checks. It does not call \`huggingface_hub\`, \`transformers\`, remote endpoints, downloads or OpenRouter. The operator must secure model provisioning and OS paths; remote-mounted filesystems and malicious privileged OS changes are not defensible by application-level SHA alone.

**Result:** implementation code complete, unit tests authored, real model inference not yet run in a controlled environment, GitHub CI overall queued. M16 DoD / acceptance stays **OPEN/BLOCKED**. Next M17 is independently scoped all-leaf similarity ranking, on request; a missing real model must not silently become a fake ranker.
