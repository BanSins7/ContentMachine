# Bright Data / Rus-GPT in main · 2026-11-07

Source branch: `feature/rtb-market-intelligence-20261030`.

The branch contains React Carousel Studio (`carousel-studio/`) plus provider-backed
carousel downloads and analysis (`intelligence/market_intelligence/carousel_*`).
It can collect TikTok Photo Mode slides via gallery-dl, read Bright Data public
metrics and analyze each full carousel with OpenRouter Gemini 3.8 Flash or
Rus-GPT Claude Haiku 5.5. A local SQLite store supports account-level cohorts,
per-run usage, deduplication and 1–2 / 3–5 / 6–8 / 9+ slide groupings.

**Scope in main:** This change integrates the provider settings only.
The whole Studio background processing pipeline is NOT merged into main.
Stored keys do not initiate any paid calls, scrape, download or analysis.

## Local credentials

New main Settings has separate OpenRouter, Rus-GPT, Bright Data secret inputs,
the preferred provider selector and an explicit Bright Data permission control.
`GET /api/provider-settings` returns status booleans without token values.
`POST /api/provider-settings` is localhost-only, checks the existing
`X-ContentMachine: 1` mutation header, and stores locally.
On Windows, storage is DPAPI CurrentUser at
`marketing-qa/carousel-studio/studio-credentials.dpapi`, the same location
and format as the isolated Carousel Studio branch. Blank secret values
preserve saved values. Existing OpenRouter credentials still work.

User-provided default tokens are not committed to public GitHub.
Instead, a conversation-only `configure-contentmachine-providers.ps1`
installs the supplied tokens into this local DPAPI store via localhost API.
After running that script once on the user's Windows PC, the status
will show both credentials configured; no repeated entry is required.

Bright Data collection authorization remains OFF until explicitly enabled.
No live provider authorization or paid workflow is claimed in this change.

## Test

Run `python -m unittest discover -s tests -p test_provider_settings.py -v`
and `cd frontend && npm run build`.