# SRE M21 — Strict Opus Output / Source Evidence Verifier

**2026-10-27 · branch:** \`feature/sre-production-v1-20261023\` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Implementation:** committed. **CI acceptance:** PENDING exact GitHub Actions. No model call, network, spending consent, provider receipt or irreversible classification storage. The pure unit and synthetic PG response fixtures **do not** demonstrate a paid provider call or semantic truth.

## Trust boundaries

\`intelligence/relevance/opus_evidence_verifier.py\` adds two functions:
- \`verify_opus_responses(m20_manifest, received)\` is **pure**. It strictly verifies an internally supplied, self-consistent transport manifest and all returned JSON fragments. It **does not** authenticate the approved taxonomy, current D1 permissions, vendor identity, receipts, model signature, or factual semantic correctness.
- \`verify_live_opus_responses(conn, project, run_id, item_id, received, operator_catalog_pin=..., expected_batch_manifest_sha256=...)\` is a **trusted read** port. It first rebuilds the exact M20 plan through actual project-scoped M12 run/item, human-reviewed H1/H2 C9 publication and current D1/S3 source/observation/source-policy access. It compares the operator-pinned model/catalog digest and expected **batch manifest SHA**. Only then does it parse replies. The host must separately authenticate the project owner; \`project_id\` is not a login token. It does not grant \`can_external_llm\`, paid permission, or worker fencing, and assumes a future audited sender authenticates the actual vendor receipt. The M20 plan's \`requires_provider_preflight\` remains true.

## Exact provider-output schema (no extra keys)

Each technical receipt from the future trusted sender has only:

\`\`\`json
{
  "request_sha256": "<SHA256 of exact M20 request>",
  "raw_json": "{\"category_key\":\"<approved C9 UUID>\",\"decision\":\"match\",\"reason\":\"One concise reason.\",\"evidence\":[{\"source\":\"title\",\"index\":0,\"start_char\":0,\"end_char\":6,\"quote\":\"verbatim\",\"start_ms\":null,\"end_ms\":null}]}"
}
\`\`\`

\`raw_json\` is **one whole JSON object, not markdown or an array**. The decoded object must contain exactly:
- \`category_key\` — the canonical UUID of the **single** category pinned by that request. No extra, unknown, other-project, different-leaf or repeated category.
- \`decision\` — exactly one of \`match\`, \`no_match\`, \`uncertain\`. \`not_evaluated\` is a **system-internal** state and NEVER accepted as a provider output. No case aliases, bools, statuses or fallback substitutions.
- \`reason\` — nonblank, <=4096 characters, data-only untrusted text.
- \`evidence\` — 0..16 exact structured source citations per chunk, with **at least one** for \`match\`.

An evidence object has exactly \`source,index,start_char,end_char,quote,start_ms,end_ms\`. The source is one of **title/description/hashtag/transcript/scene**; no C9 description, search query, discovery id or taxonomy-approved fact is proof that a video contains it. \`index\` refers to the actual array element from D1, \`start_char/end_char\` are half-open original Unicode codepoint offsets, and \`quote\` must match an exact substring of a single **offered M20 D1 chunk**, not another chunk and not a paraphrase. No case folding, normalization, text search outside declared span, substitute quote or inferred transcript. For title/description/hashtag, \`start_ms=end_ms=null\`; transcript/scene citations must use **the same measured D1 segment start_ms and end_ms**, not estimated word timings. A generated or altered timestamp is invalid evidence.

Malformed, duplicate JSON keys, nonstandard NaN constants, oversized/truncated text, unexpected keys, bad enum, empty match evidence, duplicate quote, fake source/timestamps or fabricated C9 category result in a **typed technical error** (\`malformed_model_output\`, \`invalid_decision\`, \`invalid_evidence\`, \`unexpected_category\`, etc.). **Never** convert parse/timeout/provider errors into semantic \`no_match\` or silently assign \`uncertain\` while pretending all receipts are present. Errors require M15 fail-stop / future audited retry policies, not blind paid replay.

## Full source/chunk/leaf coverage and aggregation

The verifier checks the M20 parent SHA, individual request SHA, fixed system role, canonical JSON, model/catalog revisions and source pins, local deterministic batch membership and exact ordering of every request ordinal. Every C9 leaf is processed even if it had a low M17 cosine; *all* chunk indices 0..n-1 must exist **once**, and original M20 source parts must be continuous and attributable to D1. Delivery of raw replies can arrive in any order, but the **set** of request SHAs must match exactly. A missing/duplicate/unknown response produces \`incomplete_coverage\` and no category decision.

Only after all source snippets have valid evidence can the per-leaf aggregation return a transport-checked \`match/no_match/uncertain\`:
- Exactly one chunk → its checked enum; \`uncertain\` requires review.
- Multiple chunks all agree → that enum.
- Contradictory / mixed partial chunk decisions → \`uncertain\`, \`requires_human_review=true\`, with the complete list of \`partial_decisions\`.
- Partial or invalid responses → **no aggregate output**, typed technical failure.

Result includes all C9 expected keys, each leaf's exact verified chunk count, checked evidence, pinned model/catalog/content SHA, per-category SHA and aggregate \`verification_sha256\`. It permanently marks \`provider_receipts_authenticated=false\` and \`semantics_independently_confirmed=false\` in this stage: passing a structure/evidence check **does not prove** the provider was called or the model's hypothesis judgment is true. Every output remains \`dispatch_allowed=false\`, \`paid_calls=0\`. No SQL writes, no budget reservation, no attempt-log mutations, no S4 offline score masquerading as LIVE output.

## QA and limitations

- \`tests/test_sre_opus_evidence_verifier.py\`: fast Linux+Windows no-provider tests — genuine approved C9 fixture, complete three-category outputs, missing/duplicate/unknown response SHA, out-of-order delivery, malformed JSON/markdown, duplicate keys/NaN, unexpected fields, unapproved category UUID, \`not_evaluated\`, wrong quotes/index/offset/timestamps, fake system/taxonomy quotes, metadata-only no transcript, full multilingual chunk aggregation and contradictory decisions, source-timed transcript evidence, true model/plan SHA drift and foreign-project preflight.
- \`tests/test_sre_offline_pg.py\` new \`test_m21_pg_strict_opus_evidence_checked_after_current_d1_rights\`: real disposable PostgreSQL17 C9 dual H1/H2 plus D1 policy/version and M12 planner. Its **synthetic** reply JSON is only a verifier fixture; verifies exact original project/C9/source pin, no paid attempt/classification results, plan SHA tamper denial, cross-project denial and D1 policy revocation denial before accepting replies.
- \`.github/workflows/sre-contracts.yml\` updated M02–M21 Linux/Windows unit and PG17 integration.

Local commands (not claimed executed here):

\`\`\`sh
python -m unittest discover -s tests -p test_sre_opus_evidence_verifier.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

**QA status:** historical and new GitHub workflow runs have remained \`queued\` when checked; test source commits are not proof of a passing test. A local checkout of the private GitHub repository was unavailable in this execution container. Keep QA unchecked until exact last SHA shows actual \`success\` on SRE Linux/Windows/PG17 plus related full jobs.

**Previous blocker:** M16–M18 authentic locally pinned pretrained ONNX/semantic cold-hot restart verification remains **BLOCKED**; it is unaffected by M21's pure output verifier.

**Next separate request:** M22 begins the cost, consent and only-Opus LIVE phase; **do not** make a paid call or give work to a provider without a later authorized, fenced, budget-reserved and D1 before-external-send gate.
