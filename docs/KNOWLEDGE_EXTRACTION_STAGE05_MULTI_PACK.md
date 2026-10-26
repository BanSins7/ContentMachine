# Content Machine B — Stage 05: Multi-Pack & Budget Planning

Status: **implementation committed only when all CI steps pass**.
Stages01–04 A→B frozen source provenance, read-only intake, immutable B1
KnowledgeInputBundle, and exact single EvidencePack remain unchanged.

## Scope and exact input

The planner receives one current, human-approved A Stage11 Context through
the immutable B1 bundle. It invokes existing Stage03/04 verification of
the original profile/source versions, byte hashes, Evidence origin and original
document/web processing outputs **inside the same PostgreSQL transaction**.
No new downloads, re-parsing, ASR, OCR, video model calls or MiMo calls.

Stage04 is authoritative for material that fits *one* pack; Stage05 returns
`use_full_pack` (and stores **nothing**) in that case. Stage05 is only a
lossless deterministic way to produce **2–256 packs** for oversized eligible
technical text, never a summarizer or semantic selector.

## Source semantics and substring provenance

Every original B1 technical fragment is read from immutable source text and
reverified by exact UTF-8 SHA-256. Normal-sized fragments are kept intact.
A fragment that itself exceeds a model pack can be split only into exact
Unicode-codepoint ranges: `[char_start,char_end)` relative to that source
fragment. The original `fragment_id`, original `text_sha256`, original
unmodified `locator`, pinned source/version/output, source role, subject ID
and trust label remain attached. Each chunk gets a deterministic `chunk_id`
and SHA of the *exact chunk bytes*; the parent SHA does not change.

The pure planner `multi_pack.plan_multi_pack` proves: for **every**
B1 fragment its spans start at zero, are nonempty, nonoverlapping,
contiguous and finish at exactly its original character count. Checks
also detect duplicate chunk IDs, missing fragments, interchanged source
versions, source roles or locators, mismatched lines, UTF-8 bytes and
invalid pack order. There is no source-text truncation, no duplicate
character and no false `ready` state.

Each pack contains strict UTF-8 JSONL, a pinned model-independent header and
untrusted text spans as JSON **data** (not privileged instructions).
Video Fusion references and text from video/ASR/OCR/Vision **never enter** the
pack; they stay in the B1 bundle for B9→C only. Web content retains
`model_reported_unverified`; it is not independently retrieved HTTP body.

## Model window and cost limits

Target provider model: `xiaomi/mimo-v2.6-flash`, looked up in the preexisting
OpenRouter verified catalog `tb_model_snapshots`, `tb_catalog_refreshes`,
`tb_catalog_refresh_models`, with no network call during planning.
Context length, output ceiling, immutable price tariff and catalog hash are
pinned. Missing, unsupported, stale or conditional pricing is fail-closed.

Input config is exactly Stage04's four reserved-token values plus:

- `max_packs`: integer 2–256; hard safety bound, **not** a request to
  silently omit remaining input.
- `budget_cap_usd`: positive decimal USD up to 10000; indicative ceiling,
  **not** spending consent or a charge/authorization.

Per-part conservative capacity follows Stage04:
`context_length - (max_output + instruction_reserve + schema_reserve +
safety_margin + 2048)`, with source JSONL capacity conservatively bounded by
`(remaining - 2048) / 2` UTF-8 bytes. All JSON wrappers, chunk identities
and locators count in the actual serialization size, not just raw text length.
A single character plus its JSON metadata failing this bound is a hard
`multi_pack_character_exceeds_window` error; there is never a zero-length
chunk.

Cost estimate reuses the existing price normalizer
`taxonomy_builder.cost_estimator.rates`. **OpenRouter prices are USD per
token**, not per million. For each pack:

- Estimated upper-bound input tokens are based on UTF-8 bytes × 2,
  request-wrapper overhead and instruction/schema/safety reserves.
- Maximum planned output charge includes both output and published
  internal reasoning rates when present.
- Indicative reservation adds a 20% buffer.
- `estimated_usd` uses 50% max output; `reserved_usd` uses max output
  plus buffer and upward rounding to 8 decimal places.

