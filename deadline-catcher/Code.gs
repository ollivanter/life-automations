/**
 * Deadline Catcher
 * Finds assignment, quiz and exam deadlines in college emails and adds them
 * to a "College Deadlines" Google Calendar with reminders.
 *
 * Setup (once): paste this file into a new project at script.google.com,
 * choose `setup` in the toolbar, press Run and allow the permissions.
 * After that it checks your mail every hour by itself.
 */

// ---------------------------------------------------------------------------
// Settings: edit these
// ---------------------------------------------------------------------------
const CONFIG = {
  // Only emails from these senders are read. Gmail matches partial
  // addresses, so 'pes.edu' also covers 'name@pesu.pes.edu'. Add a
  // professor's Gmail address here if they use one.
  SENDERS: ['pes.edu'],

  // An email must contain at least one of these words to be checked.
  KEYWORDS: ['assignment', 'deadline', 'due', 'submission', 'submit', 'quiz',
             'ISA', 'ESA', 'exam', 'test', 'viva', 'lab', 'project',
             'last date', 'registration', 'hackathon'],

  LOOKBACK_DAYS: 14,                    // how far back each run looks
  CALENDAR_NAME: 'College Deadlines',   // created for you if it doesn't exist

  // Pop-up reminders. daysBefore: 2 at 18:00 = two evenings before, when you're home.
  REMINDERS: [
    { daysBefore: 2, time: '18:00' },
    { daysBefore: 1, time: '18:00' },
  ],

  MAX_EVENTS_PER_EMAIL: 8,   // more dates than this (e.g. a full timetable) → flagged for you instead
  MAX_THREADS_PER_RUN: 40,
  ADDED_LABEL: 'Deadline Catcher/Added',
  REVIEW_LABEL: 'Deadline Catcher/Check manually',
};

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/** Run once: creates the calendar, labels and an hourly trigger. */
function setup() {
  getCalendar_();
  getLabel_(CONFIG.ADDED_LABEL);
  getLabel_(CONFIG.REVIEW_LABEL);
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === 'run'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('run').timeBased().everyHours(1).create();
  Logger.log('Hourly trigger created. Running a first scan now…');
  run();
}

/** Scans recent college emails. The hourly trigger calls this. */
function run() {
  const props = PropertiesService.getScriptProperties();
  const seen = JSON.parse(props.getProperty('seen') || '[]');
  const seenSet = new Set(seen);
  const calendar = getCalendar_();
  const added = getLabel_(CONFIG.ADDED_LABEL);
  const review = getLabel_(CONFIG.REVIEW_LABEL);

  const threads = GmailApp.search(buildQuery(CONFIG), 0, CONFIG.MAX_THREADS_PER_RUN);
  let created = 0, flagged = 0;

  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (message) {
      const id = message.getId();
      if (seenSet.has(id)) return;
      seenSet.add(id);
      seen.push(id);
      if (!isFromCollege(message.getFrom(), CONFIG.SENDERS)) return; // another sender in the thread

      const subject = message.getSubject() || '(no subject)';
      const text = subject + '\n' + message.getPlainBody();
      const result = findDeadlines(text, message.getDate(), CONFIG.MAX_EVENTS_PER_EMAIL);

      if (result.deadlines.length) {
        result.deadlines.forEach(function (d) {
          if (addEvent_(calendar, d, subject, message, thread)) created++;
        });
        thread.addLabel(added);
      } else if (result.needsReview) {
        thread.addLabel(review);
        flagged++;
      }
    });
  });

  // Script properties hold up to 9 KB per value; keep the newest IDs.
  props.setProperty('seen', JSON.stringify(seen.slice(-400)));
  Logger.log('Checked ' + threads.length + ' threads: ' + created + ' events added, ' + flagged + ' flagged for review.');
}

/** Clears the "already seen" list so the next run re-reads recent emails.
 *  Events are never duplicated: each one carries a tag that's checked first. */
function resetSeen() {
  PropertiesService.getScriptProperties().deleteProperty('seen');
  Logger.log('Seen list cleared.');
}

/** Try the date finder on sample text without touching Gmail or Calendar. */
function testParser() {
  const samples = [
    'Assignment 2 for UE25MA141A is due on 24/10/2026 by 11:59 PM.',
    'ISA 1 will be held on 3rd November. Syllabus: units 1 and 2.',
    'Reminder: submit your lab record before Friday.',
    'Last date for hackathon registration: Oct 30, 2026 5 pm',
  ];
  samples.forEach(function (s) {
    const r = findDeadlines(s, new Date(), 8);
    Logger.log(s + '\n  → ' + (r.deadlines.map(function (d) { return d.date + (d.hasTime ? '' : ' (all day)'); }).join(', ') || 'nothing') +
               (r.needsReview ? '  [flag for review]' : ''));
  });
}

