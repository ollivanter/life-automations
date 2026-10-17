# 📅 Notion study tracker

A study log in Notion that sets itself up every morning and writes your weekly and monthly reports for you.

- **Every day at 04:11 IST** a new page appears in **Daily Log** with a checkbox for each study block and today's subject already filled in (subjects rotate by weekday, with Maths getting the most slots).
- **Every Monday** a **Study Reviews** page summarises last week: blocks done, completion %, hours per subject, full days and your best streak.
- **On the 1st of each month** you get the same for the whole month.

You only ever tick boxes. Everything else is automatic.

## Default routine (edit `config.json`)

| Block | Time | Subject |
|---|---|---|
| 🌅 Morning study | 5:00–6:15 AM | Rotates: Maths ×3, Physics, EE (weekdays) |
| 🇩🇪 German on the metro | 6:45–7:45 AM | German |
| 🌆 Evening study | 6:00–8:00 PM | Rotates: Physics ×2, Mechanics, EE, Maths |
| 🛡️ Cybersecurity | 8:30–9:00 PM | Cybersecurity |

Change names, times, minutes, the subject list or the weekday rotation in `config.json`. After adding a new block, run the `sync` command so the database gets the new checkbox.

## Setup (about 10 minutes)

1. **Create an integration.** Go to <https://www.notion.so/my-integrations> → **New integration** → name it *Study Tracker*, pick your workspace, type **Internal**. Copy the **Internal Integration Secret**.
2. **Make a home page.** In Notion, create a page called *Study Tracker*. Open its **•••** menu → **Connections** → add *Study Tracker*. Then **Share → Copy link**.
3. **Add the secret to GitHub.** Repo **Settings → Secrets and variables → Actions → New repository secret**: `NOTION_TOKEN` = the secret from step 1.
4. **Create the databases.** **Actions → Notion study tracker → Run workflow**, choose `setup` and paste the page link into `parent_page`. When it finishes, open the run's log. It prints two IDs.
5. **Save the IDs** as two more secrets: `NOTION_DAILY_DS_ID` and `NOTION_REVIEWS_DS_ID`.
6. **Test it.** **Run workflow** → `today`. Today's page should appear in Notion.

Tip: in Notion, switch the Daily Log to a **Calendar** or **Board** view, and add a filter like *Date is within this week* so it stays tidy.

## Commands

| Command | What it does |
|---|---|
| `daily` | Today's page, plus last week's / last month's review if they don't exist yet (what the schedule runs, so a missed day catches up) |
| `today` | Only today's page |
| `weekly` | Review of last week |
| `monthly` | Review of last month (`--month 2026-10` for a specific one) |
| `sync` | Add properties for blocks you added to `config.json` |
| `setup` | Create both databases under a page |
| `preview` | Print today's page without touching Notion |

Run them from the Actions tab, or on your laptop:

```bash
pip install requests
export NOTION_TOKEN=... NOTION_DAILY_DS_ID=... NOTION_REVIEWS_DS_ID=...
python tracker.py today
python tracker.py preview --date 2026-10-20
python -m unittest -v      # 14 offline tests against a fake Notion API
```

## Notes

- Reviews only count days that have a page, so days before you set it up aren't counted as missed.
- If you tick a box after a review was written, delete that review page and run `weekly` (or `monthly`) again.
- Built on Notion API version `2026-03-11` (data sources), using plain HTTP requests, so there's no SDK to keep up to date.
