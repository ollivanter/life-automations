"""Offline tests against an in-memory fake of the Notion API.
Run: python -m unittest -v
"""
import datetime as dt
import itertools
import unittest
from unittest import mock

import tracker as t

CFG = t.load_config()
_ids = itertools.count(1)


def new_id():
    return f"00000000-0000-0000-0000-{next(_ids):012d}"


class Resp:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers, self.text = status, data, headers or {}, ""

    def json(self):
        return self._data


class FakeNotion:
    """Just enough of the Notion API (data-source version) to exercise tracker.py."""

    def __init__(self):
        self.sources = {}   # ds_id -> {"properties": {...}, "pages": [...]}
        self.log = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        assert headers["Notion-Version"] == t.NOTION_VERSION
        path = url.split("/v1/", 1)[1]
        self.log.append((method, path, json))
        parts = path.split("/")
        if method == "POST" and path == "databases":
            assert "initial_data_source" in json and "properties" not in json
            assert json["parent"]["type"] == "page_id"
            ds = new_id()
            self.sources[ds] = {"properties": dict(json["initial_data_source"]["properties"]), "pages": []}
            return Resp(200, {"object": "database", "id": new_id(), "data_sources": [{"id": ds, "name": "x"}]})
        if parts[0] == "data_sources" and len(parts) == 2:
            src = self.sources[parts[1]]
            if method == "GET":
                return Resp(200, {"object": "data_source", "properties": src["properties"]})
            src["properties"].update(json["properties"])
            return Resp(200, {"object": "data_source"})
        if method == "POST" and len(parts) == 3 and parts[2] == "query":
            src = self.sources[parts[1]]
            hits = [p for p in src["pages"] if self._match(p, json.get("filter"))]
            size = json.get("page_size", 100)
            start = int(json.get("start_cursor") or 0)
            chunk = hits[start:start + 2]  # tiny pages to exercise pagination
            more = start + 2 < len(hits)
            return Resp(200, {"results": chunk, "has_more": more, "next_cursor": str(start + 2) if more else None})
        if method == "POST" and path == "pages":
            assert json["parent"]["type"] == "data_source_id"
            assert len(json["children"]) <= 100
            src = self.sources[json["parent"]["data_source_id"]]
            for name in json["properties"]:
                assert name in src["properties"], f"unknown property {name!r}"
            page = {"id": new_id(), "properties": self._as_read(json["properties"], src), "children": list(json["children"])}
            src["pages"].append(page)
            return Resp(200, page)
        if method == "PATCH" and parts[0] == "blocks":
            for src in self.sources.values():
                for p in src["pages"]:
                    if p["id"] == parts[1]:
                        p["children"].extend(json["children"])
            return Resp(200, {})
        raise AssertionError(f"unexpected {method} {path}")

    @staticmethod
    def _as_read(props, src):
        out = {}
        for name, value in props.items():
            kind = next(iter(src["properties"][name]))
            out[name] = {"type": kind, **value}
        return out

    def _match(self, page, f):
        if not f:
            return True
        if "and" in f:
            return all(self._match(page, x) for x in f["and"])
        prop = page["properties"].get(f["property"], {})
        if "date" in f:
            d = prop["date"]["start"]
            cond = f["date"]
            return all({"equals": d == v, "on_or_after": d >= v, "on_or_before": d <= v}[k]
                       for k, v in cond.items())
        if "title" in f:
            return "".join(x["text"]["content"] for x in prop["title"]) == f["title"]["equals"]
        raise AssertionError(f)

    def tick(self, ds, day, keys):
        for p in self.sources[ds]["pages"]:
            if p["properties"]["Date"]["date"]["start"] == day.isoformat():
                for b in CFG.blocks:
                    if b.key in keys:
                        p["properties"][b.name]["checkbox"] = True


def make_tracker():
    fake = FakeNotion()
    notion = t.Notion("TOKEN", session=fake)
    with mock.patch("builtins.print"):
        daily_ds, reviews_ds = t.setup(notion, CFG, "https://www.notion.so/Study-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d?pvs=4")
    return fake, t.Tracker(notion, CFG, daily_ds, reviews_ds)


