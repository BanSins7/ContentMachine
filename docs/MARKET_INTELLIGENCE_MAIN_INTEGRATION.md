# Market Intelligence: integrated into main

## Scope

Source branch: `feature/rtb-market-intelligence-20261030` at
`e2b93ccc04cf950fe4b9445314e373890460badd`.

This integration imports the full dedicated Market Intelligence source
tree (including isolated A/B/C implementations), React + TypeScript
Carousel Studio, SQL migrations, scripts, Market tests and API surfaces.
It **does not** git-merge the feature branch over current main: the newer
React workspace, SRE, QDP, Search Planner and other existing main
services remain authoritative.

### UI

- Main app sidebar has one navigation item only: `Анализ каруселей`.
- The item opens the native, separately bundled `/carousel-studio/`.
- The existing `Инструменты` menu has moved to the header. Pipeline,
  Projects, Settings, Downloaders, SRE, QDP and Test Lab remain accessible.
- General Market Intelligence dashboard is available from the header
  menu and `/market-intelligence.html`.

### Backend

- `/api/studio/*` → `carousel_studio_http.py`.
- `/api/projects/{project_id}/market-intelligence/*` →
  `intelligence/market_intelligence/http_api.py`.
- `/carousel-studio/` and the path-validated
  `/carousel-studio/assets/{hashed-file}` serve native Vite assets.
- General Market Intelligence static dashboard routes are in the
  existing explicit server allowlist.
- Market profile/media/batch/Bright Data workers are started alongside
  existing Discovery and QDP workers, and interrupted studio runs
  are recovered on startup. Existing checkpoint logic forbids
  automatic duplicate paid triggers.
- Market `config.setting` keys are accepted from existing
  `.env`/environment. This file is never committed with secrets.

### Local setup

Node.js 22+, Python and PostgreSQL 17 where required.
Run:

```powershell
cd C:\ContentMachine
git checkout main
git pull --ff-only origin main
python -m pip install -r intelligence/requirements.txt
python scripts\ensure_frontend.py
python app.py
```

The existing `START_CONTENTMACHINE.bat` performs optional Python
dependency installation and builds both frontend apps.
The user-saved Rus-GPT/Bright Data keys stay in
`marketing-qa/carousel-studio/studio-credentials.dpapi` under Windows
CurrentUser DPAPI; they are shared by the main Settings interface and
Carousel Studio. Do not manually paste them into GitHub.

### Validation and limits

- React and Carousel Studio separate TypeScript builds and Chromium
  offline navigation smoke.
- Market unit tests incl. original profile, video, carousel, A/B/C,
  Bright Data snapshot cases and metadata preservation tests.
- Market schema migrations on disposable PostgreSQL 17.
- Actual Python HTTP handler routing smoke with localhost Host/CSRF
  and static asset path checks.
- No paid provider request or real retained-media end-to-end is claimed
  from offline CI. Absent public metrics remain null, not inferred.
- Market A/B/C is scoped to the Market module, not a replacement for
  the independent Group A/B/C and UAE/PIE upstream modules.
