#!/usr/bin/env python3
"""German vocab bot for the metro commute.

The bot is stateless: which words you get on a given day is worked out from
the date, so it can run on a schedule (GitHub Actions) without a server or
database.

Modes
  morning   send today's words with example sentences
  evening   send Telegram quiz polls on today's words
  review    Saturday quiz on everything from Monday to Friday
  preview   print what would be sent (no Telegram token needed)
  chatid    list the chats that have messaged your bot, to find your chat ID

Environment variables
  TELEGRAM_BOT_TOKEN   from @BotFather (required to send)
  TELEGRAM_CHAT_ID     your chat ID (required to send)
  START_DATE           first lesson day, YYYY-MM-DD (default 2026-10-19)
  WORDS_PER_DAY        default 10
  QUIZ_QUESTIONS       evening quiz length, default 6
  REVIEW_QUESTIONS     Saturday review length, default 10
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
HERE = Path(__file__).resolve().parent
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Telegram limits (Bot API 10.x)
MAX_MESSAGE = 4000          # real limit is 4096; leave headroom for HTML tags
MAX_POLL_QUESTION = 300
MAX_POLL_OPTION = 100
MAX_POLL_EXPLANATION = 200
ARTICLES = ("der ", "die ", "das ")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def _env(name: str, default: str) -> str:
    # GitHub passes unset repository variables as empty strings.
    value = os.getenv(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(_env(name, str(default))))
    except ValueError:
        sys.exit(f"{name} must be a whole number")


@dataclass
class Config:
    start_date: dt.date
    words_per_day: int
    quiz_size: int
    review_size: int

    @classmethod
    def from_env(cls) -> "Config":
        raw = _env("START_DATE", "2026-10-19")
        try:
            start = dt.date.fromisoformat(raw)
        except ValueError:
            sys.exit(f"START_DATE must look like 2026-10-19, got {raw!r}")
        return cls(
            start_date=start,
            words_per_day=_env_int("WORDS_PER_DAY", 10),
            quiz_size=_env_int("QUIZ_QUESTIONS", 6),
            review_size=_env_int("REVIEW_QUESTIONS", 10),
        )


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Word:
    german: str
    english: str
    example_de: str = ""
    example_en: str = ""

    @property
    def kind(self) -> str:
        """Rough part of speech, used to pick believable wrong answers."""
        if self.german.lower().startswith(ARTICLES):
            return "noun"
        if self.english.lower().startswith("to "):
            return "verb"
        return "other"


def load_vocab(path: Path) -> list[Word]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    words = []
    for i, row in enumerate(rows, start=2):
        de = (row.get("german") or "").strip()
        en = (row.get("english") or "").strip()
        if not de or not en:
            print(f"vocab.csv line {i}: skipped (needs german and english)", file=sys.stderr)
            continue
        words.append(Word(de, en, (row.get("example_de") or "").strip(),
                          (row.get("example_en") or "").strip()))
    if len(words) < 4:
        sys.exit("vocab.csv needs at least 4 words so quizzes have wrong answers")
    return words


# --------------------------------------------------------------------------
# Which words on which day
# --------------------------------------------------------------------------
def weekdays_between(start: dt.date, end: dt.date) -> int:
    """Number of Mon-Fri days in [start, end)."""
    if end <= start:
        return 0
    full_weeks, extra = divmod((end - start).days, 7)
    count = full_weeks * 5
    for i in range(extra):
        if (start + dt.timedelta(days=full_weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


def lesson_number(day: dt.date, cfg: Config) -> int:
    """0-based lesson index. Lessons only advance on weekdays."""
    return weekdays_between(cfg.start_date, day)


def words_for_lesson(vocab: list[Word], lesson: int, per_day: int) -> list[Word]:
    n = min(per_day, len(vocab))
    first = (lesson * n) % len(vocab)
    return [vocab[(first + i) % len(vocab)] for i in range(n)]


def week_words(vocab: list[Word], day: dt.date, cfg: Config) -> list[Word]:
    """All words taught Monday-Friday of the week containing `day`."""
    monday = day - dt.timedelta(days=day.weekday())
    seen, out = set(), []
    for offset in range(5):
        d = monday + dt.timedelta(days=offset)
        if d < cfg.start_date:
            continue
        for w in words_for_lesson(vocab, lesson_number(d, cfg), cfg.words_per_day):
            if w.german not in seen:
                seen.add(w.german)
                out.append(w)
    return out


# --------------------------------------------------------------------------
# Message building
# --------------------------------------------------------------------------
def esc(text: str) -> str:
    return html.escape(text, quote=False)


def morning_message(words: list[Word], lesson: int, day: dt.date, vocab_size: int) -> str:
    rnd = (lesson * len(words)) // vocab_size + 1
    head = (f"🇩🇪 <b>Guten Morgen!</b> Lesson {lesson + 1}"
            + (f" (round {rnd})" if rnd > 1 else "")
            + f" · {day:%a %d %b}\n"
            f"Today's {len(words)} words. Quiz on your way home 🚇")
    lines = [head]
    for i, w in enumerate(words, 1):
        block = f"{i}. <b>{esc(w.german)}</b> — {esc(w.english)}"
        if w.example_de:
            block += f"\n    <i>{esc(w.example_de)}</i>"
            if w.example_en:
                block += f"\n    ({esc(w.example_en)})"
        lines.append(block)
    return "\n\n".join(lines)


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on blank lines so no chunk exceeds Telegram's limit."""
    chunks, current = [], ""
    for part in text.split("\n\n"):
        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = part[:limit]
    if current:
        chunks.append(current)
    return chunks


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _keywords(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zäöüß]+", text.lower()) if len(t) > 3}