// ---------------------------------------------------------------------------
// Gmail & Calendar helpers
// ---------------------------------------------------------------------------
function getCalendar_() {
  const found = CalendarApp.getCalendarsByName(CONFIG.CALENDAR_NAME);
  if (found.length) return found[0];
  const cal = CalendarApp.createCalendar(CONFIG.CALENDAR_NAME);
  Logger.log('Created calendar "' + CONFIG.CALENDAR_NAME + '"');
  return cal;
}

function getLabel_(name) {
  // Create "Parent" before "Parent/Child" so the label nests in Gmail's sidebar.
  const parts = name.split('/');
  let label = null;
  for (let i = 1; i <= parts.length; i++) {
    const path = parts.slice(0, i).join('/');
    label = GmailApp.getUserLabelByName(path) || GmailApp.createLabel(path);
  }
  return label;
}

function addEvent_(calendar, d, subject, message, thread) {
  const tag = '[dc:' + message.getId() + ':' + isoDay(d.date) + ']';
  const day = startOfDay(d.date);
  const already = calendar.getEventsForDay(day).some(function (e) {
    return (e.getDescription() || '').indexOf(tag) !== -1;
  });
  if (already) return false;

  const title = '📌 ' + cleanSubject(subject);
  const description = [
    d.line,
    '',
    'From: ' + message.getFrom(),
    'Email: ' + thread.getPermalink(),
    '',
    'Added by Deadline Catcher ' + tag,
  ].join('\n');

  let event, eventStart;
  if (d.hasTime) {
    eventStart = new Date(d.date.getTime() - 30 * 60 * 1000);
    event = calendar.createEvent(title, eventStart, d.date, { description: description });
  } else {
    eventStart = day;
    event = calendar.createAllDayEvent(title, day, { description: description });
  }
  event.removeAllReminders();
  reminderMinutes(eventStart, day, CONFIG.REMINDERS).forEach(function (m) {
    event.addPopupReminder(m);
  });
  Logger.log('Added: ' + title + ' on ' + d.date);
  return true;
}

// ---------------------------------------------------------------------------
// Pure functions (no Google services) — tested with Node, see test.js
// ---------------------------------------------------------------------------
const MONTHS = {
  jan: 0, january: 0, feb: 1, february: 1, mar: 2, march: 2, apr: 3, april: 3,
  may: 4, jun: 5, june: 5, jul: 6, july: 6, aug: 7, august: 7, sep: 8, sept: 8,
  september: 8, oct: 9, october: 9, nov: 10, november: 10, dec: 11, december: 11,
};
const MONTH_RE = '(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)';
const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

// Words that make a line look like it states a deadline or exam date.
const STRONG_CUES = /\b(due|deadline|submit|submission|last\s+date|on\s+or\s+before|closes?|ends?)\b/i;
const WEAK_CUES = /\b(before|by|exam|isa|esa|quiz|test|viva|scheduled|will\s+be\s+(?:held|conducted)|registration|assignment|lab|project)\b/i;

function buildQuery(config) {
  const from = config.SENDERS.map(function (s) { return 'from:' + s; }).join(' ');
  const words = config.KEYWORDS.map(function (k) { return k.indexOf(' ') !== -1 ? '"' + k + '"' : k; }).join(' ');
  return '{' + from + '} {' + words + '} newer_than:' + config.LOOKBACK_DAYS + 'd';
}

function isFromCollege(fromHeader, senders) {
  const lower = String(fromHeader || '').toLowerCase();
  return senders.some(function (s) { return lower.indexOf(String(s).toLowerCase()) !== -1; });
}

