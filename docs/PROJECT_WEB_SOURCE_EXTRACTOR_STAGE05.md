# PSI Stage 05 — Web Source Extractor (Sonnet 4.6 + OpenRouter Web Fetch)

**Scope:** additive to 030–033; stage 06 video pipeline not started. No rights/copyright checking. The user explicitly changed the design: **backend sends only URL to Sonnet 4.6 through OpenRouter**, web fetch is server-side; no self-implemented page download, HTML parsing, Playwright, crawler, or web search in Content Machine.

## Source → quote → approval → job → worker → immutable result

- Stage 02 must have registered an active `product_website`, `competitor_website`, or `research_link` with `link_kind=website`, `ingestion_mode=web_ai`. `source_id` is project-scoped. Accounts and individual videos cannot use this endpoint.
- User clicks **Анализировать сайт** and obtains a quote with TTL 15 minutes. Quote pins `project_id`, `source_id`, *current source version*, exact canonical URL, model, extraction configuration, estimated USD. Quote creation **does not charge**.
- User explicitly confirms the **$0.15 planning estimate** and starts a paid `source_extraction_jobs` job. Estimate is **not a hard provider billing cap**. A second click with the same quote is idempotent. At most one pending web job per source; database partial unique index defends concurrent requests.
- HTTP enqueues only; paid execution requires `python -m intelligence.project_sources.web_jobs --once` or `--poll 3`. The local Content Machine Settings OpenRouter API key is used server-side; never returned to JS.
- The worker performs one POST to `https://openrouter.ai/api/v1/chat/completions`. `model=anthropic/claude-sonnet-4.6`, `tools=[{type:openrouter:web_fetch, parameters:{engine:openrouter,max_uses:1,max_content_tokens:12000,allowed_domains:[exact host]}}]`, `max_tokens=2600`; fixed response JSON schema. OpenRouter operates the server-side tool. Only the exact site hostname is allowed; model instructed not to browse other pages or treat page text as instructions.
- **No auto-retry** on provider error, model truncation or uncertain paid outcomes. Worker claims `queued` with attempts `0`, marks processing and charge outcome uncertain before calling provider; only one attempt. Crashed workers are not auto-reclaimed; manual operator review must decide if re-run is appropriate and request another explicit quote. No provider API calls are made by tests.
- On reported success the output records page text (bounded), summary, sections, *candidate external claims* and their exact substrings/character offsets in **model-reported page text**. Unmatched quotes are rejected. The pipeline saves `source_processing_outputs` with immutable `project_source_versions`, not approved `project_evidence`. `content_sha256` is explicitly the **SHA-256 of model-reported page_text**, NOT HTTP bytes. `source_locator` includes retrieval and verification state. `source_version_id` in output denotes the new version, input job pins previous version.
- On reported unavailability, no source version is fabricated, source last good version remains available, and the job states `unavailable`.
- Free model-provided `usage.cost` is recorded when supplied; if absent, `charge_state=unknown` instead of zero. Prices may change; the quote is only informational.

## API (local host/Origin header + `X-ContentMachine: 1` for POST enforced by app.Handler)

| Action | Route |
|---|---|
| quote | `POST /api/projects/{pid}/sources/{sid}/web/quote` body `{}` |
| start with consent | `POST .../web/start` body `{quote_id, approve_estimate_usd}` |
| runs | `GET .../web/jobs` |
| full output | `GET .../web/jobs/{job_id}` |

No DB schema or source version overwrites; migrations `034_project_web_source_extractor.sql` is additive.

## Honest source/provenance limits

OpenRouter's Chat Completions API does **not guarantee returning the raw HTTP response, headers, redirect chain, original retrieved byte stream, or authoritative server-tool trace**. Therefore a Sonnet-produced text and quotation **cannot be claimed independently verified** against the real website. The payload marks `trust_level=model_reported_unverified`, `source_content_verified=false`, raw capture `false`; it is **not** an approved product feature or authenticated HTML snapshot. UI shows this explicitly. Content hash is model transcript only, and no silent upgrade to authoritative evidence occurs. Exact HTTP statuses (401/403/404/429) from the *target site* are not guaranteed available to the backend; unavailable/partial reflects model report rather than independently proven HTTP status. This is a deliberate consequence of user's URL→model-only architecture.

Technical safety validation rejects malformed, local/private syntactic URLs, direct IPs, userinfo, unusual ports and reserved suffixes. OpenRouter enforces fetch-domain restriction for `engine=openrouter`; the backend does **not** perform target DNS resolution (or issue direct URL requests), so redirect/IP handling depends on OpenRouter provider. No exhaustive crawling; at most one web fetch tool call per request, no web_search tool. No requests go to websites in CI.

## QA

- Pure mocks verify exact model and `openrouter:web_fetch`, provider URL only, domain allowlist, no-key refusal, provider network failure, malformed/empty JSON, unsupported content, prompt-driven trust separation and quote matching.
- PostgreSQL17 CI integration checks quote/consent/idempotency, project isolation, model response persisted as pinned immutable version, cost marker, no auto recharges/retries, and no live external calls.
- Windows real localhost smoke and the existing full suite must remain green on the final commit before merging.
