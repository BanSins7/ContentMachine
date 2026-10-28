# PIE-MOD-34 — Read-only HTTP API, Pattern Explorer & Studio v1

**Branch:** feature/patterns-insights-full-module. No changes were committed to main, UAE, Group A/B/C, QDP or SRE branches.
**Backend:** intelligence/patterns_insights/explorer.py and intelligence/patterns_insights/http_api.py
**Mount:** app.py read-only GET + POST deny route for /api/projects/:project/pie/...
**Frontend:** site/pie-explorer.html, site/pie-explorer.js, site/pie-explorer.css, linked from site/index.html
**Tests:** tests/test_pie_explorer_http.py (31 authored HTTP/source-rights scenarios) and tests/test_pie_explorer_ui.cjs (Node/VM UI scenarios)
**Data status:** unverified research candidates only. No live session principal, qualified UAE source bridge, independent reviewer/holdout, approved release or paid model call has been connected. Application serves the Studio page, but project data return 503 until verified runtime ports are installed.

## Why the API must be disabled without authenticated ports

The existing ContentMachine app.py is a local-only HTTP server. Its authorized() method checks the localhost Host/Origin, which is a useful same-origin protection but **not** proof of an authenticated human or project membership. The API therefore NEVER accepts principal_id, owner_id, user_id or reviewer_id from HTTP query parameters or client-supplied headers. A trusted host has to provide ExplorerPorts with:

- actor.verified_project_actor(handler, project_id) -> bounded authenticated session principal;
- authorizer.require_current_project_access(project_id, principal_id) -> TRUE for current permission, not cached;
- reader.read_fresh_qualified(project_id, original_manifest_sha256) -> actual fresh typed UAEReadResult;
- a real SourceEligibilityGate for *all* original matched source rows and current row licensing/policy;
- a secret server-side CursorSigner (minimum 32 random bytes);
- a request-scoped PostgreSQL connection factory with migrations 200–207 installed.

install_pie_explorer_runtime(ports) is a trusted startup-only injection seam, **not** an HTTP route or automatic discovery. Its default is None. Without these connected capabilities every relevant GET fails closed (503); a denied member gets 403; malformed requests get 400/404. Absence of a usable row is a safe empty/not_ready response, not permission to fall back to an earlier pattern revision. No source snapshot, LLM, source-policy key, private reviewer identity or row content leaks on access failure.

Every GET resolves the principal again on the server and rechecks current project membership before creating a DB connection. It invokes the existing stage26 query_pattern_registry with current UAE, provenance gate, all matched-row rights, stage27 tombstones, latest immutable stage25 revision selection and project/owner-bound signed cursor. The current page is rechecked on every view and every pagination request. A signed stale cursor cannot be reused for a different owner, project or filter. The service never calls stage29 model dispatch or stage32 edit/review mutation.

## Strict, read-only endpoints

Same-origin GETs (all project IDs 32 lowercase hex):

| Route | Visible response only |
|---|---|
| /api/projects/:project/pie/overview | Current allowed page count, matched pairs, exploratory/rejected/needs_review counts (page only), safe pattern rows |
| /api/projects/:project/pie/patterns | Filtered stage26 latest, redacted immutable Pattern Registry candidate projections |
| /api/projects/:project/pie/candidates | Same safe candidate pattern projection; no raw rows, no source media |
| /api/projects/:project/pie/validations | Descriptive candidate source quality state, no p-value/independent inferential claim |
| /api/projects/:project/pie/results | Exact raw descriptive rational difference + physical unit, bin support, N and uncertainty caveats |
| /api/projects/:project/pie/datasets | Explicit not_ready; raw dataset manifests, IDs, source rows and licensing not accessible through this endpoint |
| /api/projects/:project/pie/insights | Only internal generation SHA/revision and verification reason/state; no Insight text, model response or public approval |
| /api/projects/:project/pie/reviews | Only restricted internal review-event count per current visible generation; no reviewer IDs, rationales or review source text |

Every view uses the same safe source rights gate. The insights/reviews SQL query is limited to series IDs present in the *currently allowed* stage26 page and suppresses older revision artifacts. It accesses stage30 generation, stage31 verification and stage32 review-count metadata without exposing the private draft JSON, freeform text or identity of the expert. No historical/unsupported insight appears as a published finding.

Only these bounded filters are accepted: status, platform, b_feature_name, hypothesis_sha256, min_matched_pairs, min_supported_bins, page_size (max 80), and opaque cursor (<=4096 bytes). No arbitrary SQL, IDs, raw export flags, owner override, sorting by private attributes or unsupported joins. Duplicate or unknown query keys are rejected. Each response has a fixed schema pie-pattern-explorer/v1, exact project, view, page_count, page_scope_only=true, publication_allowed=false, causal_claim=false. Do not assume the current page equals the full project. Page counts are not unique media totals and must not be interpreted as independent effect estimates.

