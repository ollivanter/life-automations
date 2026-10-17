// Offline tests for the date finder. Run: TZ=Asia/Kolkata node test.js
const assert = require('assert');
const path = require('path');
const dc = require(path.join(__dirname, 'Code.gs'));

const SENT = new Date(2026, 9, 17, 10, 0); // Sat 17 Oct 2026, 10:00
let passed = 0;

function fmt(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${dc.isoDay(d.date)}${d.hasTime ? ` ${p(d.date.getHours())}:${p(d.date.getMinutes())}` : ''}`;
}

function expect(text, wanted, opts = {}) {
  const r = dc.findDeadlines(text, opts.sent || SENT, 8);
  const got = r.deadlines.map(fmt);
  try {
    assert.deepStrictEqual(got, wanted);
    if ('review' in opts) assert.strictEqual(r.needsReview, opts.review);
    passed++;
  } catch (e) {
    console.error(`FAIL: ${JSON.stringify(text)}\n  wanted ${JSON.stringify(wanted)} review=${opts.review}\n  got    ${JSON.stringify(got)} review=${r.needsReview}`);
    process.exitCode = 1;
  }
}

// Formats
expect('Assignment 2 for UE25MA141A\nAssignment 2 is due on 24/10/2026 by 11:59 PM.', ['2026-10-24 23:59']);
expect('ISA 1\nISA 1 will be held on 3rd November. Syllabus: units 1 and 2.', ['2026-11-03']);
expect('Lab record\nReminder: submit your lab record before Friday.', ['2026-10-23']);
expect('Hackathon\nLast date for hackathon registration: Oct 30, 2026 5 pm', ['2026-10-30 17:00']);
expect('Deadline 2026-11-05 for the project report', ['2026-11-05']);
expect('Quiz\nSubmit by tomorrow midnight', ['2026-10-18 23:59']);
expect('Viva\nViva on 28.10.2026 at 10.30 am in lab 4', ['2026-10-28 10:30']);
expect('Due 10/24/2026 (month first)', ['2026-10-24']);
expect('Physics\nThe deadline is 24th of October, 2026 at 14:00.', ['2026-10-24 14:00']);
expect('Mechanics\nTest on Monday 26-10-26 9:00', ['2026-10-26 09:00']);

// Tricky wording
expect('Assignment 2 Oct 30 submission', ['2026-10-30']);
expect('Lab 3 due 21 Oct. Lab 4 due 28 Oct.', ['2026-10-21', '2026-10-28']);
expect('Notes\nGroup 2 may submit late if needed. Room 5 may change.', [], { review: false });
expect('Exam schedule\nExam on 5th January', ['2027-01-05'], { sent: new Date(2026, 11, 20, 9, 0) });
expect('Grades\nThe quiz on 10/10/2026 has been graded.', [], { review: false });
expect('Assignment\n> due 25/10/2026\nSee quoted text above', [], { review: false });
expect('Dr. Rao says the assignment is due 29 Oct.', ['2026-10-29']);
expect('Assignment 3 is due tonight', ['2026-10-17 23:59']);

// Single date, only trusted when the subject is about a deadline
expect('ISA 2 seating plan\nPlease check the hall on 22 Oct.', ['2026-10-22']);
expect('Club newsletter\nMeet us on 22 Oct.', [], { review: true });

// Big timetables get flagged instead of flooding the calendar
const timetable = 'ESA timetable\n' + Array.from({ length: 10 }, (_, i) =>
  `Exam ${i + 1}: ${i + 2} December 2026`).join('\n');
expect(timetable, [], { review: true });

// Reminders
const day = new Date(2026, 9, 24);
assert.deepStrictEqual(dc.reminderMinutes(day, day, dc.CONFIG.REMINDERS), [30 * 60, 6 * 60]);
const start = new Date(2026, 9, 24, 23, 29);
assert.deepStrictEqual(dc.reminderMinutes(start, day, dc.CONFIG.REMINDERS), [53 * 60 + 29, 29 * 60 + 29]);
assert.deepStrictEqual(dc.reminderMinutes(new Date(2026, 9, 24, 9), day, [{ daysBefore: 60, time: '18:00' }]), []);
passed += 3;

// Helpers
assert.strictEqual(dc.cleanSubject('Re: Fwd: RE: Assignment 2'), 'Assignment 2');
assert.ok(dc.isFromCollege('"Prof X" <x@pesu.pes.edu>', ['pes.edu']));
assert.ok(!dc.isFromCollege('spam@example.com', ['pes.edu']));
assert.strictEqual(dc.buildQuery({ SENDERS: ['pes.edu', 'a@b.com'], KEYWORDS: ['due', 'last date'], LOOKBACK_DAYS: 7 }),
  '{from:pes.edu from:a@b.com} {due "last date"} newer_than:7d');
passed += 4;

// Glue code: run setup() and run() against fake Gmail/Calendar services.
(function glue() {
  const fs = require('fs');
  const vm = require('vm');
  const events = [], labels = {}, triggers = [], props = {};
  const msg = (id, from, subject, body) => ({
    getId: () => id, getFrom: () => from, getSubject: () => subject,
    getPlainBody: () => body, getDate: () => SENT,
  });
  const thread = {
    labels: [],
    getMessages: () => [
      msg('m1', 'Prof <p@pes.edu>', 'Assignment 4', 'Assignment 4 is due on 25/10/2026 by 11:59 PM.'),
      msg('m2', 'friend@gmail.com', 'Re: Assignment 4', 'Is it due 26/10/2026?'),
      msg('m3', 'Office <o@pes.edu>', 'Fest', 'The fest is on 1 Nov and 2 Nov and 3 Nov.'),
    ],
    addLabel(l) { this.labels.push(l.name); },
    getPermalink: () => 'https://mail.google.com/mail/#all/t1',
  };
  const calendar = {
    getEventsForDay: (d) => events.filter((e) => dc.isoDay(e.start) === dc.isoDay(d)),
    createEvent: (title, start, end, o) => mkEvent(title, start, o, false),
    createAllDayEvent: (title, day, o) => mkEvent(title, day, o, true),
  };
  function mkEvent(title, start, o, allDay) {
    const e = { title, start, allDay, reminders: [], getDescription: () => o.description,
      removeAllReminders() { this.reminders = []; }, addPopupReminder(m) { this.reminders.push(m); } };
    events.push(e);
    return e;
  }
  const ctx = {
    console, Set, JSON, Math, Date,
    Logger: { log: () => {} },
    GmailApp: {
      search: (q) => { ctx.lastQuery = q; return [thread]; },
      getUserLabelByName: (n) => labels[n] || null,
      createLabel: (n) => (labels[n] = { name: n }),
    },
    CalendarApp: { getCalendarsByName: () => [], createCalendar: () => calendar },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: (k) => props[k] || null, setProperty: (k, v) => { props[k] = v; },
      deleteProperty: (k) => { delete props[k]; } }) },
    ScriptApp: {
      getProjectTriggers: () => triggers, deleteTrigger: () => {},
      newTrigger: (fn) => ({ timeBased: () => ({ everyHours: (h) => ({ create: () => triggers.push({ fn, h }) }) }) }),
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8'), ctx);
  vm.runInContext('setup(); run();', ctx); // second run must not duplicate anything
  assert.deepStrictEqual(Object.keys(labels).sort(),
    ['Deadline Catcher', 'Deadline Catcher/Added', 'Deadline Catcher/Check manually']);
  assert.strictEqual(triggers.length, 1);
  assert.strictEqual(events.length, 1, events.map((e) => e.title).join(', '));
  assert.strictEqual(events[0].title, '📌 Assignment 4');
  assert.ok(events[0].reminders.length === 2);
  assert.ok(thread.labels.includes('Deadline Catcher/Added'));
  assert.ok(thread.labels.includes('Deadline Catcher/Check manually')); // the fest email has no deadline cue
  assert.ok(JSON.parse(props.seen).length === 3);
  vm.runInContext('resetSeen(); run();', ctx); // re-reading after a reset still doesn't duplicate
  assert.strictEqual(events.length, 1);
  passed += 1;
})();

console.log(process.exitCode ? 'Some tests failed' : `All ${passed} tests passed`);
