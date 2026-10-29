# SRE M19 — Complete C9 Hypothesis Context / Prompt-Injection Isolation

**Date:** 2026-10-27 · **Branch:** `feature/sre-production-v1-20261023` · **Draft PR:** [#22](https://github.com/BanSins7/ContentMachine/pull/22)

**Status:** implementation and negative tests committed. Exact GitHub Actions PASS not verified; local repository clone blocked by GitHub DNS. This stage **does not send to Opus**, does not consume tokens, create provider attempts, authorize external LLM access, reserve money or interpret provider output.

## Requirements and authority

Canonical M19: *complete C9 classifier context and prompt-injection isolation*, fixing FIX:05,11; QA:041–046,051–060. Each actual RTB C9 leaf must reach later provider stages with **all 11 RTB node fields**, every ancestor, full sibling hypotheses, approved facts and their exact source quote/field/origin, plus actually observed D1 content. No summary or partial truncation, synthetic transcript, query-to-label shortcut or inclusion threshold. One packet per approved C9 leaf, including M17's low-ranked ones.

- **`intelligence/relevance/live_classifier_context.py`**: `assemble_classifier_packets(c9, content)` is a pure transport projection, **NOT a publication or project authorization**. It checks the full self-consistent C9 bundle from M06, then copies the exact node fields `id`, `parent_id`, `kind`, `relation`, `source_fact_ids`, `name`, `rationale`, `definition`, `include`, `exclude`, `examples` for direction, optional branch and hypothesis. Sibling context includes all C9-reviewed `id/name/definition/include/exclude`. Every approved fact copies `id/kind/text/source_quote/source_field/origin` exactly, including explicit `null` for absent upstream source quotes. It NEVER manufactures a quote. Independent C9 validation assures source identifiers, H1/H2, parent links, context SHA and all UUID5 leaf mappings.
- **`build_live_classifier_context(conn, project_id, run_id, item_id)`**: actual trusted production read boundary. Delegates to M17 `_authorize_live_rank_inputs` to validate an exact M12 LIVE run and item against genuine project-owned PostgreSQL S2 deep C9 publication (both H1/H2), pinned taxon/context, source-approved D1/S3 content version/SHA, provenance, observation expiry, original source-policy version and currently active permission. Foreign/inactive/unknown/paused/cancelled runs rejected. The external host must authenticate the project owner separately. Output is read-only; no model or network dependency required.
- D1 `metadata_only` carries **only observed title, description and hashtags**. Missing transcript and scenes remain `null` and are listed in `missing_fields`. Partial/full media requires actual reviewed media rights and source-backed media asset; no video frames, quotes, transcripts or scenes are synthesized. Unicode/Multi-language text is preserved via UTF-8 canonical JSON. Query/discovery ids appear only as provenance pins; the classifier's trust policy explicitly denies treating search query as category evidence.
- `sre-live-classifier-context/v1` returns precisely one packet per C9 hypothesis, sorted by canonical leaf UUID. Each holds pinned project/publication/taxonomy/content/source version/SHA, its full RTB C9 context, observed D1 content, role-separated messages, `system_instruction_sha256`, `user_payload_sha256`, `context_sha256`, `packet_sha256`. Aggregate manifest contains all expected keys/count and SHA. Both levels explicitly set `external_dispatch_allowed=false`, `paid_calls=0`. There are no classification decisions or selective `top_k` drops.
- No silent prompt shortening: per-packet UTF-8 cap 128 KiB, aggregate 6 MiB; over-cap raises typed `context_overflow`. Caller must explicitly revise/authorize the upstream content or prompt design; missing data is never invisibly omitted.

## Trust and prompt-injection defenses

Instructions are a fixed, code-authored system string, entirely outside the JSON. All potentially hostile text — **including human-approved taxonomy names, descriptions and facts** — appears exclusively within one serialized `user` JSON value as **data**, with an explicit `trust_boundary` stating that its contents cannot introduce system/developer instructions, override evidence rules or trigger payments. D1 video text containing `</system>`, role delimiter tokens, JSON pretending to be system messages, multilingual imperatives or forged decisions stays as one string in that JSON. Roles are always exactly `system`, `user`.

**Limit:** JSON separation and instruction hierarchy reduce risk; they do **not** mathematically guarantee immunity to future prompt injection. The future Opus execution stage must recheck actual D1 `can_external_llm` at the last send boundary, verify active run/worker/consent and budget before a paid POST, and independently validate all provider labels and *verbatim evidence* against source content. A compliant-looking model reply is not proof of correctness. M19 includes no endpoint that sends these packets.

## QA

- `tests/test_sre_live_classifier_context.py`: actual repository-approved C9 fixture (3 leaves), all 11 fields for every full ancestry node, branch/direct path, exact siblings, fact id/quote/source origin; deterministic multilang UTF-8/hashes, zero candidate drop; D1 metadata-only no invented transcript; payload/role injection tests; malformed/tampered C9 and cross-project denial; no provider or ONNX import; overflow fails closed.
- `tests/test_sre_offline_pg.py::test_m19_live_classifier_context_real_c9_d1_and_injection_rights`: disposable real PostgreSQL17 two-human-approved C9+ D1 content; verifies all leaf packets even with a malicious video title, exact source facts and no model/outbound attempt, source revocation and cross-project denial.
- `.github/workflows/sre-contracts.yml`: M19 unit suites on Linux and Windows, existing PostgreSQL17 fixture, full migrations/regressions.
- No SQL migration needed for M19; reused read-only pinned M11/M12 tables and C9/D1 authority. Historic migrations unchanged.

Commands:

```sh
python -m unittest discover -s tests -p test_sre_live_classifier_context.py -v
CM_TEST_PG_DSN='<DISPOSABLE_PG17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

**QA reality:** the repository could not be cloned locally because `github.com` did not resolve. The tests have been authored but **no local PASS** is claimed. GitHub Actions results must be checked for the exact final branch SHA. Earlier M16–M18 REAL pretrained ONNX inference verification remains independently **BLOCKED**; M19 transport assembly does not require a real ONNX model and must not falsely claim to lift that separate acceptance blocker.

**Next separate user request:** M20 — provider/model/prompt identity pins and subsequent Opus transport contracts, without paid execution until dedicated ledger/consent stages.