The Stage26 RedactedPattern projection retains series/hypothesis IDs, revision, factor/feature/metric/platform, original discovery matched_pair_count, supported_bin_count, descriptive_direction, exact rational raw mean with original source unit. It adds:
- sample_scope = matched_discovery_only;
- inferential_p_value = null, confidence_interval = null, confidence = not_calibrated;
- independent_holdout_confirmed=false, causal_claim=false, can_publish=false;
- explicit caveats that association is not causation, holdout is unconfirmed, statistical significance is not calibrated and rights are checked on each request;
- suggested preregistered independent test **idea**, not an automated experiment.

No user-generated language model prose becomes a factual summary. The server owns all descriptions and validation labels.

All GET responses use app.py's no-store, nosniff and restrictive CSP headers. POST under PIE routes responds with 405 when the global local mutation guard permits routing (otherwise may be 403); there are no write/review/approve/publication functions exposed by this module. The frontend never issues POST.

## UI / Studio

Open http://127.0.0.1:8765/pie-explorer.html from the local app sidebar. The page is usable as a read-only shell even when the runtime is missing. It accepts an explicit project ID or a verified project link with ?project=<32-hex>, *not* an owner ID. A project ID does not grant data access.

The controls select one of eight views, allowed candidate statuses, platform filter, bounded minimum matched-pair count and a server-signed cursor. Safe returned rows show original sample scope, N matched pairs, supported bins, exact raw rational mean/unit, caveats and version. A compact comparison chart built from accessible native HTML progress elements compares **sample sizes only**, not uplift %, significance, causal effects or confidence; dynamic styles are not injected and the existing strict CSP remains enforced. Insufficient/no-authorized data is a not_ready state; rights errors are shown without raw source IDs. Insights/reviews render only their internal status, not text or reviewer profile.

The UI uses textContent and document.createElement for every dynamic value, never innerHTML/eval or HTML from a model. It invalidates pending fetches when filters change (epoch fencing) and clears previously rendered cards/metrics BEFORE every new request so a revoked source cannot remain displayed while authorization is being refreshed. It rejects whole responses or rows that claim publication_allowed, can_publish, causal_claim or independently confirmed holdout. Page pointers belong to the same current filter/actor; changing filters discards them.

## Auth/security/error code contract

- 200: current actor and source rights verified, safe candidate/empty payload only.
- 400: unsupported filter, duplicate query parameter, malformed/expired scope cursor.
- 403: unverified or revoked authenticated project membership. No existence leak.
- 404: unknown PIE route or invalid project route; no raw records.
- 405: PIE write route not supported; no model, human review, approval or publish.
- 503: ports not connected, qualified UAE/row source unavailable, or independent source checks fail closed.

Do not treat 503 as "there are no patterns". Do not silently substitute mock source rows, show historical last-good insight, or auto-repeat paid inference.

## QA and reproducibility

**31 authored unittest methods** in tests/test_pie_explorer_http.py cover positive 4 matched-pair fixture with exact 5/2 ms descriptive effect, source-filtered pages, server-derived session actor, pre-SQL denial, missing/fake actor and ports, current rights per view/pagination, revoked/empty/unready, malformed filters, forbidden owner overrides, stale/wrong-project generation summary suppression, reviewer privacy, no raw media/row secrets, blocked HTTP mutation, typed fail closed SQL and exact no-release flags. Current source authorization and PostgreSQL rows are MOCKED; a positive synthetic test is NOT a real rights certificate.

tests/test_pie_explorer_ui.cjs runs with Node >=18 using vm and fake DOM/fetch; asserts HTML/server route links, default no-project/no-GET, exact same-origin no-cache request, safe charts and fixed units, pagination, empty/unready, 503 revocation clears prior results, raw HTML injection rendered only as text, and rejection of forged public/causal/holdout claims. Browser end-to-end and actual authenticated sessions are NOT proven by Node VM.

Run on a full source checkout:

    python -m unittest discover -s tests -p 'test_pie_explorer_http.py' -q
    node tests/test_pie_explorer_ui.cjs
    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_explorer_http.py

No new SQL migration is needed for Stage34: it reads the existing immutable stage25–32 ledgers and source-rights reader, and appends no database tables. Apply/test migrations 200–207 only on disposable PostgreSQL17 with real row-policy fixtures. Reconcile the previously repaired SQL203 checksum if any database already applied a differing version. Do not mark full PG17, HTTP/JS, or host capability verification passed without observed runs.

## Integration / next

The following are **not yet integrated or verified**: real authenticated host/project principal, live UAE qualified reader and current row rights, actual source policy/consent, multiworker/PG17 integration, source-event invalidation across HTTP, independent reviewer/holdout calibration and a publication gateway. The Studio is an intentional fail-closed shell by default, not a live pattern service.

Next: **PIE-MOD-35 — real-data E2E, independent scientific gold sets, performance (10k+ posts), disposable PostgreSQL17 fresh/upgrade, Windows/Linux compatibility, live peer handshakes and standalone release readiness**. Stage34 code committed does not imply production quality or a merge into main.
