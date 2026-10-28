# Group B — GB-MOD-20 B8 Topic Registry & Resolver

**Canonical branch:** `feature/group-b-full-module`. **Implementation:** B8 domain, immutable SQL005 registry and one-shot approved assignment. **QA truth:** synthetic tests and optional PG17 tests committed; not verified as PASS. No paid calls, production writes, main/peer edits or automatic creation of taxonomy terms.

## What B8 measures
B8 classifies the subject **actually observed in a post**, not a search phrase, a research hypothesis from Group C/SRE or a Search Planner topic. Model returns **labels** (CATEGORY > TOPIC > optional SUBTOPIC), never database IDs, taxonomy slugs or foreign keys. Registry resolver maps normalized **exact** existing names/slugs to `content_b_categories`, `content_b_topics` and `content_b_subtopics`. Fuzzy similarity/synonym/embedding matching is intentionally not approved. All new suggestions remain review-only.

## Frozen SQL005 relation
- `content_b_categories(id, slug, name, merged_into_id)`; `content_b_topics(id, category_id, slug, name, merged_into_id)`; `content_b_subtopics(id, topic_id, slug, name, merged_into_id)`; `content_b_topic_assignments(analysis_id, category_id, topic_id, subtopic_id, taxonomy_version, needs_review)`.
- `category_id`, `topic_id` and optional `subtopic_id` are stable existing **positive int** IDs. The exact hierarchy must be valid: topic belongs to that category; subtopic belongs to that topic. The original SQL005 composite FK checks are left unchanged.
- `merged_into_id` may redirect a topic/subtopic to an **approved target within the same parent**; resolver detects cycles, missing targets and cross-parent merges, maps alias to canonical terminal ID and retains `alias_levels` in a provenance proposal. Category merges are **review-only**: moving/relinking children across category parents requires human approval; no automatic reparenting.
- Duplicate normalized names that point to distinct targets are **ambiguous**, not chosen by arbitrary sort order. Missing category/topic/subtopic generates a `needs_review` proposal with explicit reason, **no SQL005 insertion**. Names normalized only by Unicode NFKC, casefold and whitespace; no hallucinated synonyms or semantic merges.
- A project-authorized transaction must read the current registry via `read_topic_registry(conn)` and freeze its SHA. That SHA alone is not proof of freshness, authenticity, authorization or reviewer approval. `persist_b8_topic` holds PostgreSQL SHARE locks on the registry tables until the caller's commit, then rereads the live registry in that saving transaction and rejects any changed digest.
- A mixed carousel may provide its own genuine **video slide** with source-anchored original video timebase, exact slide index and SHA-backed transcript/frame. An image-only carousel cannot invent spoken video content. The source-claim pure suite has a mixed-carousel negative test for slide-index substitution.
- Partial context/source omissions, low confidence, unverified model delivery or missing visual image attachments can never produce a persistable B8 assignment.

## Source-integrity and security
`validate_b8_topic` independently matches current original GA/QDP version/policy/media SHA, original video duration or carousel ordering, ASR/OCR SHA-verified UTF-8 observations, OCR-to-original video frame timing and frame identity, exactly selected/omitted refs and source-derived counters. A cited OCR span must have the original supporting frame in authenticated delivered images. Image bytes are attached only in strict **UNSENT** `prepare_b8_request`; no HTTP/paid request is initiated in B8 code. Provider receipt must independently attest the actual approved request, selected image attachments and result. Source data is untrusted prompt content.

## Files
- `intelligence/group_b/topic_registry.py`: immutable `TopicNode`/`TaxonomySnapshot`, strict builder, read-only SQL005 registry loading, canonical/merge-safe resolver.
- `intelligence/group_b/blocks/b8_topic.py`: source-pinned model response validator; immutable `B8TopicProposal`; `unknown_b8` and UNSENT `prepare_b8_request`.
- `intelligence/group_b/blocks/b8_schema.py`, `intelligence/group_b/prompts/b8_topic_v1.txt`: strict versioned label-only classification interface.
- `intelligence/group_b/blocks/b8_storage.py`: optional **caller-owned transaction**. Requires precreated pending/partial B analysis, project/run/media/proposal/registry SHA pins, independently injected `verify_topic_persistence` (fresh QDP/GA/media/source rights, provider receipt, project auth), canonical re-resolution from current SQL005 registry, one-shot INSERT in `content_b_topic_assignments`, readback and parent `partial`. Never writes categories/topics/subtopics or stamps full B1–B8 completed.
- `tests/test_group_b_b8.py`, `tests/test_group_b_b8_db.py`: synthetic response/host admission; pure and optional disposable PG17 regression against exact ID/alias/parent/duplicates/cycles/unapproved new topic/stale snapshot/one-shot write. Existing schema 005 remains untouched.

## Deployment and trust blockers
The module ships **no** live topic registry moderation/change-approval service, no real signed provider image/LLM receipt or authorization for paid calls. A trusted host must freshly authenticate producer QDP, exact Group A snapshot, current source rights and media bytes and provide a real provider receipt at point of use. Registry approval/rename/merge API and human review process remain stage GB-MOD-23 and system integration; B8 never grants such permission.

## Reproducible QA commands
```bash
python -m unittest discover -s tests -p "test_group_b_b8.py" -v
CM_TEST_PG_DSN="postgresql://.../cm_group_b_test" python -m unittest discover -s tests -p "test_group_b_b8_db.py" -v
```
Ubuntu/Windows plus disposable PostgreSQL17 checks are in `.github/workflows/group-b-module.yml`. **QA status remains NOT RUN/QUEUED** until observed otherwise, and all fake inspectors/receipts in tests must never be used in production.
