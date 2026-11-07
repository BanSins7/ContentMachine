#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pip install -r worker/requirements.txt
python3 scripts/ensure_frontend.py
python3 -u app.py
