# SRE M20 — Deterministic Opus Request & Local Batch Packer

**2026-10-27 · SRE branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Status:** code and Linux/Windows/PG17 QA suites authored and committed. **Actual CI success is unverified** until GitHub Actions report completion for exact last SHA. No paid Opus POST, no unapproved taxonomy, no money, no consent grant, no merge to `main`.

## Scope and authority

M20 converts **complete M19 classifier packets** into deterministic, revision-pinned Opus request and local work-batch manifests. It does **not** send anything to OpenRouter and does not represent a paid model execution. The server-only production path `pack_live_opus_requests(conn, project, run, item, operator_catalog_pin=...)` begins at M19's real project-scoped M12/approved-H1/H2-C9 and current D1/S3 provenance/rights reader. No user-supplied C9 or source text authenticates a project. The host must independently authenticate the project owner. Future dispatch **must** recheck permissions via M08 `before_external_send`, live run/token/generation, spending consent, price/budget/ledger and exact provider token support at send time.

Pure `pack_opus_requests(m19, operator_catalog_pin,...)` checks M19's SHA seals and shape, exact model catalog pin structure and all leaf UUID coverage. This pure function checks transport consistency only, **not** C9 authorization or catalog authenticity. Catalog values must be selected and reviewed by the trusted operator, not supplied by an HTTP caller. A correctly formatted `catalog_sha256` is not evidence that a model is currently available on a live provider.

## Implementation

**New `intelligence/relevance/opus_request_packer.py`**:
- Exact operator catalog identity `sre-opus-catalog-pin/v1`: `provider=openrouter`, `anthropic/claude-opus-...` model ID, fixed immutable `model_revision` and `catalog_revision`, `catalog_sha256`, context/output token limits, explicitly chosen input byte-proxy budget and deterministic requested `temperature_milli=0`. Reject `latest`, `current`, `auto`, other providers, dynamic or invalid fields; changing any pin changes every request/batch identity. The example model identifiers in **unit tests are synthetic fixtures, not live catalog IDs**.
- `sre-opus-request-pack/v1` seals every input packet's `packet_sha256`, exact fixed `SYSTEM_INSTRUCTION`, canonical JSON, `user_payload_sha256`, C9 leaf UUID/context/SHA and matching M19 publication/video SHA. Missing, duplicated or reordered leaf keys fail closed, independent of the count of request chunks; no `top_k`, threshold or low-similarity drop.
- Every request repeats the **full unmodified approved C9** direction/branch/hypothesis 11-field ancestry, sibling definitions, full approved facts and references, frozen system instruction and source pins; **only D1 observations are partitioned**. The user payload contains the observed source field chunks, original whole D1 SHA and descriptive header/missing media state. A slice contains exact `kind`, index, Unicode codepoint `start_char`/`end_char`/`full_char_count`, verbatim text and unchanged transcript/scene `start_ms/end_ms` provenance. Never infer missing media or fabricate quotes.
- Splitting uses UTF-8 JSON-size binary search at Unicode codepoint boundaries, and `_check_lossless` verifies every observed source field and every offset exactly once, with no gaps, overlap, duplicates or changed text. A part that cannot fit (even a single character after full C9 context) yields `token_budget_exceeded`, **never silent C9 truncation**. Per-hypothesis chunk count and original packet SHA are pinned to the manifest; later evidence handling must aggregate and independently check *all* slices, not treat one partial answer as final classification.
- Per-request source M19 context may exceed the old 128KiB limit: M19 now exposes a default-OFF `split_ready=True` mode (2MiB maximum raw packet envelope) for M20 only. Default standalone M19 behavior stays strict at 128KiB and remains read-only and zero-cost. The special mode **does not truncate source data or grant LLM dispatch**. Actual S1 D1 input validation still caps total raw data and media origin.
- Local batching is deterministic: at most 16 requests and 250,000 input proxy units per batch, with ordered request ordinals, original request SHA list, leaf keys, count and `batch_sha256`. Full flattening must recover exactly all request ordinals and SHAs, before aggregate `batch_manifest_sha256` is accepted. **These batches are local workgroups, not OpenRouter remote batch API calls.**

## Token limits: deliberate honesty boundary

The target Opus model's **exact tokenizer is not available** as an independently verified local source in this environment. This stage therefore does **not** invent exact token counts. Instead:
- `input_token_proxy_units = UTF8_BYTES(canonical request user JSON) + UTF8_BYTES(fixed system instruction) + 512 framing reserve`.
- Enforce operator-pinned `max_input_token_proxy` (4,096–250,000), with `context_window_tokens - max_output_tokens - 1024` minimum headroom; a per-call override may **only decrease** the trusted bound. Every request is individually checked.
- Set `token_count_exact=false` and `requires_provider_preflight=true` in every request and batch manifest, always. The UTF-8 proxy is **not a vendor-guaranteed upper bound on Opus tokenization**, not a billable token estimate and not permission to spend. An authorized future provider-specific preflight must enforce the actual model context and official token limits, even on repeated packets.
- The original source/C9 and pinned catalog SHA enter `request_sha256`, `batch_sha256` and `batch_manifest_sha256` through canonical JSON; stable replays are byte-for-byte reproducible. No randomness, timestamps, candidate filtering, money or provider secrets.

Every request and parent manifest contains `dispatch_allowed=false` and `paid_calls=0`. The model's requested `temperature=0` is a deterministic *parameter*, not a guarantee that future LLM responses will be deterministic.

## QA contracts

- `tests/test_sre_opus_request_packer.py`: actual approved C9 fixture with complete 3-leaf and H1/H2 contexts; exact unchanged full ancestry/quote provenance in each chunk; stable request hashes, batch IDs and exact sorted keys; long Unicode/role-injected D1 text with offset reconstruction; `split_ready` source over 128KiB containing 130 real-structure transcript segments and preserved timestamps; missing/duplicate/reordered C9 keys; fake system role or tampered SHA; catalog/model/revision changes yield distinct hashes; dynamic model/temperature/limits rejected; overflow never silently truncates; no external/provider imports.
- `tests/test_sre_offline_pg.py::test_m20_pg_pinned_opus_packing_no_provider_send_or_lost_leaf`: **real disposable PostgreSQL17** approved dual-human C9 publication, D1 content version, correct project/source, all category keys and SHA; M12 planned while required external grant active and M20 local-only work after grant revoked; cross-project denied, source fully revoked -> rights denied, and no classification or paid attempt rows.
- `.github/workflows/sre-contracts.yml`: Linux and Windows pure tests, existing PG17 and full CI jobs; no real model or price provider required to create a transport plan.
- No SQL migration required, no modification of old migrations, no main/peer branch changes.

Commands:
```bash
python -m unittest discover -s tests -p test_sre_opus_request_packer.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES_17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

**QA reality:** repository cannot be cloned locally from this container (DNS for `github.com` unavailable); source and tests are committed through GitHub connector, but no locally observed test `PASS` has been claimed. GitHub Actions exact final SHA must report `conclusion=success` before marking QA complete. Earlier M16–M18 real ONNX inference remains independently BLOCKED.

**Next M21:** strict output/evidence verifier and chunk aggregation, performed only upon next user request. M20 cannot accept a model answer as valid evidence or permit charging.
