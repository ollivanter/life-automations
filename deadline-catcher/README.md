# 📌 Deadline catcher

A Google Apps Script that reads your college emails every hour, finds assignment, quiz and exam dates, and puts them in a **College Deadlines** Google Calendar with pop-up reminders two evenings and one evening before.

It runs on Google's servers for free. Nothing to host.

## What it understands

- `24/10/2026`, `24-10-26`, `24.10.2026`, `2026-10-24` (day first, as written in India)
- `3rd November`, `24 Oct 2026`, `Oct 30, 2026`, `October 24th`
- `tomorrow`, `tonight`, `by Friday`, `before next Wednesday`
- Times like `11:59 PM`, `5 pm`, `10.30 am`, `14:00`, `midnight`

It only adds a date when the sentence around it looks like a deadline (*due, deadline, submit, last date, exam, ISA, quiz…*). Past dates are ignored, and each event links back to the email.

Emails it can't be sure about (several dates with no deadline wording, or a 10-exam timetable) get the Gmail label **Deadline Catcher/Check manually** instead of cluttering your calendar.

## Setup (about 5 minutes, on a laptop)

The Apps Script editor doesn't work well on phones.

1. Sign in to the Google account that **receives your college emails** and open <https://script.google.com> → **New project**. Rename it *Deadline Catcher*.
2. Delete the sample code, paste in all of `Code.gs` and press **Save**.
3. **Project Settings** (⚙️ on the left) → **Time zone** → *(GMT+05:30) India Standard Time*.
4. Check `SENDERS` at the top of the code. It's set to `pes.edu`, which matches any address ending in `pes.edu`. Open a real college email and confirm the sender's address. Add professors who email from personal Gmail if needed.
5. In the toolbar, pick **`setup`** from the function menu and press **Run**.
6. Google asks for permission. Choose your account → **Advanced** → **Go to Deadline Catcher (unsafe)** → **Allow**. ("Unsafe" just means Google hasn't reviewed your personal script.)

`setup` creates the calendar and labels, schedules an hourly check, and runs a first scan of the last 14 days. See what happened under **Executions** on the left.

## Handy functions

| Function | Use |
|---|---|
| `setup` | First-time setup (safe to run again) |
| `run` | Scan now instead of waiting for the hour |
| `testParser` | Try the date finder on sample sentences (see the log) |
| `resetSeen` | Re-read recent emails after you change settings. Events are never duplicated. |

## Testing locally

The date finder is plain JavaScript, so it runs in Node too:

```bash
TZ=Asia/Kolkata node test.js    # 29 tests, including a fake Gmail + Calendar run
```

## Notes

- If your college account is managed by the college (Google Workspace), the admin may block Apps Script. In that case forward college mail to a personal Gmail and run the script there.
- Reminder times are in `CONFIG.REMINDERS`. Calendar allows up to 5 reminders and at most 4 weeks ahead.
- To stop it: **Triggers** (⏰ on the left) → delete the `run` trigger.
