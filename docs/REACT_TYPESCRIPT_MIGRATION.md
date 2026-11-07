# Frontend migration: React + TypeScript

## Status (2026-11-07)

Phase 1, compatibility bridge. This change creates an actual React 19/Vite
entrypoint in `frontend/src/main.tsx` and a TypeScript source
for every classic browser module used by the existing main, SRE and Group A
pages. TypeScript emits classic (non-module) JavaScript with identical global
semantics into the Vite production distribution. Original browser source and
HTML remain for rollback and for existing DOM/API contract tests.

**Not yet a full declarative rewrite.** The current React root owns the DOM
island, but its child feature controllers still use `getElementById`,
events and imperative DOM mutation. They have `@ts-nocheck` for this
compatibility phase. It would be incorrect to claim all UI features are now
type-safe or state-driven React components.

## Scope and invariants

- Existing localhost Python API, CSRF header, project data and DB are unchanged.
- Existing routes and deep links work through the DOM compatibility layer.
- The same CSS/HTML is retained to avoid layout and selector regressions.
- Modules load in exactly the order declared by their original HTML.
- Main, SRE and Group A surfaces are selected by the current URL.
- The new frontend does not authorize models, paid work, workers or downloads.
- No external CDN scripts or runtime code generation; CSP remains self-only.
- A missing/broken frontend build must not prevent the backend from starting.

## Development

Use Node.js 22+.

```bash
python -u app.py               # backend, terminal 1
cd frontend
npm install --no-audit --no-fund
npm run dev                     # Vite, terminal 2 on 127.0.0.1:5173
npm run typecheck
npm run build
```

The production app is opened at `http://127.0.0.1:8765/`.
Built code is placed in `site/react-dist/`; never commit that
directory or `node_modules/`. A rebuild is attempted by
`scripts/ensure_frontend.py` at server startup if npm is
available. The frontend is forced to the classic UI using
`CM_FRONTEND_MODE=legacy` for emergency rollback.

## Remaining work for complete native React migration

1. Replace raw HTML island with typed feature components, keeping stable
   layout/ARIA/ID contracts until individual modules are removed.
2. Migrate module state from shared global objects and DOM mutations into
   React hooks, context and typed stores.
3. Extract typed HTTP contracts and API adapters for every feature, not just
   the central request helper. Ensure cancellation, safe errors and polling.
4. Port all legacy DOM tests to component, browser and API integration tests,
   including workflow handoffs and model spending boundaries.
5. Remove the classic script loader, copied legacy TypeScript sources and
   old HTML/CSS only after full parity and Windows E2E validation.

The compatibility bridge is deliberately reversible. Never remove the
legacy fallback until every feature has a validated React replacement.
