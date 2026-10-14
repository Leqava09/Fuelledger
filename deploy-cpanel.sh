#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$HOME/Fuelledger"
APP_DIR="$HOME/fuelledger"

if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "Missing Git repository at $REPO_DIR." >&2
    exit 1
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "Missing $APP_DIR/.env; create it from $REPO_DIR/.env.example first." >&2
    exit 1
fi

git -C "$REPO_DIR" pull --ff-only origin main
source "$HOME/virtualenv/fuelledger/3.11/bin/activate"
python -m pip install -r "$REPO_DIR/requirements.txt"

rsync -a --exclude='.git/' --exclude='.env' --exclude='tmp/' "$REPO_DIR/" "$APP_DIR/"
cd "$APP_DIR"
python manage.py migrate --noinput
python manage.py collectstatic --noinput
mkdir -p tmp
touch tmp/restart.txt
