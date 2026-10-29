# ContentMachine — hosted Jina Query Expansion A/B experiment

## What is implemented

Experimental, **read-only shadow comparison** on a single immutable completed
Search Planner generation. This is the **Jina research method**, not an RTB
heuristic substituted for an LLM expansion.

1. Read one completed SP4 generation and verify its approved hypothesis contexts.
2. Select the same 5/10/20 query candidates by deterministic fair round-robin
   across hypotheses (same input IDs in both arms).
3. **A / Baseline**: hosted `jina-embeddings-v3` with task `retrieval.query`
   embeds original SP4 query strings. Approved RTB reference text is embedded
   with `retrieval.passage`, as appropriate for asymmetric retrieval.
4. Send the **unmodified original query text** plus unique candidate ID to
   an OpenRouter-hosted LLM. Jina's published generic prompt asks for additional
   search keywords and phrases, targeting **100 / 150 / 250 words** per query.
   Validate a full JSON `[{qid, additional_info}]` mapping. One request only;
   never silently replace the output with RTB vocabulary.
5. **B / Expanded**: concatenate each original query with its real LLM-produced
   `additional_info` and compute fresh embeddings using the SAME hosted
   `jina-embeddings-v3` and `retrieval.query` adapter.
6. Run unchanged Query Validation selection logic (cosine, positive/negative
   RTB references, dedup, MMR) for each arm using the same thresholds. The
   underlying hypothesis reference vectors are identical between arms.
7. Report selection changes, actual A/B similarity scores, complete expansion
   text per query, mean and per-query `1 - cosine(qA, qB)` drift.

**No local embedding model is needed**. This experiment calls
`https://api.jina.ai/v1/embeddings` (Jina v3) and
`https://openrouter.ai/api/v1/chat/completions` (LLM).

**Not a reproduction of identical research *scores***: the Jina study tested
Gemini 2.0 Flash on BEIR documents, and the selected OpenRouter model can
differ depending on current availability. The experiment uses approved RTB
descriptions as proxy reference passages, not a real video corpus.
Accordingly, no retrieval precision, real recall, or video ranking quality
can be inferred from this A/B alone. It **does** execute the paper's actual
LLM text-expansion and query-embedding transformations.

## One-time PowerShell environment (before launching server)

```powershell
$env:CM_SP_OWNER_TOKEN = "<existing-separate-owner-token-at-least-32-chars>"
$env:CM_SP_JINA_AB_ENABLED = "1"
$env:CM_SP_JINA_AB_LLM_MODEL = "google/gemini-2.5-flash"
$env:OPENROUTER_API_KEY = "<openrouter-key>"
$env:JINA_API_KEY = "<jina-key>"
```

These are placeholders, not credentials. The chosen OpenRouter model must be
currently accessible and capable of returning a complete JSON list. Adjust
the model after checking available models; no provider fallback/retry. Running
the main production QV still independently requires its pinned local model —
this experiment neither enables nor changes that production contract.

In the Search Planner UI, after a Generation has reached `completed`:

1. Go to **Query Validation**; fill the **Generation ID** in the Generation
   section and the same policy you would use for normal QV.
2. Open **Jina Query Expansion A/B**.
3. Choose 5/10/20 candidates, choose 100/150/250 expansion words.
4. Explicitly approve **separate external spending** and press the compare
   button.
5. Inspect side-by-side results and export complete JSON.

The HTTP endpoint is owner-token-authenticated local-only
`POST /api/projects/{project_id}/search-planner/qv/ab-jina`, requires
`CM_SP_JINA_AB_ENABLED=1`, an explicit confirmation Boolean and a server-side
single-run semaphore. **It can incur charges** at both providers; there is
no spending quote/ledger for this isolated research endpoint. The user is
explicitly shown this before launching. Calls are bounded (20 candidate
queries, one LLM completion; cached Jina v3 embeddings), and **never retried**.
A timeout can still have a billable outcome. Do not blindly retry.

No PostgreSQL writes, source-provider queries, SP4 re-generation, QV commits,
or Collector submissions are made. Experiments return the entire result JSON
to the local owner browser, where it can be saved.

## Tests

```powershell
python -m unittest discover -s tests -p "test_search_planner_jina_ab.py" -v
python -m unittest discover -s tests -p "test_search_planner_query_validation.py" -v
node --test tests/test_search_planner_ui.mjs
```

Unit tests use injected deterministic vectors/LLM responses ONLY to check
wiring; these are not production substitutes and not evidence of real API
performance. Live provider tests require explicitly provided keys and
authorization.

## Sources

- Jina official article:
  https://jina.ai/news/query-expansion-with-llms-searching-better-by-saying-more/
- Original code:
  https://github.com/jina-ai/llm-query-expansion
- Hosted v3 asymmetric embedding tasks:
  https://jina.ai/news/migration-from-jina-embeddings-v2-to-v3/