def _distractors(target: Word, pool: list[Word], rng: random.Random,
                 field: str, k: int = 3) -> list[str]:
    """Pick k wrong answers, preferring the same part of speech and skipping
    answers that overlap with the right one (e.g. 'to learn' vs 'to study')."""
    right = getattr(target, field)
    right_kw = _keywords(right)

    def ok(w: Word) -> bool:
        value = getattr(w, field)
        return (w.german != target.german and value.lower() != right.lower()
                and not (_keywords(value) & right_kw))

    same = [w for w in pool if w.kind == target.kind and ok(w)]
    other = [w for w in pool if w.kind != target.kind and ok(w)]
    rng.shuffle(same)
    rng.shuffle(other)
    picked, seen = [], {right.lower()}
    for w in same + other:
        value = getattr(w, field)
        if value.lower() not in seen:
            seen.add(value.lower())
            picked.append(value)
        if len(picked) == k:
            break
    return picked


def build_quiz(targets: list[Word], pool: list[Word], n: int,
               rng: random.Random) -> list[dict]:
    """Return sendPoll payloads (without chat_id). Alternates German→English
    and English→German questions."""
    chosen = rng.sample(targets, min(n, len(targets)))
    polls = []
    for i, w in enumerate(chosen):
        to_english = i % 2 == 0
        if to_english:
            question = f"What does „{w.german}“ mean?"
            right, wrong = w.english, _distractors(w, pool, rng, "english")
        else:
            question = f"How do you say “{w.english}” in German?"
            right, wrong = w.german, _distractors(w, pool, rng, "german")
        options = [right] + wrong
        rng.shuffle(options)
        explanation = f"{w.german} = {w.english}"
        if w.example_de:
            explanation += f"\n{w.example_de}"
            if w.example_en:
                explanation += f" ({w.example_en})"
        polls.append({
            "question": _clip(question, MAX_POLL_QUESTION),
            "options": [{"text": _clip(o, MAX_POLL_OPTION)} for o in options],
            "type": "quiz",
            "correct_option_ids": [options.index(right)],
            "explanation": _clip(explanation, MAX_POLL_EXPLANATION),
        })
    return polls


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, session=None):
        import requests  # imported here so preview mode needs no packages
        self.token = token
        self.chat_id = chat_id
        self.http = session or requests.Session()

    def call(self, method: str, payload: dict | None = None, attempts: int = 4) -> dict:
        url = TELEGRAM_API.format(token=self.token, method=method)
        for attempt in range(1, attempts + 1):
            try:
                resp = self.http.post(url, json=payload or {}, timeout=30)
                data = resp.json()
            except Exception as exc:  # network error: never print the URL (it has the token)
                if attempt == attempts:
                    raise TelegramError(f"{method}: network error ({type(exc).__name__})") from None
                time.sleep(2 * attempt)
                continue
            if data.get("ok"):
                return data["result"]
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if retry_after and attempt < attempts:
                time.sleep(int(retry_after) + 1)
                continue
            raise TelegramError(f"{method}: {data.get('description', 'unknown error')}")
        raise TelegramError(f"{method}: gave up after {attempts} attempts")

    def send_text(self, text: str) -> None:
        for chunk in split_message(text):
            self.call("sendMessage", {
                "chat_id": self.chat_id, "text": chunk,
                "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
            })
            time.sleep(1)

    def send_quiz(self, poll: dict) -> None:
        payload = {"chat_id": self.chat_id, **poll}
        try:
            self.call("sendPoll", payload)
        except TelegramError as exc:
            # Bot API 9.6 replaced correct_option_id with correct_option_ids.
            # Fall back to the old field in case a server still wants it.
            if "option" not in str(exc).lower():
                raise
            legacy = dict(payload)
            legacy["correct_option_id"] = legacy.pop("correct_option_ids")[0]
            self.call("sendPoll", legacy)
        time.sleep(1.2)  # stay under Telegram's ~1 message/second per chat


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------
def plan(mode: str, day: dt.date, vocab: list[Word], cfg: Config):
    """Return (text messages, polls) for a mode. Pure, so it is easy to test."""
    rng = random.Random(f"{day.isoformat()}-{mode}")
    if mode in ("morning", "evening") and day.weekday() >= 5:
        return [], []
    if mode == "morning":
        lesson = lesson_number(day, cfg)
        words = words_for_lesson(vocab, lesson, cfg.words_per_day)
        return [morning_message(words, lesson, day, len(vocab))], []
    if mode == "evening":
        lesson = lesson_number(day, cfg)
        words = words_for_lesson(vocab, lesson, cfg.words_per_day)
        polls = build_quiz(words, vocab, cfg.quiz_size, rng)
        intro = f"🧠 <b>Quiz time!</b> {len(polls)} questions on today's words. Viel Erfolg!"
        return [intro], polls
    if mode == "review":
        words = week_words(vocab, day, cfg)
        if not words:
            return [], []
        polls = build_quiz(words, vocab, cfg.review_size, rng)
        intro = (f"📚 <b>Weekly review</b> · {len(words)} words this week, "
                 f"{len(polls)} questions. Let's see what stuck!")
        return [intro], polls
    raise ValueError(mode)


