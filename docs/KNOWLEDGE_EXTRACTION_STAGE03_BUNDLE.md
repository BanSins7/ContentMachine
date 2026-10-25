# Content Machine B — Stage 03: Immutable KnowledgeInputBundle

**Status:** implementation + offline PostgreSQL/CI release review. Stage03 completes
the B1 inventory/persistence layer; it does **not** perform packing, MiMo,
paid execution, normalization, review or KnowledgeSnapshot publication.

## Immutable inputs and source of truth

All new B1 input bundles are derived from an **approved**, current A Stage11
`project-context/v1` envelope, using existing Stage02 `PostgresAResolver`
validation. No changes to A migrations 001–041, legacy RTB, or its review states.

`KnowledgeInputRepository.create(project_id, context_id, source_routes=None)`
performs an explicit PostgreSQL `REPEATABLE READ` transaction:
lock project registry row, verify the A Context, SHA, output/Evidence/source pins,
original registered document bytes, and compare with current A _assemble.
It then inventories every supported **pinned output** and profile field,
calculates canonical checksums, and saves immutable SQL rows. No website
fetch, provider call, file parsing or other new media operation occurs.

SQL migration **042_ke_input_bundles.sql** creates append-only
`knowledge_input_bundles`, `knowledge_input_fragments`,
`knowledge_input_video_refs` with immutable triggers, project+context
foreign keys, pinned source/version and original output foreign keys, and
`project_id + context_id + config_sha256` idempotency.

Schema: `intelligence/knowledge_extraction/schemas/knowledge_input_bundle_v1.json`;
canonical version `knowledge-input-bundle/v1`; engine
`knowledge-inventory/1.0.0`. Bundle SHA includes exact pinned A Context SHA,
explicit role configuration, ordered fragment identity/metadata, excluded
sources, uncovered reasons and versioned video references. It deliberately
does not contain source text or copy raw PDF/MP4 bytes.

## Inventory source and exclusion policy

1. **PDF/DOCX/document**: iterate ALL segments in every pinned
   `document-segments/v1` output (not just up to A Evidence normalizer
   limit of 450). Preserve original segment index, locator and exact text hash.
   Invalid text, broken offsets, incomplete parser output or oversized segment
   produces explicit `uncovered_required`, never silent clipping.
2. **Web**: preserve full captured `page_text` as one technical fragment
   (B2 will split if too large), verify `page_text_sha256` and
   `trust_level=model_reported_unverified`. Incomplete `fetch_status`
   or absent/invalid page text is uncovered. A's Sonnet page_text limit
   means no claim of independently verified full website bytes.
3. **Questionnaire**: if A Stage10 imported a `questionnaire_field`
   source, verify source_locator's pinned revision/hash/field path and
   original profile value SHA, then index that exact source-version.
   Otherwise index the **Stage11-approved sanitized Context profile fields**
   as `context_profile` fragments pinned by profile revision/hash and
   A Context SHA; DO NOT invent source ID or source_version ID for virtual
   profile fields. Do not double-count imported paths.
   Missing structured values and A-bound oversized profile strings/lists
   become uncovered. Secret keys and paths not accepted by A's sanitization
   do not become additional B inputs.
4. **Video**: `video-intelligence/v1` **output ID/version/SHA refs only**.
   Video, transcript, ASR/OCR observations and metadata aren't text
   fragments; video links/handles in questionnaire (`video_references`,
   `promoter_accounts`, `competitor_socials`) aren't passed to B3.
   Preserve pinned fusion references separately even when A notes that a
   video lacks grounded Evidence; retain the explicit A exclusion.
5. **Unknown/unavailable**: missing required document/website output,
   incomplete source processing, unverifiable segment or truncated profile
   yields `readiness=blocked` and an exact code. A-approved explicit
   omissions are preserved in `excluded_sources` and use
   `approved_partial` readiness, not a counterfeit `ready`.
   A no-text video-only bundle has zero MiMo text fragments.

When a reviewed A Evidence quote is exactly equal to a complete pinned
fragment, `fragment.evidence_ids` records all such A Evidence IDs. Partial
quotes (including most web excerpts) are not guessed or linked by
substring-only matches; Stage09 Grounding resolves them independently.

The Stage03 bundle stores **technical fragments**, which are **not**
`project_evidence.evidence_id` or approved B Facts. B5 must later
verify a model's quoted span against the original pinned fragment, and
B8 must separately approve knowledge facts. Count of fragments does not
establish semantic completeness.

## Source roles / subject scopes

`source_routing.py`: `own_product`, `audience_research`,
`competitor`, `creative_reference`, `other`.

Known own-product and competitor websites default conservatively;
unclassified research documents, research links, unknown source types and
unassigned questionnaire paths default `other`. Approved profile product
fields route to `own_product`; explicit audience-problem fields route to
`audience_research`. Distinct competitor sources get distinct stable
`subject_id=source_id` unless the owner explicitly confirms an identity
mapping. `source_routes` requires exact source UUID, role, subject_id and
`confirmed_by=local_owner`. AI suggestions never set or promote source roles.

## Reproducible output and historic replay

- `create(...)`: returns immutable bundle ID/hash, readiness and coverage.
  Repeated identical project/context/config returns the same ID and verifies
  persisted membership rows; different confirmed routing creates a new bundle.
- `get(project_id,bundle_id)`: read-only, verifies canonical bundle SHA,
  SQL fragment/video membership and all historic original A pins without
  rebinding to newer versions.
- `resolve_fragment(project_id,bundle_id,fragment_id)`: server resolves
  pinned profile value, original document segment, or model-reported web
  page, rechecking original output SHA, locator and exact fragment text SHA.
  Historical snapshots remain readable when newer source/profile versions
  are created. No arbitrary URL, local path or model-provided source pointer.

**Important limitations:** Stage03 does not hash MP4 media bytes (pinned
Video Intelligence JSON hashes and original A SQL provenance are verified).
A's pre-existing profile sanitization intentionally excludes some fields;
they are not implicitly authorized for B extraction. Web text is model
reported, not independently downloaded/verified. Source revocation policy
conflict is deferred and no new copyright ownership gate is invented.

## Tests and release gate

```bash
python -m unittest discover -s tests -p 'test_ke_stage03_*.py' -v
python -m unittest discover -s tests -v
python -m scripts.psi_release_gate --strict-ci
```

QA covers >450 document segments from one A Evidence,
web trust and integrity, broken offsets, partial outputs,
unknown/confirmed source ownership, untrusted unknown source IDs,
profile/video-link exclusion, video-only refs, real PostgreSQL persisted
checksums/idempotency, original fragment replay, foreign projects,
FK/immutable triggers, profile change/history, and A legacy regression.

Next Stage04 builds bounded **EvidencePack** from exactly these fragments;
this stage cannot start MiMo or spend money.