The immutable plan records pricing_sha256, per-pack USD estimates and total
USD totals. If `reserved_usd > budget_cap_usd`, result is
`over_budget`; **no pack/plan is persisted**, no paid request is made.
If total bytes, spans or number of packs exceeds the bounded limits, the
entire plan is rejected, never published as partially complete.

These are conservative **estimates only**. A final B3 request contains
real messages, JSON response schema, provider overhead and potential model
tokenizer differences. Stage06/07 MUST independently re-check **actual**
serialized request, current tariff, sufficient reserved budget, and explicit
cost consent **before** any paid dispatch. Stage05 cannot deduct any money.

## State, SQL and API

**Migration 044** is strictly additive and immutable:
`knowledge_multi_pack_plans`, `knowledge_multi_pack_parts` and
`knowledge_multi_pack_spans`. All tables use project-scoped composite
foreign keys; spans must reference exactly one B1 fragment in the same
project/bundle, parts exactly one published parent plan.
All three tables reject UPDATE/DELETE using the A immutable SQL function.
Plans pin the model snapshot/price SHA, config SHA and full plan hash.
No raw source text or raw JSONL bytes are persisted, only manifests and
original immutable A/B1 locators.

Python entry point:
`intelligence.knowledge_extraction.multi_pack_repository.MultiPackRepository`

- `prepare(project_id,bundle_id,config,allow_partial=False)`:
  synchronous repeatable-read PostgreSQL transaction; locks the current
  project row, validates A/B1, reads verified model catalog, resolves every
  original text, determines full/multi/blocked/budget-exceeded status, and
  publishes **only** complete, within-cap, multi-part metadata plans.
  Identical project/bundle/model/config has one immutable ID; differing
  exact tariff/config generates a new plan; hash mismatch blocks instead
  of overwriting historical data. Partial A-approved omissions require
  `allow_partial=True` (hashed into config).
- `materialize(project_id,plan_id)`: strictly read-only, verifies historical
  original A Context/metadata, B1 source fragments, SQL plan/part/span rows,
  immutable model snapshot/tariff, re-plans deterministically and verifies
  every original reconstructed JSONL byte. Historical versions survive
  profile/source updates; new plans on stale Context are forbidden.

Explicit status codes: `fits_multi`, `use_full_pack`, `blocked`,
`over_budget`; unrecoverable over-cap/integrity errors fail closed with
stable machine-readable `IntakeError.code`.

## Limits and non-claims

- No full-provider-tokenizer guarantee, cost guarantee, runtime B3 executor,
  or spending authorization.
- Semantic completeness, translation quality and objective source truth
  are not proven by byte/character coverage; previous A extractor limits
  remain.
- Large input above MAX_PARTS=256, MAX_SPANS=100000 or total wire bytes
  64 million is blocked explicitly. It is not sampled.
- Video byte integrity remains A-side; no video reprocessing in B.
- No new rights-processing checks were added, consistent with existing
  Content Machine A owner's instructions; technical access/provenance
  validation remains.

## QA and acceptance

```bash
python -m unittest discover -s tests -p 'test_ke_stage05_*.py' -v
python -m unittest discover -s tests -v
python -m scripts.psi_release_gate --strict-ci
```

New unit tests cover exact reconstruction of unicode fragments spanning
multiple packs, 0 gaps and 0 overlapping spans, idempotent deterministic
hashes, byte corruption, missing/replaced source hashes, role and source pin
integrity, multiple budget/cost permutations, output/price catalog failures,
explicit partial approval, prompt injection as inert JSON data, refusal to
publish single packs and strict video bypass.

New PostgreSQL17 tests cover real B1→Multi-Pack persistence, fragment FKs,
immutable plan/parts/spans, cross-project refusal, replay after revised
profile, no persistence on over-budget or single-pack cases, and
`approved_partial` video-only bypass.

**Next after user authorization:** Stage06 B3 MiMo Fact Extraction
`facts-v3`; Stage07 adds paid-run orchestration and independent spending
consents. Neither should treat a Stage05 quote as provider consent.