function cleanSubject(subject) {
  let s = String(subject || '').trim();
  while (/^(re|fw|fwd)\s*:\s*/i.test(s)) s = s.replace(/^(re|fw|fwd)\s*:\s*/i, '');
  return s.length > 80 ? s.slice(0, 79) + '…' : (s || 'Deadline');
}

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function isoDay(d) {
  const p = function (n) { return (n < 10 ? '0' : '') + n; };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function validDate(y, m, d) {
  if (m < 0 || m > 11 || d < 1 || d > 31) return null;
  const date = new Date(y, m, d);
  return date.getMonth() === m && date.getDate() === d ? date : null;
}

/** Year for a date written without one: this year, or next year if that's long past. */
function inferYear(m, d, sentAt) {
  const y = sentAt.getFullYear();
  const candidate = validDate(y, m, d);
  if (candidate && candidate < new Date(sentAt.getTime() - 30 * 86400000)) return y + 1;
  return y;
}

function fullYear(y) {
  y = parseInt(y, 10);
  return y < 100 ? 2000 + y : y;
}

/** All date mentions in one line: [{date, start, end}] */
function datesInLine(line, sentAt) {
  const out = [];
  const taken = [];
  function free(s, e) { return taken.every(function (t) { return e <= t[0] || s >= t[1]; }); }
  function add(date, m) {
    const s = m.index, e = m.index + m[0].length;
    if (!date || !free(s, e)) return;
    taken.push([s, e]);
    out.push({ date: date, start: s, end: e });
  }
  let m;

  // 2026-10-24
  const iso = /\b(\d{4})-(\d{1,2})-(\d{1,2})\b/g;
  while ((m = iso.exec(line))) add(validDate(+m[1], +m[2] - 1, +m[3]), m);

  // 24/10/2026, 24-10-26, 24.10.2026 (day first, as written in India)
  const num = /\b(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{2,4})\b/g;
  while ((m = num.exec(line))) {
    let d = +m[1], mo = +m[2];
    if (mo > 12 && d <= 12) { const t = d; d = mo; mo = t; } // clearly month-first
    add(validDate(fullYear(m[3]), mo - 1, d), m);
  }

  // "may" is also a verb ("group 2 may submit"): only trust it capitalised or with a year.
  function verbMay(token, year) { return token === 'may' && !year; }

  // 24th October 2026, 24 Oct, 3rd of November.
  // Not when another day number follows ("Assignment 2 Oct 30" means Oct 30).
  const dm = new RegExp('\\b(\\d{1,2})(?:st|nd|rd|th)?\\s*(?:of\\s+)?' + MONTH_RE +
    '\\.?(?:,?\\s*(\\d{4}))?\\b(?!\\s+\\d{1,2}(?:st|nd|rd|th)?\\b(?!\\s*(?:a\\.?m|p\\.?m|:|\\.\\d)))', 'gi');
  while ((m = dm.exec(line))) {
    if (verbMay(m[2], m[3])) continue;
    const mo = MONTHS[m[2].toLowerCase()];
    const y = m[3] ? +m[3] : inferYear(mo, +m[1], sentAt);
    add(validDate(y, mo, +m[1]), m);
  }

  // October 24th, 2026 / Oct 24
  const md = new RegExp('\\b' + MONTH_RE + '\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?\\b(?:,?\\s*(\\d{4})\\b)?', 'gi');
  while ((m = md.exec(line))) {
    if (verbMay(m[1], m[3])) continue;
    const mo = MONTHS[m[1].toLowerCase()];
    const y = m[3] ? +m[3] : inferYear(mo, +m[2], sentAt);
    add(validDate(y, mo, +m[2]), m);
  }

  // today / tonight / tomorrow
  const rel = /\b(today|tonight|tomorrow)\b/gi;
  while ((m = rel.exec(line))) {
    const base = startOfDay(sentAt);
    if (m[1].toLowerCase() === 'tomorrow') base.setDate(base.getDate() + 1);
    add(base, m);
  }

  // "by Friday", "on Monday", "before next Wednesday" → the next such day after the email.
  // Skipped when the line also has a written date ("on Monday 26-10-26" is that date).
  const explicit = out.length > 0;
  const wd = /\b(by|on|before|this|next|till|until|due)\s+(?:next\s+|this\s+)?(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b/gi;
  while (!explicit && (m = wd.exec(line))) {
    const target = WEEKDAYS.indexOf(m[2].toLowerCase());
    const base = startOfDay(sentAt);
    let diff = (target - base.getDay() + 7) % 7;
    if (diff === 0) diff = 7;
    base.setDate(base.getDate() + diff);
    add(base, m);
  }

  return out.sort(function (a, b) { return a.start - b.start; });
}

/** Times in one line: [{h, min, start, end}] */
function timesInLine(line, dateSpans) {
  const out = [];
  function inDate(s, e) { return dateSpans.some(function (d) { return s < d.end && e > d.start; }); }
  let m;
  const ampm = /\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?=[^a-z]|$)/gi;
  while ((m = ampm.exec(line))) {
    let h = +m[1];
    const min = m[2] ? +m[2] : 0;
    if (h < 1 || h > 12 || min > 59 || inDate(m.index, m.index + m[0].length)) continue;
    const pm = m[3].toLowerCase().charAt(0) === 'p';
    if (pm && h !== 12) h += 12;
    if (!pm && h === 12) h = 0;
    out.push({ h: h, min: min, start: m.index, end: m.index + m[0].length });
  }
  const h24 = /\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*(?:a\.?m|p\.?m))/gi;
  while ((m = h24.exec(line))) {
    const s = m.index, e = s + m[0].length;
    if (inDate(s, e) || out.some(function (t) { return s < t.end && e > t.start; })) continue;
    out.push({ h: +m[1], min: +m[2], start: s, end: e });
  }
  const words = /\b(midnight|noon|midday|tonight)\b/gi;
  while ((m = words.exec(line))) {
    const w = m[1].toLowerCase();
    const t = w === 'midnight' || w === 'tonight' ? { h: 23, min: 59 } : { h: 12, min: 0 };
    t.start = m.index; t.end = m.index + m[0].length;
    out.push(t);
  }
  return out;
}

