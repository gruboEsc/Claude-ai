#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu/Debian VPS. Run as root from the project folder:
#   bash scripts/setup_server.sh
# Installs Docker, starts the bot (restarting on crashes and reboots) and schedules a
# daily database backup. Safe to run again.
set -euo pipefail

cd "$(dirname "$0")/.."
DIR="$(pwd)"

if ! command -v docker >/dev/null 2>&1; then
    echo "==> Устанавливаю Docker"
    curl -fsSL https://get.docker.com | sh
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo
    echo "Создан файл .env. Заполни его (nano .env) и запусти этот скрипт ещё раз."
    exit 1
fi
if grep -q "^TELEGRAM_BOT_TOKEN=123456:ABC" .env; then
    echo "В .env остался пример токена. Впиши настоящий (nano .env) и запусти скрипт ещё раз."
    exit 1
fi

mkdir -p data backups
echo "==> Собираю и запускаю бота"
docker compose up -d --build

echo "==> Ставлю ежедневный бэкап базы (04:00)"
cat > /etc/cron.d/companion-bot-backup <<EOF
0 4 * * * root bash $DIR/scripts/backup.sh >> $DIR/backups/backup.log 2>&1
EOF
chmod 644 /etc/cron.d/companion-bot-backup

echo
echo "Готово! Бот работает и сам перезапустится после сбоя или перезагрузки сервера."
echo "  Логи:        docker compose logs -f"
echo "  Обновить:    bash scripts/update.sh"
echo "  Бэкап сейчас: bash scripts/backup.sh"