def print_preview(texts: list[str], polls: list[dict]) -> None:
    for t in texts:
        print(re.sub(r"</?[bi]>", "", html.unescape(t)))
        print()
    for i, p in enumerate(polls, 1):
        right = p["correct_option_ids"][0]
        print(f"Q{i}. {p['question']}")
        for j, o in enumerate(p["options"]):
            print(f"    {'✔' if j == right else ' '} {o['text']}")
    if not texts and not polls:
        print("(nothing to send on this day)")


def show_chat_ids(token: str) -> None:
    tg = Telegram(token, chat_id="")
    updates = tg.call("getUpdates", {"limit": 100})
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat
    if not chats:
        print("No messages found. Send any message to your bot in Telegram, then run this again.")
        return
    for cid, chat in chats.items():
        name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        print(f"{cid}\t{chat.get('type')}\t{name}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["morning", "evening", "review", "preview", "chatid"])
    p.add_argument("--date", help="pretend today is YYYY-MM-DD (for testing)")
    p.add_argument("--preview-mode", default="morning", choices=["morning", "evening", "review"],
                   help="what to preview (with mode=preview)")
    p.add_argument("--vocab", default=str(HERE / "vocab.csv"))
    p.add_argument("--force", action="store_true",
                   help="on weekends, send Friday's lesson instead of nothing (for manual tests)")
    args = p.parse_args(argv)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if args.mode == "chatid":
        if not token:
            sys.exit("Set TELEGRAM_BOT_TOKEN first")
        show_chat_ids(token)
        return 0

    cfg = Config.from_env()
    vocab = load_vocab(Path(args.vocab))
    day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(IST).date()
    if args.force and args.mode in ("morning", "evening") and day.weekday() >= 5:
        day -= dt.timedelta(days=day.weekday() - 4)  # use Friday's lesson

    if args.mode == "preview":
        print(f"Preview of '{args.preview_mode}' for {day:%a %d %b %Y}\n")
        print_preview(*plan(args.preview_mode, day, vocab, cfg))
        return 0

    texts, polls = plan(args.mode, day, vocab, cfg)
    if not texts and not polls:
        print(f"Nothing to send for {args.mode} on {day:%A}.")
        return 0

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (see README)")
    tg = Telegram(token, chat_id)
    try:
        for t in texts:
            tg.send_text(t)
        for poll in polls:
            tg.send_quiz(poll)
    except TelegramError as exc:
        sys.exit(f"Telegram error: {exc}")
    print(f"Sent {args.mode}: {len(texts)} message(s), {len(polls)} quiz question(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