function splitLines(text) {
  return String(text || '')
    .replace(/\r/g, '')
    .split(/\n|(?<=[.!?])\s+(?=[A-Z])/)
    .map(function (l) { return l.replace(/\s+/g, ' ').trim(); })
    .filter(function (l) { return l.length > 0 && l.length < 400 && !/^>/.test(l); });
}

/**
 * Finds deadlines in an email.
 * Returns {deadlines: [{date, hasTime, line}], needsReview}
 */
function findDeadlines(text, sentAt, maxEvents) {
  const earliest = startOfDay(sentAt);
  const latest = new Date(earliest.getTime() + 366 * 86400000);
  const candidates = [];
  const lines = splitLines(text);
  const subjectHasCue = lines.length > 0 && (STRONG_CUES.test(lines[0]) || WEAK_CUES.test(lines[0]));

  lines.forEach(function (line, index) {
    const dates = datesInLine(line, sentAt);
    if (!dates.length) return;
    const times = timesInLine(line, dates);
    const score = (STRONG_CUES.test(line) ? 3 : 0) + (WEAK_CUES.test(line) ? 2 : 0) + (index === 0 ? 1 : 0);
    dates.forEach(function (d, i) {
      if (d.date < earliest || d.date > latest) return;
      // Pair with the nearest time in the line (dates and times in order if counts match).
      let t = null;
      if (times.length === dates.length) t = times[i];
      else if (times.length) {
        t = times.reduce(function (best, cur) {
          const dist = Math.min(Math.abs(cur.start - d.end), Math.abs(d.start - cur.end));
          const bestDist = best ? Math.min(Math.abs(best.start - d.end), Math.abs(d.start - best.end)) : Infinity;
          return dist < bestDist ? cur : best;
        }, null);
      }
      const date = new Date(d.date.getFullYear(), d.date.getMonth(), d.date.getDate(),
                            t ? t.h : 0, t ? t.min : 0);
      candidates.push({ date: date, hasTime: !!t, line: line, score: score });
    });
  });

  if (!candidates.length) return { deadlines: [], needsReview: false };

  // One event per day: keep the best-scoring mention (prefer ones with a time).
  const byDay = {};
  candidates.forEach(function (c) {
    const key = isoDay(c.date);
    const cur = byDay[key];
    if (!cur || c.score > cur.score || (c.score === cur.score && c.hasTime && !cur.hasTime)) byDay[key] = c;
  });
  const unique = Object.keys(byDay).sort().map(function (k) { return byDay[k]; });

  let picked = unique.filter(function (c) { return c.score >= 2; });
  // A single date in an email whose subject is about an exam/assignment is almost certainly the deadline.
  if (!picked.length && unique.length === 1 && subjectHasCue) picked = unique;
  if (!picked.length) return { deadlines: [], needsReview: true };
  if (picked.length > maxEvents) return { deadlines: [], needsReview: true };
  return {
    deadlines: picked.map(function (c) { return { date: c.date, hasTime: c.hasTime, line: c.line }; }),
    needsReview: false,
  };
}

/** Minutes-before values for popup reminders (Calendar allows 0–40320). */
function reminderMinutes(eventStart, day, reminders) {
  const out = [];
  reminders.forEach(function (r) {
    const parts = String(r.time || '18:00').split(':');
    const at = new Date(day.getFullYear(), day.getMonth(), day.getDate() - r.daysBefore, +parts[0], +(parts[1] || 0));
    const minutes = Math.round((eventStart.getTime() - at.getTime()) / 60000);
    if (minutes > 0 && minutes <= 40320 && out.indexOf(minutes) === -1) out.push(minutes);
  });
  return out.slice(0, 5); // Calendar keeps at most 5 reminders
}

// Lets test.js load this file in Node. Apps Script ignores it.
if (typeof module !== 'undefined') {
  module.exports = {
    CONFIG: CONFIG, buildQuery: buildQuery, isFromCollege: isFromCollege, cleanSubject: cleanSubject,
    datesInLine: datesInLine, timesInLine: timesInLine, splitLines: splitLines,
    findDeadlines: findDeadlines, reminderMinutes: reminderMinutes, isoDay: isoDay,
  };
}
