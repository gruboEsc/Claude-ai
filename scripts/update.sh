#!/usr/bin/env bash
# Pull the latest code and restart the bot. The database and .env are kept.
set -euo pipefail

cd "$(dirname "$0")/.."

# With a DOMAIN in .env, also run the HTTPS proxy for webhooks (LINE).
compose_profiles() {
    if grep -qE '^DOMAIN=.+' .env 2>/dev/null; then echo "--profile web"; fi
}
bash scripts/backup.sh || echo "Бэкап не удался — продолжаю обновление"
git pull --ff-only
docker compose $(compose_profiles) up -d --build
echo "Обновлено. Логи: docker compose logs -f"