class Pure(unittest.TestCase):
    def test_parse_page_id(self):
        self.assertEqual(t.parse_page_id("https://www.notion.so/me/My-Page-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d?pvs=4"),
                         "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d")
        self.assertEqual(t.parse_page_id("1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"),
                         "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d")
        self.assertEqual(t.parse_page_id("https://www.notion.so/Deadline-Face-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d/"),
                         "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d")
        self.assertEqual(t.parse_page_id(" 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d "),
                         "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d")

    def test_rotation(self):
        mon, tue = dt.date(2026, 10, 19), dt.date(2026, 10, 20)
        morning = next(b for b in CFG.blocks if b.key == "morning")
        self.assertEqual(morning.subject_on(mon), "Mathematics")
        self.assertEqual(morning.subject_on(tue), "Physics")

    def test_week_and_month_bounds(self):
        self.assertEqual(t.previous_week(dt.date(2026, 10, 26)), (dt.date(2026, 10, 19), dt.date(2026, 10, 25)))
        self.assertEqual(t.previous_week(dt.date(2026, 10, 28)), (dt.date(2026, 10, 19), dt.date(2026, 10, 25)))
        self.assertEqual(t.previous_month(dt.date(2027, 1, 1)), (2026, 12))
        self.assertEqual(t.month_bounds(2028, 2), (dt.date(2028, 2, 1), dt.date(2028, 2, 29)))

    def test_streak_and_totals(self):
        keys = [b.key for b in CFG.blocks]
        d0 = dt.date(2026, 10, 19)
        recs = []
        pattern = [keys, keys, keys[:1], keys, keys, keys, []]   # full,full,partial,full,full,full,none
        for i, done in enumerate(pattern):
            r = t.DayRecord(d0 + dt.timedelta(days=i))
            r.done = {k: k in done for k in keys}
            r.subjects = {b.key: b.subject_on(r.date) for b in CFG.blocks}
            recs.append(r)
        s = t.summarize(recs, CFG, d0, d0 + dt.timedelta(days=6))
        self.assertEqual(s.full_days, 5)
        self.assertEqual(s.best_streak, 3)
        self.assertEqual(s.planned, 7 * len(keys))
        self.assertEqual(s.done, 5 * len(keys) + 1)
        per_full_day = sum(b.minutes for b in CFG.blocks)
        self.assertEqual(s.minutes, 5 * per_full_day + CFG.blocks[0].minutes)

    def test_streak_breaks_on_gap(self):
        keys = [b.key for b in CFG.blocks]
        days = [dt.date(2026, 10, 19), dt.date(2026, 10, 21)]  # missing day in between
        recs = [t.DayRecord(d, {k: True for k in keys}, {}) for d in days]
        s = t.summarize(recs, CFG, days[0], days[-1])
        self.assertEqual(s.best_streak, 1)

    def test_formula_mentions_every_block(self):
        expr = t.progress_formula(CFG)["Progress"]["formula"]["expression"]
        for b in CFG.blocks:
            self.assertIn(f'prop("{b.name}")', expr)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        p = mock.patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)
        self.fake, self.tr = make_tracker()

    def test_setup_creates_two_sources_with_formula(self):
        self.assertEqual(len(self.fake.sources), 2)
        daily = self.fake.sources[self.tr.daily_ds]["properties"]
        self.assertIn("Progress", daily)
        for b in CFG.blocks:
            self.assertIn(b.name, daily)

    def test_daily_is_idempotent(self):
        day = dt.date(2026, 10, 19)
        self.assertTrue(self.tr.ensure_day(day))
        self.assertFalse(self.tr.ensure_day(day))
        self.assertEqual(len(self.fake.sources[self.tr.daily_ds]["pages"]), 1)

    def test_full_week_then_weekly_review(self):
        start = dt.date(2026, 10, 19)
        for i in range(7):
            day = start + dt.timedelta(days=i)
            self.tr.daily(day)
            self.fake.tick(self.tr.daily_ds, day, {"morning", "evening"} if i % 2 == 0 else {"morning"})
        # Monday: daily() should also produce last week's review, once.
        self.tr.daily(dt.date(2026, 10, 26))
        self.tr.daily(dt.date(2026, 10, 27))
        reviews = self.fake.sources[self.tr.reviews_ds]["pages"]
        self.assertEqual(len(reviews), 1)
        r = reviews[0]["properties"]
        self.assertEqual(r["Blocks done"]["number"], 7 + 4)
        self.assertEqual(r["Blocks planned"]["number"], 7 * len(CFG.blocks))
        self.assertEqual(r["Type"]["select"]["name"], "Weekly")
        self.assertEqual(r["Period"]["date"], {"start": "2026-10-19", "end": "2026-10-25"})
        self.assertEqual(r["Hours studied"]["number"], round((7 * 75 + 4 * 120) / 60, 1))

    def test_monthly_review_on_first(self):
        for i in range(31):
            self.tr.ensure_day(dt.date(2026, 10, 1) + dt.timedelta(days=i))
        self.tr.daily(dt.date(2026, 11, 1))
        titles = ["".join(x["text"]["content"] for x in p["properties"]["Review"]["title"])
                  for p in self.fake.sources[self.tr.reviews_ds]["pages"]]
        self.assertIn("October 2026", titles)
        monthly = next(p for p in self.fake.sources[self.tr.reviews_ds]["pages"]
                       if p["properties"]["Type"]["select"]["name"] == "Monthly")
        day_lines = [c for c in monthly["children"] if c["type"] == "bulleted_list_item"
                     and " — " in c["bulleted_list_item"]["rich_text"][0]["text"]["content"]
                     and "/" in c["bulleted_list_item"]["rich_text"][0]["text"]["content"]]
        self.assertGreaterEqual(len(day_lines), 31)

    def test_no_review_without_data(self):
        self.assertFalse(self.tr.weekly(dt.date(2026, 10, 26)))
        self.assertEqual(self.fake.sources[self.tr.reviews_ds]["pages"], [])

    def test_sync_adds_new_block(self):
        cfg2 = t.Config(CFG.timezone, CFG.subjects,
                        CFG.blocks + [t.Block("reading", "📖 Reading", "9 PM", 20, subject="German")])
        tr2 = t.Tracker(self.tr.n, cfg2, self.tr.daily_ds, self.tr.reviews_ds)
        tr2.sync()
        self.assertIn("📖 Reading", self.fake.sources[self.tr.daily_ds]["properties"])
        tr2.ensure_day(dt.date(2026, 10, 30))

    def test_retry_on_rate_limit(self):
        calls = {"n": 0}
        real = self.fake.request

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return Resp(429, {"code": "rate_limited", "message": "slow down"}, {"Retry-After": "0"})
            return real(*a, **k)

        self.fake.request = flaky
        with mock.patch.object(t.time, "sleep"):
            self.assertTrue(self.tr.ensure_day(dt.date(2026, 12, 1)))

    def test_error_message(self):
        def denied(*a, **k):
            return Resp(404, {"code": "object_not_found", "message": "Could not find data source"})
        self.fake.request = denied
        with self.assertRaises(t.NotionError) as ctx:
            self.tr.ensure_day(dt.date(2026, 12, 1))
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
