# Claude-ai — Telegram-бот на Claude

Умный Telegram-бот на модели Claude (по умолчанию `claude-opus-5-5`).

## Что умеет

- 💬 **Живые ответы** — текст печатается прямо в сообщении, как в ChatGPT
- 🧠 **Память диалога** — помнит весь разговор (SQLite); очень длинные диалоги
  сжимаются автоматически на стороне API
- 📷 **Фото** — описывает, читает текст, решает задачи с картинки
- 📄 **PDF и файлы** — PDF, код, `.txt`, `.md`, `.csv`, `.json` и т.д.
- 🌐 **Поиск в интернете** — сам ищет свежие данные и даёт ссылки на источники
- 🎭 **Режимы** (`/mode`): ассистент, программист, переводчик, копирайтер, учитель, остряк
- 🔬 **Глубина размышлений** (`/effort`): от «быстро» до «максимум»
- 👥 **Группы** — отвечает, когда упомянули или ответили на его сообщение
- 🔒 **Белый список** — только для своих, чтобы не сжечь бюджет на API
- 📊 **Статистика** токенов (`/stats`), красивое форматирование, разбиение длинных ответов

## Быстрый старт

1. Создай бота у [@BotFather](https://t.me/BotFather) и возьми токен.
2. Получи ключ API в [console.anthropic.com](https://console.anthropic.com/).
3. Настрой и запусти:

```bash
cp .env.example .env        # впиши TELEGRAM_BOT_TOKEN и ANTHROPIC_API_KEY
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m bot
```

4. Напиши боту `/start` — он покажет твой id. Впиши его в `ALLOWED_USERS` в `.env`
   и перезапусти бота.

### Docker

```bash
docker build -t claude-bot .
docker run -d --restart=always --env-file .env -v $(pwd)/data:/app/data claude-bot
```

## Настройки (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | токен от BotFather (обязательно) |
| `ANTHROPIC_API_KEY` | — | ключ Claude API |
| `ALLOWED_USERS` | пусто (все) | id пользователей через запятую |
| `CLAUDE_MODEL` | `claude-opus-5-5` | модель |
| `DEFAULT_EFFORT` | `medium` | `low` / `medium` / `high` / `xhigh` / `max` |
| `MAX_TOKENS` | `32000` | лимит длины ответа |
| `WEB_SEARCH` | `1` | поиск в интернете (`0` — выключить) |
| `DB_PATH` | `data/bot.db` | файл базы |

## Устройство

```
bot/
  __main__.py    запуск, команды в меню
  handlers.py    команды, сообщения, стриминг в Telegram
  claude.py      запросы к Claude: стриминг, поиск, кэш, сжатие, запасная модель
  db.py          SQLite: история и настройки чатов
  formatting.py  Markdown → HTML для Telegram, разбиение длинных ответов
  personas.py    режимы и системные промпты
```

Что внутри запросов к Claude:
- история только дописывается и кэшируется, поэтому длинные диалоги дешёвые;
- если Claude отказывается отвечать, запрос автоматически повторяется на запасной
  модели (`fallbacks: "default"`).

Тесты: `pip install pytest && pytest`
