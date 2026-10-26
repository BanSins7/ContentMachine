# Content Machine · SP-06: offline pinned semantic embeddings — implementation and operations

**Date:** 2026-10-26  
**Working branch:** `feature/search-planner-full-integration`  
**Status:** code + offline security tests implemented. **Not release-signed** until focused/full Linux+Windows CI and an actual local reviewed multilingual model passes smoke/E2E in a provisioned environment.

## 1. Exact scope and trust boundaries

Production Query Validation uses a real local `SentenceTransformerEmbeddingProvider` and **never** silently falls back to `HashingTestEmbeddings`, OpenRouter embeddings, fake cosine or Hub downloads. The manifest records:
- `schema_version: search-planner-embedding-model/v1`
- upstream `owner/model` ID asserted by operator;
- exact 40-character lowercase Git commit/revision asserted by operator;
- exact vector dimension;
- SHA-256 of **every** file in the already present model directory, sorted by portable relative path.

`model_key = st-local-sha256:<sha256(canonical(manifest))>`. The model key is **derived from checked bytes** and is the existing immutable `qv_runs.embedding_model_key` pin; there is no new SQL or `validated_queries/v1` change. The manifest excludes only itself from the artifact set. Missing/added/mutated files, nested symlinks, invalid paths, wrong revision shape, incorrect dimensions, or a mismatched expected key prevent construction.

**Important provenance limit:** local SHA checks prove byte-level integrity relative to the operator's recorded manifest. They do NOT independently prove that the asserted upstream Git SHA is genuinely from the claimed model repository, or that the model is multilingual/relevant for a target domain. Operators must confirm this separately against a trusted upstream source and run representative language/evaluation tests.

## 2. Operator-managed provisioning, no automatic download

1. On a controlled preparation machine, select a multilingual sentence-transformer suitable for the target locales and verify exact upstream model repo + immutable Git revision. Example model family (not hardcoded): `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
2. Download to a reviewed **local** folder outside the production application. Include `modules.json`, tokenizer/config/pooling/weight files. All required artifacts must be actual files, **not symlink references**. No app startup operation performs this download.
3. In the isolated embedding-worker environment, install from the explicitly optional pinned dependency file. Pin transitive PyTorch/Hugging Face packages and wheels in a deployment-specific lock if cross-host binary reproducibility is required:

   ```bash
   python -m pip install -r intelligence/search_planner/query_validation/requirements-embeddings.txt
   ```

4. Once the bytes and upstream revision are independently verified, generate the local manifest **offline** (insert the genuine commit SHA and actual embedding dimension, not placeholders):

   ```bash
   python -m intelligence.search_planner.query_validation.provision_model \
      --model-dir /opt/contentmachine/models/multilingual \
      --write \
      --model-id sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
      --revision <YOUR_VERIFIED_40_HEX_UPSTREAM_COMMIT> \
      --dimensions 384
   ```

5. Before startup, verify the same folder without any network call:

   ```bash
   python -m intelligence.search_planner.query_validation.provision_model \
       --model-dir /opt/contentmachine/models/multilingual
   ```

6. Construct a verified local provider:

   ```python
   from intelligence.search_planner.query_validation import (
       SentenceTransformerEmbeddingProvider, validate_generation_outbox,
       DEFAULT_POLICY,
   )

   provider = SentenceTransformerEmbeddingProvider(
       "/opt/contentmachine/models/multilingual",
       # optional model_key=... from prior verified CLI output
   )
   result = validate_generation_outbox(
       project_id, sqg_source_outbox_id,
       embedding_provider=provider, policy=DEFAULT_POLICY,
   )
   ```

Under the hood, runtime uses `SentenceTransformer(local_path, local_files_only=True, trust_remote_code=False)`. It checks the model's reported embedding dimensions against manifest. The bytes are rehashed **after loading**, **before validation**, and **after inference before PostgreSQL QV commit**. If drift occurs, do not ACK source SQG outbox. No model bytes, torch checkpoints or bundled weights are committed to GitHub.

## 3. Additional hardening

- `query_validation/service.py`: for `semantic_local`, accept only exact `SentenceTransformerEmbeddingProvider` instance whose `verify_runtime_pin()` reproduces `model_key`; reject arbitrary injected objects with a spoofed `mode="semantic_local"`.
- For `test_fixture`, accept only exact `HashingTestEmbeddings` and `allow_test_embeddings=True`. Production `get_validated_bundle` and `ack_validated_outbox` restrictions from SP8 remain intact. No fixture can be published as a production validated output merely by spoofing a mode string.
- `encode_checked` maintains bounded batches, finite/nonzero vectors, consistent dimensionality and normalized embeddings; all existing QV/selection contracts remain V1-compatible.
- Hashes are computed streaming (1 MiB blocks) without loading entire weight files into Python memory. Verification may take time for large models; do not omit pin verification for throughput.
- The optional dependency is **not** installed by `intelligence/requirements.txt` in default app/CI; the base Search Planner is importable without torch, sentence-transformers, pretrained weights or Collector.
- QV remains a manually invoked/background-worker function (actual worker supervision belongs to SP-15); no process launches just because the model is configured.

## 4. Files changed

| Path | Change |
| --- | --- |
| `intelligence/search_planner/query_validation/model_manifest.py` | Build/check immutable local manifest, stream SHA over all files, reject unpinned/symlinked payload |
| `intelligence/search_planner/query_validation/provision_model.py` | Operator-only offline create-once/verify CLI |
| `intelligence/search_planner/query_validation/embedding.py` | Strict production local loader, derived key, dimension check and ongoing pin verification |
| `intelligence/search_planner/query_validation/service.py` | Strict actual production provider gate; verify model before and after encoding |
| `intelligence/search_planner/query_validation/requirements-embeddings.txt` | Optional library version pin, not installed in default CI |
| `tests/test_search_planner_stage06_embeddings.py` | Temp files and mocked SentenceTransformer offline, tampering, symlinks, drift, model impersonation, fixture opt-in |
| `.github/workflows/sp-stage01-fast.yml` | Adds SP-06 QA without model downloads/paid calls |

## 5. QA commands and release gate

```bash
python -m unittest discover -s tests -p 'test_search_planner_stage06_embeddings.py' -v
python -m unittest discover -s tests -p 'test_search_planner_query_validation.py' -v
python -m compileall -q intelligence/search_planner/query_validation
```

**Not yet proven:** exact-head green GitHub Actions, actual multilingual model bytes tested in production-like offline environment with representative Russian/English inputs, model quality/threshold calibration on labeled data, fresh PostgreSQL17/upgrade and full real QV result E2E. A test fake loader is not a real semantic inference benchmark. Leave SP-06 overall open until these pass.

The base SQL migration `016_query_validation.sql` is unchanged. No changes to `main`, Video Collector or C Corrector branches. PR #34 remains DRAFT.
