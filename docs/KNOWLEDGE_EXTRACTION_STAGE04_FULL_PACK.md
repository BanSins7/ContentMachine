# Content Machine B — Stage 04: Deterministic Full Context EvidencePack

Status: **implemented; technical CI/PG verification required**. No paid calls, no
OpenRouter chat requests, no B3 MiMo extraction and no B9 knowledge publication.

## Source boundary

B1 `knowledge-input-bundle/v1` created in Stage03 is the only input.
The packer reads exact retained text with the **same** server-verified
A project/context/output/profile provenance as Stage02–03. Each
`fragment_id` maps to one complete text span; Stage04 never splits,
shortens, paraphrases or selectively skips an included fragment.

The full pack is an ordered UTF-8 **JSONL** document with an immutable
header and one JSON object per B1 fragment. One entry contains the exact
source text, server-owned source/version/output, original locator, exact
text SHA, subject scope and role, and optional *exact-match* A Evidence IDs.
Model instructions, JSON-schema, prices and future outputs are **not** embedded
in these fragments. Untrusted document text remains data, never a privileged
system or developer message.

All B1 video analysis refs, transcript/ASR/OCR/visual descriptions and
social-video handles stay outside the Full Pack and MiMo. The Decision
records only the **count** of excluded pinned video refs, not their content.

## Model window / safety

Model `xiaomi/mimo-v2.6-flash` comes from the **already-observed, current
and verified OpenRouter catalog** (`taxonomy_builder.catalog.latest_verified`).
The catalog snapshot ID, SHA and context length are pinned. No catalog
refresh or provider network call occurs as a side effect of packing.

Caller supplies a strict `config`:
- `max_output_tokens` — nonzero reserved output for future B3 extraction
- `instruction_reserve_tokens` — B3 system/developer instructions
- `schema_reserve_tokens` — structured response schema
- `safety_margin_tokens` — additional headroom

Provider request messages are NOT yet available in Stage04. The same
conservative estimator used by the existing RTB provider is applied:
`2 × UTF8_wire_bytes + 2048` for full JSONL input plus explicit reserves.
This is a **planning estimate**, not a certified token count, not a price
quote, and not a guaranteed billable ceiling. Stage06/07 MUST recompute
against the **actual final serialized messages and response schema**,
recheck the current provider snapshot and use an independently authorized
cost quote/consent. A Full Pack is not permission to spend.

If even the raw character lower bound exceeds the available window, return
`requires_multi_pack` **without loading the full payload**; Stage05
handles splitting. If the header and complete JSONL entries exceed the
window, return `requires_multi_pack` and discard partial assembly.
No truncated output is ever stored or labeled `fits`. An unknown/unverified
model window fails closed.

## Decisions

- `fits`: exactly one pack, complete B1 fragment coverage, exact bytes,
  persisted immutable metadata; **tokenizer_check=pending_exact_provider_preflight**.
- `requires_multi_pack`: too large for one conservative context window;
  no pack row, no paid call. Stage05 builds N packs.
- `blocked`: B1 uncovered mandatory source, missing text, model output
  limit, or `approved_partial` without explicit `allow_partial=True`.
- `candidate`: internal pure metadata probe only, never persisted/reported
  by `FullContextPackRepository.prepare`.

Source-excluded partial A approvals are preserved; an explicit
`allow_partial=True` is included in the immutable config SHA. Even
`approved_partial` only means the user acknowledged *which original
sources were excluded*; it does not approve B3 facts.

## Database and API

**Migration 043** adds `knowledge_full_context_packs` with composite
project+bundle FK, pinned model snapshot FK, unique
(project,bundle,model_snapshot,config SHA), immutable trigger and
byte-level manifest/payload digests. Historical A migrations 001–042
remain unchanged.

Python: `intelligence.knowledge_extraction.full_pack_repository.FullContextPackRepository`.

`prepare(project_id,bundle_id,config,allow_partial=False)`
- Project scoped, `REPEATABLE READ`, project row lock.
- Verifies current A Context and Stage03 persisted membership and SHA.
- Uses the current verified model catalog without outgoing requests.
- Calculates decision; only potentially-fitting inputs load raw text,
  validate all `fragment_id` text SHA and locators, rebuild B1 inventory
  against the pinned records, and create complete JSONL.
- When `fits`, saves metadata-only pack and returns ID and hash.
  Idempotently returns same ID for identical canonical config/snapshot.

`materialize(project_id,pack_id)`
- Historical read only (no current-profile requirement).
- Re-verifies original immutable A Context, B1 Bundle, every fragment,
  original output/profile field, pinned catalog snapshot, exact JSONL SHA,
  byte offsets, text SHA, role and original source link.
- Returns the original byte-exact JSONL pack for the **future** B3
  orchestrator, with no sending or charging.

## Limitations

A's parsed document output can be incomplete before reaching B1, and
Sonnet webpage text is `model_reported_unverified`. This layer cannot
make them independently complete or objectively true. Stage04's
single-pack guarantee applies only to the complete set of *eligible
pinned B1 technical fragments*. Stage05 is required for larger payloads,
and Stage06/07 must perform exact provider preflight and cost consent.
No new copyright/rights enforcement was introduced.

## Regression tests

```bash
python -m unittest discover -s tests -p 'test_ke_stage04_*.py' -v
python -m unittest discover -s tests -v
python -m scripts.psi_release_gate --strict-ci
```

Test exact deterministic Cyrillic/JSON/meta serialization, UTF-8 offsets
and SHA, 501-fragment oversized input, large framing overhead, foreign
project and poisoned IDs, altered text, model/catalog validation,
explicit partial authorization, video bypass, PostgreSQL immutable
snapshots and historical replay after profile changes.

**Next stage, if requested:** Stage05 Multi-Pack + budget planning with
complete fragment-by-fragment coverage and explicit per-pack provenance.
