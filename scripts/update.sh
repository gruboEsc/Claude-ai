#!/usr/bin/env bash
# Pull the latest code and restart the bot. The database and .env are kept.
set -euo pipefail

cd "$(dirname "$0")/.."
bash scripts/backup.sh || echo "Бэкап не удался — продолжаю обновление"
git pull --ff-only
docker compose up -d --build
echo "Обновлено. Логи: docker compose logs -f"
