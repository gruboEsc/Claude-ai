#!/usr/bin/env bash
# Consistent copy of the live SQLite database into backups/, keeping the last 14 days.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
name="bot-$(date +%Y-%m-%d_%H-%M).db"

# SQLite's backup API gives a consistent snapshot even while the bot is writing.
docker compose exec -T bot python -c "
import sqlite3
src = sqlite3.connect('data/bot.db')
dst = sqlite3.connect('data/$name')
src.backup(dst)
dst.close()
src.close()
"
mv "data/$name" "backups/$name"
gzip -f "backups/$name"
find backups -name 'bot-*.db.gz' -mtime +14 -delete
echo "$(date '+%F %T') saved backups/$name.gz"
