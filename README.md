# ⚙️ Life automations

Three small automations that run my everyday student routine, all on free infrastructure.

| | Project | What it does | Runs on |
|---|---|---|---|
| 🇩🇪 | [German vocab bot](german-vocab-bot/) | Sends 10 German words every morning for the metro ride and quizzes me on the way home | Python · Telegram Bot API · GitHub Actions |
| 📅 | [Notion study tracker](notion-study-tracker/) | Creates a daily study checklist in Notion and writes weekly and monthly progress reports | Python · Notion API · GitHub Actions |
| 📌 | [Deadline catcher](deadline-catcher/) | Finds deadlines in college emails and adds them to Google Calendar with reminders | JavaScript · Google Apps Script |

```
                ┌──────────── GitHub Actions (cron) ────────────┐
06:20 Mon–Fri ─►│ bot.py morning  ──► Telegram: today's 10 words │
16:25 Mon–Fri ─►│ bot.py evening  ──► Telegram: quiz polls       │
10:03 Sat     ─►│ bot.py review   ──► Telegram: weekly review    │
04:11 daily   ─►│ tracker.py daily ─► Notion: today's page,      │
                │                     weekly/monthly reviews     │
                └────────────────────────────────────────────────┘
every hour ─► Apps Script: Gmail (college) ─► parse dates ─► Google Calendar
```

## Set up

Each folder's README has step-by-step instructions. You'll need:

| Secret (GitHub → Settings → Secrets and variables → Actions) | From |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | @BotFather in Telegram |
| `NOTION_TOKEN`, `NOTION_DAILY_DS_ID`, `NOTION_REVIEWS_DS_ID` | notion.so/my-integrations, then the `setup` command |

The deadline catcher lives in Google Apps Script, not GitHub Actions, because that's the only way to read Gmail and write Calendar without managing OAuth tokens.

## Design choices

- **Stateless.** The bot works out the day's lesson from the date, and the tracker treats Notion as its database, so nothing needs a server or storage.
- **Idempotent.** Every job checks before it creates, so re-running or a delayed cron never duplicates a page, review or calendar event.
- **Current APIs.** Telegram Bot API 10.x (`correct_option_ids`, with a fallback) and Notion API `2026-03-11` (data sources).
- **Tested offline.** 58 tests run on every push without touching real accounts: fake Telegram and Notion HTTP clients, and fake Gmail/Calendar services for the Apps Script.

```bash
pip install -r requirements.txt
(cd german-vocab-bot && python -m unittest)
(cd notion-study-tracker && python -m unittest)
(cd deadline-catcher && TZ=Asia/Kolkata node test.js)
```

## Cost

₹0. Telegram bots, Notion integrations and Apps Script are free, and these jobs use a few minutes of GitHub Actions per day.

## License

MIT
