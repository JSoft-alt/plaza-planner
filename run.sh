#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/planner-requirements.txt; then
  .venv/bin/python -m pip install -r requirements.txt
  cp requirements.txt .venv/planner-requirements.txt
fi
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "${PORT:-8011}" --workers 1
