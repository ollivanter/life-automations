# 🇩🇪 German vocab bot

A Telegram bot that turns the metro commute into German practice.

| When (IST) | What you get |
|---|---|
| Mon–Fri 06:20 | 10 new words, each with an example sentence and translation |
| Mon–Fri 16:25 | A 6-question Telegram quiz on that morning's words |
| Saturday 10:03 | A 10-question review of the whole week |

There's no server and no database. The day's words are worked out from the date, so GitHub Actions can run it on a schedule for free. Quizzes use Telegram's built-in quiz polls, so answers are checked instantly with an explanation.

## Setup (about 10 minutes, works from a phone)

1. **Create the bot.** In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`. Copy the token it gives you.
2. **Say hi.** Open your new bot and press **Start** (or send any message). The bot can only message people who've messaged it first.
3. **Add secrets to GitHub.** In your repo: **Settings → Secrets and variables → Actions → New repository secret**
   - `TELEGRAM_BOT_TOKEN` = the token from step 1
4. **Find your chat ID.** **Actions → German vocab bot → Run workflow**, choose `chatid`. Open the run's log: the first number on the line with your name is your chat ID. Add it as a secret:
   - `TELEGRAM_CHAT_ID` = that number
5. **Pick your start day.** Same page, **Variables** tab → `START_DATE` = the Monday you want lesson 1 on, e.g. `2026-10-19`.
6. **Test it.** **Run workflow** with `morning`, then with `evening`. Messages should arrive within a minute.

That's it. The schedule runs on its own from now on.

## Settings (optional repository variables)

| Variable | Default | Meaning |
|---|---|---|
| `START_DATE` | 2026-10-19 | Lesson 1. Lessons only advance on weekdays. |
| `WORDS_PER_DAY` | 10 | New words each morning |
| `QUIZ_QUESTIONS` | 6 | Evening quiz length |
| `REVIEW_QUESTIONS` | 10 | Saturday review length |

To change the times, edit the `cron` lines in `.github/workflows/german-bot.yml` (they're in UTC: subtract 5h30m from IST) **and** the matching `case` lines below them.

## Adding words

`vocab.csv` has 200 everyday A1–A2 words grouped by theme (commute, uni, tech, routine, food…). Add rows in the same format; open it in Google Sheets or any spreadsheet app and export as CSV:

```csv
german,english,example_de,example_en
die Klausur,written exam,Die Klausur war schwer.,The exam was hard.
```

Put the article on nouns (`der/die/das`) and start verb meanings with `to` — the quiz uses these to pick believable wrong answers. When the list runs out it starts again from the top.

## Running it on your laptop

```bash
pip install requests
python bot.py preview                         # today's morning message, no Telegram needed
python bot.py preview --preview-mode evening --date 2026-10-21
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python bot.py morning --force                 # --force sends Friday's lesson on weekends
python -m unittest -v                         # 15 offline tests
```

## Notes

- GitHub can start scheduled runs a few minutes late at busy times. The morning run is set 25 minutes before the 06:45 metro to leave room.
- On a public repo, GitHub pauses scheduled workflows after 60 days with no commits. If you get that email, open the Actions tab and press **Enable workflow** (or push any commit).
- Never commit the bot token. If it leaks, send `/revoke` to @BotFather and update the secret.
