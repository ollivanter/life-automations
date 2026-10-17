"""Offline tests: python -m unittest -v (no Telegram account needed)."""
import datetime as dt
import random
import unittest
from pathlib import Path
from unittest import mock

import bot

VOCAB = bot.load_vocab(Path(bot.HERE / "vocab.csv"))
CFG = bot.Config(start_date=dt.date(2026, 10, 19), words_per_day=10, quiz_size=6, review_size=10)


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    """Records calls; replies with queued responses (default: ok)."""
    def __init__(self, replies=None):
        self.calls, self.replies = [], list(replies or [])

    def post(self, url, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json))
        return FakeResponse(self.replies.pop(0) if self.replies else {"ok": True, "result": {}})


class LessonMath(unittest.TestCase):
    def test_weekdays_only(self):
        mon = dt.date(2026, 10, 19)
        self.assertEqual(bot.lesson_number(mon, CFG), 0)
        self.assertEqual(bot.lesson_number(mon + dt.timedelta(days=4), CFG), 4)   # Fri
        self.assertEqual(bot.lesson_number(mon + dt.timedelta(days=7), CFG), 5)   # next Mon
        self.assertEqual(bot.lesson_number(mon + dt.timedelta(days=28), CFG), 20)

    def test_before_start_is_lesson_zero(self):
        self.assertEqual(bot.lesson_number(dt.date(2026, 10, 1), CFG), 0)

    def test_matches_brute_force(self):
        start = CFG.start_date
        for n in range(0, 400, 7):
            end = start + dt.timedelta(days=n)
            brute = sum((start + dt.timedelta(days=i)).weekday() < 5 for i in range(n))
            self.assertEqual(bot.weekdays_between(start, end), brute)

    def test_words_wrap_around(self):
        lesson = len(VOCAB) // 10  # first lesson of round 2
        self.assertEqual(bot.words_for_lesson(VOCAB, lesson, 10)[0], VOCAB[0])
        self.assertEqual(len(bot.words_for_lesson(VOCAB, 7, 10)), 10)

    def test_week_has_five_lessons(self):
        words = bot.week_words(VOCAB, dt.date(2026, 10, 24), CFG)  # Saturday
        self.assertEqual(len(words), 50)


class Quiz(unittest.TestCase):
    def test_every_poll_is_valid(self):
        for day in range(60):
            rng = random.Random(day)
            targets = bot.words_for_lesson(VOCAB, day, 10)
            for poll in bot.build_quiz(targets, VOCAB, 10, rng):
                texts = [o["text"] for o in poll["options"]]
                self.assertEqual(len(texts), 4)
                self.assertEqual(len(set(t.lower() for t in texts)), 4, texts)
                self.assertLessEqual(len(poll["question"]), bot.MAX_POLL_QUESTION)
                self.assertLessEqual(len(poll["explanation"]), bot.MAX_POLL_EXPLANATION)
                self.assertTrue(all(0 < len(t) <= bot.MAX_POLL_OPTION for t in texts))
                right = texts[poll["correct_option_ids"][0]]
                self.assertIn(right, poll["explanation"])

    def test_same_day_same_quiz(self):
        day = dt.date(2026, 10, 21)
        self.assertEqual(bot.plan("evening", day, VOCAB, CFG), bot.plan("evening", day, VOCAB, CFG))

    def test_no_overlapping_answers(self):
        lernen = next(w for w in VOCAB if w.german == "lernen")
        for seed in range(30):
            wrong = bot._distractors(lernen, VOCAB, random.Random(seed), "english")
            self.assertFalse(any("learn" in w for w in wrong), wrong)

    def test_weekend_sends_nothing_except_review(self):
        sat = dt.date(2026, 10, 24)
        self.assertEqual(bot.plan("morning", sat, VOCAB, CFG), ([], []))
        self.assertEqual(bot.plan("evening", sat, VOCAB, CFG), ([], []))
        texts, polls = bot.plan("review", sat, VOCAB, CFG)
        self.assertEqual(len(polls), 10)


class Messages(unittest.TestCase):
    def test_html_escaped(self):
        w = bot.Word("a<b", "x & y", "<i>", "")
        msg = bot.morning_message([w], 0, dt.date(2026, 10, 19), 100)
        self.assertIn("a&lt;b", msg)
        self.assertIn("x &amp; y", msg)

    def test_split_long_message(self):
        texts, _ = bot.plan("morning", dt.date(2026, 10, 19), VOCAB,
                            bot.Config(CFG.start_date, 120, 6, 10))
        chunks = bot.split_message(texts[0])
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= bot.MAX_MESSAGE for c in chunks))
        self.assertEqual("\n\n".join(chunks), texts[0])


class TelegramClient(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(bot.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sends_quiz_with_new_field(self):
        s = FakeSession()
        tg = bot.Telegram("TOKEN", "42", session=s)
        _, polls = bot.plan("evening", dt.date(2026, 10, 19), VOCAB, CFG)
        tg.send_quiz(polls[0])
        method, payload = s.calls[0]
        self.assertEqual(method, "sendPoll")
        self.assertEqual(payload["chat_id"], "42")
        self.assertEqual(payload["type"], "quiz")
        self.assertIn("correct_option_ids", payload)

    def test_falls_back_to_legacy_field(self):
        s = FakeSession([{"ok": False, "description": "Bad Request: wrong option ids"}])
        tg = bot.Telegram("TOKEN", "42", session=s)
        _, polls = bot.plan("evening", dt.date(2026, 10, 19), VOCAB, CFG)
        tg.send_quiz(polls[0])
        self.assertEqual(len(s.calls), 2)
        self.assertIn("correct_option_id", s.calls[1][1])
        self.assertNotIn("correct_option_ids", s.calls[1][1])

    def test_retries_after_rate_limit(self):
        s = FakeSession([{"ok": False, "description": "Too Many Requests",
                          "parameters": {"retry_after": 3}}])
        tg = bot.Telegram("TOKEN", "42", session=s)
        tg.send_text("hallo")
        self.assertEqual(len(s.calls), 2)

    def test_error_never_leaks_token(self):
        class Broken:
            def post(self, *a, **k):
                raise ConnectionError("https://api.telegram.org/botSECRET/sendMessage")
        tg = bot.Telegram("SECRET", "42", session=Broken())
        with self.assertRaises(bot.TelegramError) as ctx:
            tg.call("sendMessage", {}, attempts=2)
        self.assertNotIn("SECRET", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
