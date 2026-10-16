#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/Fuelledger"

if [[ ! -d "$APP_DIR/.git" ]]; then
    echo "Missing Git repository at $APP_DIR." >&2
    exit 1
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "Missing $APP_DIR/.env; create it from $APP_DIR/.env.example first." >&2
    exit 1
fi

git -C "$APP_DIR" pull --ff-only origin main
source "$HOME/virtualenv/Fuelledger/3.11/bin/activate"
python -m pip install -r "$APP_DIR/requirements.txt"

cd "$APP_DIR"
python manage.py migrate --noinput
python manage.py collectstatic --noinput
mkdir -p tmp
touch tmp/restart.txt
