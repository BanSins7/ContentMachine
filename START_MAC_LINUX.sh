#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pip install -r worker/requirements.txt
if ! python3 -c 'import psycopg, PIL, gallery_dl' >/dev/null 2>&1; then
  python3 -m pip install -r intelligence/requirements.txt
fi
python3 scripts/ensure_frontend.py
python3 -u app.py
