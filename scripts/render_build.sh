#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "Usage: bash scripts/render_build.sh" >&2
  echo "Do not append pip arguments here; configure only this script as the Render Build Command." >&2
  exit 2
fi

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install Flask-SQLAlchemy Flask-Migrate SQLAlchemy psycopg2-binary "psycopg[binary]"
