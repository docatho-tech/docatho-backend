#!/usr/bin/env bash
#
# Server-side deploy script for the docatho backend.
# Invoked over SSH by .github/workflows/deploy.yml (or run manually on the EC2
# box). Pulls the latest code, syncs deps, migrates, collects static to S3,
# and restarts the app + celery services.
#
# Requires (on the server):
#   - the repo cloned at $APP_DIR
#   - uv installed and on PATH for the deploy user
#   - /etc/docatho/docatho.env populated (see docatho.env.example)
#   - passwordless sudo for `systemctl restart docatho-*` (see deploy/README.md)

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/docatho/docatho-backend}"
BRANCH="${DEPLOY_BRANCH:-master}"
ENV_FILE="${DOCATHO_ENV_FILE:-/etc/docatho/docatho.env}"

echo "==> Deploying docatho backend from origin/${BRANCH}"
cd "$APP_DIR"

echo "==> Fetching latest code"
git fetch --all --prune
git reset --hard "origin/${BRANCH}"

echo "==> Loading environment (${ENV_FILE})"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

echo "==> Installing dependencies (uv sync --locked)"
uv sync --locked

echo "==> Applying database migrations"
uv run python manage.py migrate --noinput

echo "==> Collecting static files (-> S3)"
uv run python manage.py collectstatic --noinput

echo "==> Restarting services"
sudo systemctl restart docatho-gunicorn.service
sudo systemctl restart docatho-celery.service
sudo systemctl restart docatho-celery-beat.service

echo "==> Deploy complete: $(git rev-parse --short HEAD)"
