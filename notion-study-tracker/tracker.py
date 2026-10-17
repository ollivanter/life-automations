#!/usr/bin/env python3
"""Self-resetting Notion study tracker.

Commands
  setup <parent page URL>   create the Daily Log and Reviews databases (run once)
  daily                     create today's page; on Mondays add last week's review,
                            on the 1st add last month's review (this is what the
                            scheduled job runs)
  today                     only create today's page
  weekly [--date D]         build the review for the week before D (default: today)
  monthly [--month YYYY-MM] build the review for a month (default: last month)
  sync                      add properties for blocks you added to config.json
  preview                   print today's page without touching Notion

Environment variables
  NOTION_TOKEN          internal integration secret (starts with ntn_ or secret_)
  NOTION_DAILY_DS_ID    data source ID of the Daily Log (printed by setup)
  NOTION_REVIEWS_DS_ID  data source ID of Study Reviews (printed by setup)
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"   # data-source API (2025-09-03+) with in_trash naming
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
COLORS = {"default", "gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"}

TITLE_DAILY = "Day"
TITLE_REVIEW = "Review"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass
class Block:
    key: str
    name: str
    time: str
    minutes: int
    subject: str | None = None              # fixed subject
    rotation: dict[str, str] | None = None  # weekday -> subject

    @property
    def subject_property(self) -> str | None:
        return f"{self.key.capitalize()} subject" if self.rotation else None

    @property
    def emoji(self) -> str:
        first = self.name.split(" ", 1)[0]
        return first if not first.isalnum() else "•"

    def subject_on(self, day: dt.date) -> str | None:
        if self.rotation:
            return self.rotation.get(WEEKDAYS[day.weekday()])
        return self.subject


@dataclass
class Config:
    timezone: str
    subjects: list[dict]
    blocks: list[Block]

    @property
    def subject_names(self) -> list[str]:
        return [s["name"] for s in self.subjects]


def load_config(path: Path = HERE / "config.json") -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cfg = Config(
        timezone=raw.get("timezone", "Asia/Kolkata"),
        subjects=raw["subjects"],
        blocks=[Block(**b) for b in raw["blocks"]],
    )
    problems = []
    names = set(cfg.subject_names)
    keys = set()
    for s in cfg.subjects:
        if s.get("color", "default") not in COLORS:
            problems.append(f"subject {s['name']}: color must be one of {sorted(COLORS)}")
    for b in cfg.blocks:
        if b.key in keys:
            problems.append(f"duplicate block key {b.key!r}")
        keys.add(b.key)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", b.key):
            problems.append(f"block key {b.key!r}: use lowercase letters, digits, underscores")
        if b.minutes <= 0:
            problems.append(f"block {b.key}: minutes must be positive")
        if b.rotation:
            for day, subj in b.rotation.items():
                if day not in WEEKDAYS:
                    problems.append(f"block {b.key}: unknown weekday {day!r} (use {WEEKDAYS})")
                if subj not in names:
                    problems.append(f"block {b.key}: {subj!r} is not in subjects")
        elif b.subject and b.subject not in names:
            problems.append(f"block {b.key}: {b.subject!r} is not in subjects")
    if problems:
        sys.exit("config.json problems:\n  " + "\n  ".join(problems))
    try:
        ZoneInfo(cfg.timezone)
    except Exception:
        sys.exit(f"config.json: unknown timezone {cfg.timezone!r}")
    return cfg


# --------------------------------------------------------------------------
# Notion client (plain HTTP, Notion-Version 2026-03-11)
# --------------------------------------------------------------------------
class NotionError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"Notion {status} {code}: {message}")
        self.status, self.code = status, code


class Notion:
    def __init__(self, token: str, session=None):
        import requests
        self.http = session or requests.Session()
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, body: dict | None = None, attempts: int = 5) -> dict:
        url = f"{NOTION_API}/{path.lstrip('/')}"
        for attempt in range(1, attempts + 1):
            try:
                resp = self.http.request(method, url, headers=self.headers, json=body, timeout=30)
            except Exception as exc:
                if attempt == attempts:
                    raise NotionError(0, "network_error", type(exc).__name__) from None
                time.sleep(2 * attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < attempts:
                    wait = float(resp.headers.get("Retry-After", 2 * attempt))
                    time.sleep(min(wait, 60))
                    continue
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code >= 400:
                raise NotionError(resp.status_code, data.get("code", "error"),
                                  data.get("message", resp.text[:300]))
            return data
        raise NotionError(0, "gave_up", f"{method} {path}")

    # Databases & data sources ------------------------------------------------
    def create_database(self, parent_page_id: str, title: str, icon: str,
                        properties: dict) -> tuple[str, str]:
        db = self.request("POST", "databases", {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "icon": {"type": "emoji", "emoji": icon},
            "is_inline": True,
            "initial_data_source": {"properties": properties},
        })
        sources = db.get("data_sources") or self.request("GET", f"databases/{db['id']}").get("data_sources", [])
        if not sources:
            raise NotionError(0, "no_data_source", f"database {db['id']} has no data source")
        return db["id"], sources[0]["id"]

    def get_data_source(self, ds_id: str) -> dict:
        return self.request("GET", f"data_sources/{ds_id}")

    def update_data_source(self, ds_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"data_sources/{ds_id}", {"properties": properties})

    def query(self, ds_id: str, filter_: dict | None = None, sorts: list | None = None) -> list[dict]:
        results, cursor = [], None
        while True:
            body: dict = {"page_size": 100}
            if filter_:
                body["filter"] = filter_
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            data = self.request("POST", f"data_sources/{ds_id}/query", body)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")

    # Pages ---------------------------------------------------------------------
    def create_page(self, ds_id: str, properties: dict, children: list[dict],
                    icon: str | None = None) -> dict:
        body = {
            "parent": {"type": "data_source_id", "data_source_id": ds_id},
            "properties": properties,
            "children": children[:100],
        }
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        page = self.request("POST", "pages", body)
        rest = children[100:]
        while rest:  # Notion accepts at most 100 children per request
            self.request("PATCH", f"blocks/{page['id']}/children", {"children": rest[:100]})
            rest = rest[100:]
        return page


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
def daily_schema(cfg: Config) -> dict:
    options = [{"name": s["name"], "color": s.get("color", "default")} for s in cfg.subjects]
    props: dict = {TITLE_DAILY: {"title": {}}, "Date": {"date": {}}}
    for b in cfg.blocks:
        props[b.name] = {"checkbox": {}}
        if b.subject_property:
            props[b.subject_property] = {"select": {"options": options}}
    props["Notes"] = {"rich_text": {}}
    return props


def progress_formula(cfg: Config) -> dict:
    parts = " + ".join(f'toNumber(prop("{b.name}"))' for b in cfg.blocks)
    expr = f'format(round(({parts}) / {len(cfg.blocks)} * 100)) + "%"'
    return {"Progress": {"formula": {"expression": expr}}}


def reviews_schema() -> dict:
    return {
        TITLE_REVIEW: {"title": {}},
        "Type": {"select": {"options": [{"name": "Weekly", "color": "blue"},
                                        {"name": "Monthly", "color": "purple"}]}},
        "Period": {"date": {}},
        "Completion": {"number": {"format": "percent"}},
        "Hours studied": {"number": {"format": "number"}},
        "Blocks done": {"number": {"format": "number"}},
        "Blocks planned": {"number": {"format": "number"}},
        "Full days": {"number": {"format": "number"}},
        "Best streak": {"number": {"format": "number"}},
        "Top subject": {"rich_text": {}},
    }


# --------------------------------------------------------------------------
# Pure helpers (no network) — easy to test
# --------------------------------------------------------------------------
def rt(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[:2000]}}]


def para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(text)}}


def bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(text)}}


def heading(text: str) -> dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt(text)}}


def day_title(day: dt.date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"


def daily_page(cfg: Config, day: dt.date) -> tuple[dict, list[dict]]:
    props: dict = {
        TITLE_DAILY: {"title": rt(day_title(day))},
        "Date": {"date": {"start": day.isoformat()}},
    }
    children = [para("Tick each block in the properties above when you finish it.")]
    for b in cfg.blocks:
        props[b.name] = {"checkbox": False}
        subj = b.subject_on(day)
        if b.subject_property and subj:
            props[b.subject_property] = {"select": {"name": subj}}
        label = f"{b.name} · {b.time} · {b.minutes} min"
        children.append(bullet(f"{label} · {subj}" if subj else label))
    return props, children


@dataclass
class DayRecord:
    date: dt.date
    done: dict[str, bool] = field(default_factory=dict)       # block key -> ticked
    subjects: dict[str, str | None] = field(default_factory=dict)


def parse_daily_page(page: dict, cfg: Config) -> DayRecord | None:
    props = page.get("properties", {})
    date_prop = (props.get("Date") or {}).get("date") or {}
    if not date_prop.get("start"):
        return None
    rec = DayRecord(dt.date.fromisoformat(date_prop["start"][:10]))
    for b in cfg.blocks:
        rec.done[b.key] = bool((props.get(b.name) or {}).get("checkbox"))
        if b.subject_property:
            sel = (props.get(b.subject_property) or {}).get("select")
            rec.subjects[b.key] = (sel or {}).get("name") or b.subject_on(rec.date)
        else:
            rec.subjects[b.key] = b.subject
    return rec


@dataclass
class Summary:
    start: dt.date
    end: dt.date
    days: list[DayRecord]
    done: int
    planned: int
    minutes: int
    by_subject: dict[str, list[int]]   # subject -> [minutes, sessions]
    by_block: dict[str, int]           # block key -> times done
    full_days: int
    best_streak: int

    @property
    def completion(self) -> float:
        return self.done / self.planned if self.planned else 0.0

    @property
    def hours(self) -> float:
        return round(self.minutes / 60, 1)

    @property
    def top_subject(self) -> str:
        if not self.by_subject:
            return "—"
        name, (mins, _) = max(self.by_subject.items(), key=lambda kv: kv[1][0])
        return f"{name} ({mins / 60:.1f} h)"


def summarize(records: list[DayRecord], cfg: Config, start: dt.date, end: dt.date) -> Summary:
    by_date: dict[dt.date, DayRecord] = {}
    for r in records:
        if start <= r.date <= end:
            by_date[r.date] = r  # if a day was duplicated, keep one
    days = [by_date[d] for d in sorted(by_date)]
    done = minutes = full = streak = best = 0
    by_subject: dict[str, list[int]] = {}
    by_block = {b.key: 0 for b in cfg.blocks}
    prev: dt.date | None = None
    for rec in days:
        all_done = True
        for b in cfg.blocks:
            if rec.done.get(b.key):
                done += 1
                minutes += b.minutes
                by_block[b.key] += 1
                subj = rec.subjects.get(b.key) or b.name
                entry = by_subject.setdefault(subj, [0, 0])
                entry[0] += b.minutes
                entry[1] += 1
            else:
                all_done = False
        if all_done:
            full += 1
            streak = streak + 1 if prev and (rec.date - prev).days == 1 and streak else 1
            best = max(best, streak)
        else:
            streak = 0
        prev = rec.date
    return Summary(start, end, days, done, len(days) * len(cfg.blocks), minutes,
                   by_subject, by_block, full, best)


def review_page(cfg: Config, s: Summary, kind: str, title: str) -> tuple[dict, list[dict]]:
    props = {
        TITLE_REVIEW: {"title": rt(title)},
        "Type": {"select": {"name": kind}},
        "Period": {"date": {"start": s.start.isoformat(), "end": s.end.isoformat()}},
        "Completion": {"number": round(s.completion, 4)},
        "Hours studied": {"number": s.hours},
        "Blocks done": {"number": s.done},
        "Blocks planned": {"number": s.planned},
        "Full days": {"number": s.full_days},
        "Best streak": {"number": s.best_streak},
        "Top subject": {"rich_text": rt(s.top_subject)},
    }
    children = [
        heading("Summary"),
        para(f"You completed {s.done} of {s.planned} blocks ({s.completion:.0%}) across "
             f"{len(s.days)} logged days and studied {s.hours:g} hours. "
             f"{s.full_days} day(s) had every block done; best streak {s.best_streak} day(s)."),
        heading("Hours by subject"),
    ]
    if s.by_subject:
        for subj, (mins, sessions) in sorted(s.by_subject.items(), key=lambda kv: -kv[1][0]):
            children.append(bullet(f"{subj} — {mins / 60:.1f} h ({sessions} session{'s' if sessions != 1 else ''})"))
    else:
        children.append(para("Nothing ticked in this period."))
    children.append(heading("Blocks"))
    for b in cfg.blocks:
        children.append(bullet(f"{b.name} — {s.by_block[b.key]}/{len(s.days)}"))
    children.append(heading("Day by day"))
    for rec in s.days:
        marks = "".join("✅" if rec.done.get(b.key) else "⬜" for b in cfg.blocks)
        count = sum(rec.done.get(b.key, False) for b in cfg.blocks)
        children.append(bullet(f"{rec.date:%a %d %b} — {marks} ({count}/{len(cfg.blocks)})"))
    return props, children


def previous_week(day: dt.date) -> tuple[dt.date, dt.date]:
    this_monday = day - dt.timedelta(days=day.weekday())
    start = this_monday - dt.timedelta(days=7)
    return start, start + dt.timedelta(days=6)


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def previous_month(day: dt.date) -> tuple[int, int]:
    return (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)


def weekly_title(start: dt.date) -> str:
    return f"Week of {start.day} {start:%b %Y}"


def monthly_title(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def parse_page_id(text: str) -> str:
    """Accept a Notion URL or ID and return a dashed UUID."""
    # The ID is the last 32 hex characters of the URL path's last segment,
    # e.g. notion.so/me/My-Page-1a2b...c6d (the slug before it can contain hex letters).
    segment = text.strip().split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
    found = re.search(r"([0-9a-fA-F]{32})$", segment.replace("-", ""))
    if not found:
        sys.exit("Couldn't find a page ID. Paste the page URL from Notion's Share → Copy link.")
    h = found.group(1).lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# --------------------------------------------------------------------------
# Actions that talk to Notion
# --------------------------------------------------------------------------
class Tracker:
    def __init__(self, notion: Notion, cfg: Config, daily_ds: str, reviews_ds: str):
        self.n, self.cfg, self.daily_ds, self.reviews_ds = notion, cfg, daily_ds, reviews_ds

    def ensure_day(self, day: dt.date) -> bool:
        existing = self.n.query(self.daily_ds, {"property": "Date", "date": {"equals": day.isoformat()}})
        if existing:
            print(f"Page for {day_title(day)} already exists")
            return False
        props, children = daily_page(self.cfg, day)
        self.n.create_page(self.daily_ds, props, children, icon="📅")
        print(f"Created page for {day_title(day)}")
        return True

    def records(self, start: dt.date, end: dt.date) -> list[DayRecord]:
        pages = self.n.query(self.daily_ds, {"and": [
            {"property": "Date", "date": {"on_or_after": start.isoformat()}},
            {"property": "Date", "date": {"on_or_before": end.isoformat()}},
        ]}, sorts=[{"property": "Date", "direction": "ascending"}])
        return [r for r in (parse_daily_page(p, self.cfg) for p in pages) if r]

    def ensure_review(self, kind: str, title: str, start: dt.date, end: dt.date,
                      force: bool = False) -> bool:
        existing = self.n.query(self.reviews_ds, {"property": TITLE_REVIEW, "title": {"equals": title}})
        if existing and not force:
            print(f"Review '{title}' already exists")
            return False
        recs = self.records(start, end)
        if not recs:
            print(f"No daily pages between {start} and {end}; skipping '{title}'")
            return False
        summary = summarize(recs, self.cfg, start, end)
        props, children = review_page(self.cfg, summary, kind, title)
        self.n.create_page(self.reviews_ds, props, children, icon="📊" if kind == "Weekly" else "🗓️")
        print(f"Created review '{title}': {summary.completion:.0%} done, {summary.hours:g} h")
        return True

    def weekly(self, day: dt.date) -> bool:
        start, end = previous_week(day)
        return self.ensure_review("Weekly", weekly_title(start), start, end)

    def monthly(self, year: int, month: int) -> bool:
        start, end = month_bounds(year, month)
        return self.ensure_review("Monthly", monthly_title(year, month), start, end)

    def daily(self, day: dt.date) -> None:
        self.ensure_day(day)
        # Checking every run (not only on Mondays / the 1st) means a missed
        # scheduled run is caught up the next day. Existing reviews are skipped.
        self.weekly(day)
        self.monthly(*previous_month(day))

    def sync(self) -> None:
        current = self.n.get_data_source(self.daily_ds).get("properties", {})
        wanted = daily_schema(self.cfg)
        missing = {k: v for k, v in wanted.items() if k not in current}
        if not missing:
            print("Daily Log already has every property in config.json")
        else:
            self.n.update_data_source(self.daily_ds, missing)
            print("Added properties: " + ", ".join(missing))
        try:
            self.n.update_data_source(self.daily_ds, progress_formula(self.cfg))
            print("Updated the Progress formula")
        except NotionError as exc:
            print(f"Couldn't update the Progress formula ({exc}); everything else works without it")


def setup(notion: Notion, cfg: Config, parent: str) -> tuple[str, str]:
    page_id = parse_page_id(parent)
    print(f"Creating databases under page {page_id} …")
    _, daily_ds = notion.create_database(page_id, "Daily Log", "📅", daily_schema(cfg))
    try:
        notion.update_data_source(daily_ds, progress_formula(cfg))
    except NotionError as exc:
        print(f"  (skipped the optional Progress formula: {exc})")
    _, reviews_ds = notion.create_database(page_id, "Study Reviews", "📊", reviews_schema())
    print("\nDone! Add these as GitHub repository secrets:\n")
    print(f"  NOTION_DAILY_DS_ID   = {daily_ds}")
    print(f"  NOTION_REVIEWS_DS_ID = {reviews_ds}\n")
    return daily_ds, reviews_ds


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _need(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"Set {name} first (see README)")
    return value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("parent", help="URL or ID of the Notion page to put the databases in")
    for name in ("daily", "today", "weekly", "preview"):
        sp = sub.add_parser(name)
        sp.add_argument("--date", help="pretend today is YYYY-MM-DD")
    m = sub.add_parser("monthly")
    m.add_argument("--month", help="YYYY-MM (default: last month)")
    sub.add_parser("sync")
    args = p.parse_args(argv)

    cfg = load_config()
    today = (dt.date.fromisoformat(args.date) if getattr(args, "date", None)
             else dt.datetime.now(ZoneInfo(cfg.timezone)).date())

    if args.cmd == "preview":
        props, children = daily_page(cfg, today)
        print(day_title(today))
        for b in cfg.blocks:
            print(f"  ☐ {b.name:<26} {b.time:<14} {b.subject_on(today) or ''}")
        return 0

    notion = Notion(_need("NOTION_TOKEN"))
    try:
        if args.cmd == "setup":
            setup(notion, cfg, args.parent)
            return 0
        tracker = Tracker(notion, cfg, _need("NOTION_DAILY_DS_ID"), _need("NOTION_REVIEWS_DS_ID"))
        if args.cmd == "daily":
            tracker.daily(today)
        elif args.cmd == "today":
            tracker.ensure_day(today)
        elif args.cmd == "weekly":
            tracker.weekly(today)
        elif args.cmd == "monthly":
            if args.month:
                y, mo = (int(x) for x in args.month.split("-"))
            else:
                y, mo = previous_month(today)
            tracker.monthly(y, mo)
        elif args.cmd == "sync":
            tracker.sync()
    except NotionError as exc:
        hint = ""
        if exc.status in (401, 403):
            hint = "\nCheck NOTION_TOKEN, and that the page is shared with your integration (••• → Connections)."
        elif exc.status == 404:
            hint = "\nNotion can't see that page/database. Share it with your integration (••• → Connections)."
        sys.exit(f"{exc}{hint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
