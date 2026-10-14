#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/fuelledger"

if [[ ! -f .env ]]; then
    echo "Missing $HOME/fuelledger/.env; create it from .env.example first." >&2
    exit 1
fi

git pull --ff-only origin main
source "$HOME/virtualenv/fuelledger/3.14/bin/activate"
python -m pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
mkdir -p tmp
touch tmp/restart.txt
