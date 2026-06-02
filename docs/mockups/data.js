/* ============================================================================
 * Shared data for the Camp Planner timeline prototypes.
 *
 * Parsed from the REAL ŠMF summer-camp Google Calendar export (the full 2-week
 * camp, So 4. 7. – Ne 19. 7. 2026). Both mock-vis-timeline.html and
 * mock-gridstack.html consume THIS file, so we compare the two libraries on
 * identical, realistic data.
 *
 * NOTE: Google Calendar's iCal export carries no per-event colour/category, so
 * `cat` here is INFERRED by keyword (meals/lectures/exercise/org recognised by
 * name; everything else defaults to `game`). Times stored as UTC in the export
 * were converted to Europe/Prague (UTC+2, July DST); the one all-day event was
 * dropped (per the no-allDay decision).
 *
 * The new app wants ROW-PER-DAY / horizontal-time. Each program is stored as a
 * flat row with a full start/end DATETIME (DB-style); `buildSlots()` converts those
 * to absolute minutes from CAMP_START and each renderer lays the days out as stacked
 * rows with time running left→right.
 * ==========================================================================*/

// Camp day window: full 24 h, 05:00 → 05:00 next day. Days tile with NO gap, so the
// whole night (incl. 03:00–05:00) is always shown at the right end of each day row.
const WINDOW_START_MIN = 5 * 60;   // 05:00
const WINDOW_END_MIN   = 29 * 60;  // 05:00 next day (05:00 + 24 h)
const SLOT_MIN         = 15;       // 15-min grid resolution (GridStack columns)
const SLOT_COUNT       = (WINDOW_END_MIN - WINDOW_START_MIN) / SLOT_MIN; // 96
const DAY_MIN          = 24 * 60;  // 1440

// Category → Google-Calendar-like colors (mirrors what we use today).
const CATEGORIES = {
  exercise: { label: 'Rozcvička',     color: '#e67c73', text: '#fff' }, // flamingo
  meal:     { label: 'Jídlo',         color: '#4285f4', text: '#fff' }, // blueberry
  lecture:  { label: 'Přednáška',     color: '#f6bf26', text: '#3c3c3c' }, // banana
  game:     { label: 'Hra',           color: '#0b8043', text: '#fff' }, // basil
  biggame:  { label: 'Velká hra',     color: '#7986cb', text: '#fff' }, // lavender
  trip:     { label: 'Výlet / blok',  color: '#33b679', text: '#fff' }, // sage
  org:      { label: 'Orgování',      color: '#f4511e', text: '#fff' }, // tangerine
};

// Day 0 of the camp = midnight of this date; absolute minutes are measured from here.
const CAMP_START = '2026-07-04';

// The 16 camp days (one timeline ROW each), 4. 7. – 19. 7. 2026.
const DAYS = [
  { wd: 'So', date: '4. 7.' },
  { wd: 'Ne', date: '5. 7.' },
  { wd: 'Po', date: '6. 7.' },
  { wd: 'Út', date: '7. 7.' },
  { wd: 'St', date: '8. 7.' },
  { wd: 'Čt', date: '9. 7.' },
  { wd: 'Pá', date: '10. 7.' },
  { wd: 'So', date: '11. 7.' },
  { wd: 'Ne', date: '12. 7.' },
  { wd: 'Po', date: '13. 7.' },
  { wd: 'Út', date: '14. 7.' },
  { wd: 'St', date: '15. 7.' },
  { wd: 'Čt', date: '16. 7.' },
  { wd: 'Pá', date: '17. 7.' },
  { wd: 'So', date: '18. 7.' },
  { wd: 'Ne', date: '19. 7.' },
];

// Every program as a flat row with a full start/end DATETIME (local, Europe/Prague),
// exactly how a DB table would store it. Parsed from the real ŠMF calendar export;
// `cat` is inferred by keyword (iCal carries no colour). Optional per-event
// `prep`/`cleanup: { start, end }` ISO datetimes add independent prep/cleanup slots.
const EVENTS = [
  { t: 'Seznamky', start: '2026-07-04T14:30', end: '2026-07-04T18:00', cat: 'game', emoji: '🫱🫲🤝', orgs: 'Á+L' },
  { t: 'S paní v lese', start: '2026-07-04T18:00', end: '2026-07-05T08:00', cat: 'trip', emoji: '🏞️🌲🌜😴', orgs: 'O,V' },
  { t: 'Přednáška', start: '2026-07-05T08:30', end: '2026-07-05T10:15', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Ptáčata', start: '2026-07-05T10:30', end: '2026-07-05T12:30', cat: 'game', emoji: '🏃‍♀️🏃‍♂️‍➡️🦆🐓🦃🦅🕊️🦢🦜🦩🐦‍🔥🪿🐦‍⬛🦚🦉🦤🐦🐧🐥', orgs: 'O' },
  { t: 'Obíííídek', start: '2026-07-05T12:30', end: '2026-07-05T14:15', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Přednáška', start: '2026-07-05T14:30', end: '2026-07-05T16:15', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Stratego', start: '2026-07-05T16:30', end: '2026-07-05T19:15', cat: 'game', emoji: '🤔👉👎☠️👍👌', orgs: 'K,M' },
  { t: 'Vééééča', start: '2026-07-05T19:30', end: '2026-07-05T20:45', cat: 'meal', emoji: '', orgs: '' },
  { t: 'Banáni', start: '2026-07-05T22:00', end: '2026-07-06T02:30', cat: 'game', emoji: '🧄🧅🥕🍌🏃‍♀️‍➡️🏃‍♂️‍➡️🧑‍🤝‍🧑', orgs: 'K,B,O' },
  { t: 'Fyzcvička', start: '2026-07-06T08:00', end: '2026-07-06T08:30', cat: 'exercise', emoji: '', orgs: '' },
  { t: 'Snída', start: '2026-07-06T08:30', end: '2026-07-06T09:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Přednáška', start: '2026-07-06T09:00', end: '2026-07-06T10:45', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Život', start: '2026-07-06T10:45', end: '2026-07-06T14:00', cat: 'biggame', emoji: '👩‍🍼👶👧👩‍🎓🧑‍🦱👨‍💻💑👨‍👩‍👧‍👦👴☠️', orgs: 'H,L,V' },
  { t: 'Obíííídek', start: '2026-07-06T14:00', end: '2026-07-06T15:45', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Přednáška', start: '2026-07-06T15:45', end: '2026-07-06T17:30', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Divadla', start: '2026-07-06T17:30', end: '2026-07-06T20:30', cat: 'biggame', emoji: '🏃‍♂️‍➡️🏃‍♀️‍➡️📑👑👗🦺🪭🎭', orgs: 'Á,M,B' },
  { t: 'Vééééča', start: '2026-07-06T20:30', end: '2026-07-06T21:45', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-06T21:00', end: '2026-07-06T21:30', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Začátek trifidů', start: '2026-07-06T22:00', end: '2026-07-06T23:00', cat: 'biggame', emoji: '🧣🕶️', orgs: 'V,O,L' },
  { t: 'Trifidí dopoledne', start: '2026-07-07T08:30', end: '2026-07-07T12:30', cat: 'biggame', emoji: '👩‍🦯‍➡️👨‍🦯‍➡️🧗‍♀️🪂🏞️🏃‍♂️‍➡️🔪🤾‍♀️', orgs: '' },
  { t: 'Snída', start: '2026-07-07T09:00', end: '2026-07-07T09:30', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Obíííídek', start: '2026-07-07T12:30', end: '2026-07-07T14:15', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Trifidí odpo', start: '2026-07-07T14:15', end: '2026-07-07T17:15', cat: 'biggame', emoji: '👃👂😛💻📖', orgs: '' },
  { t: 'Přednáška', start: '2026-07-07T17:15', end: '2026-07-07T19:00', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Trifidi zakončení', start: '2026-07-07T20:15', end: '2026-07-07T21:45', cat: 'biggame', emoji: '📖🐉🧌🧞‍♀️😴🛏️', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-07T22:30', end: '2026-07-07T23:00', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Snída', start: '2026-07-08T08:00', end: '2026-07-08T08:30', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Trifidí reflexe', start: '2026-07-08T08:00', end: '2026-07-08T09:00', cat: 'org', emoji: '', orgs: 'V,O,L' },
  { t: 'Anotovat výlety', start: '2026-07-08T09:00', end: '2026-07-08T09:45', cat: 'org', emoji: '', orgs: 'H,J,M' },
  { t: 'Přednáška', start: '2026-07-08T09:00', end: '2026-07-08T10:45', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Barvičky', start: '2026-07-08T10:45', end: '2026-07-08T12:15', cat: 'game', emoji: '🏃‍♂️‍➡️🏃‍♀️‍➡️🔫🟥🟨🟩🟦🟪', orgs: 'J,M' },
  { t: 'Obíííídek', start: '2026-07-08T12:30', end: '2026-07-08T14:15', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-08T13:15', end: '2026-07-08T13:45', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Obchodka', start: '2026-07-08T14:15', end: '2026-07-08T16:45', cat: 'game', emoji: '🤝💰💴💵💶💷💳🪙', orgs: 'V,J,K' },
  { t: 'Přednáška', start: '2026-07-08T16:45', end: '2026-07-08T18:30', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Empatická +Ohýnek večeře', start: '2026-07-08T18:30', end: '2026-07-08T23:00', cat: 'meal', emoji: '🧑‍🤝‍🧑🤔🤝🧀🌭🔥🎸🎶', orgs: 'L,Á' },
  { t: 'Snída', start: '2026-07-09T08:30', end: '2026-07-09T09:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Výlet', start: '2026-07-09T09:00', end: '2026-07-09T18:00', cat: 'trip', emoji: '🚶‍♀️‍➡️🚶‍♂️‍➡️🧭🗺️⛰️🛤️🏞️🌄🧺', orgs: 'H,J,M' },
  { t: 'Vééééča', start: '2026-07-09T18:15', end: '2026-07-09T19:30', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-09T18:45', end: '2026-07-09T19:15', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Vědomostní kvíz', start: '2026-07-09T19:30', end: '2026-07-09T22:00', cat: 'biggame', emoji: '🤔🔬🎥📚📐🌌🏛️❔❕', orgs: 'Á,L,M' },
  { t: 'Přednáška', start: '2026-07-10T08:00', end: '2026-07-10T09:45', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Tvořko', start: '2026-07-10T09:45', end: '2026-07-10T12:15', cat: 'biggame', emoji: '🪡🧶🛠️🖌️🖍️✒️', orgs: 'H,L' },
  { t: 'Obíííídek', start: '2026-07-10T12:15', end: '2026-07-10T14:00', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'NLH +Elementi', start: '2026-07-10T14:00', end: '2026-07-10T17:30', cat: 'trip', emoji: '🧰🔥💧🪵🪨➡️🤔🎲🃏', orgs: 'Á +L,J,B' },
  { t: 'Přednáška', start: '2026-07-10T17:30', end: '2026-07-10T19:15', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Vééééča', start: '2026-07-10T19:15', end: '2026-07-10T20:30', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-10T19:45', end: '2026-07-10T20:15', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Spink před moc', start: '2026-07-10T22:00', end: '2026-07-10T23:15', cat: 'org', emoji: '😴💤', orgs: '' },
  { t: 'MocV1', start: '2026-07-11T00:00', end: '2026-07-12T00:00', cat: 'biggame', emoji: '🕰️🏃‍♀️‍➡️', orgs: '' },
  { t: 'Fukční', start: '2026-07-11T08:00', end: '2026-07-11T10:15', cat: 'game', emoji: '🏃‍♀️‍➡️🏃‍♂️‍➡️📏📐📈📉', orgs: 'H,P' },
  { t: 'Přednáška', start: '2026-07-11T10:15', end: '2026-07-11T12:00', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Přednáška', start: '2026-07-11T13:45', end: '2026-07-11T15:30', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Salónky', start: '2026-07-11T15:30', end: '2026-07-11T19:00', cat: 'game', emoji: '🚪🧮🤔➕➖✖️➗', orgs: 'J,K' },
  { t: 'Plížící', start: '2026-07-11T21:00', end: '2026-07-11T23:15', cat: 'game', emoji: '🤫🙈🔍🔦🌲🌙', orgs: 'K,J' },
  { t: 'Fyzcvička', start: '2026-07-12T08:00', end: '2026-07-12T08:30', cat: 'exercise', emoji: '', orgs: '' },
  { t: 'Snída', start: '2026-07-12T08:30', end: '2026-07-12T09:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Přednáška', start: '2026-07-12T09:00', end: '2026-07-12T10:45', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Roboti vymýšlení', start: '2026-07-12T10:45', end: '2026-07-12T12:45', cat: 'lecture', emoji: '🤖🤔👅', orgs: 'J,B' },
  { t: 'Roboti 1. předvádění', start: '2026-07-12T12:45', end: '2026-07-12T13:15', cat: 'org', emoji: '🤖💃🕺📸', orgs: 'J,B' },
  { t: 'Obíííídek', start: '2026-07-12T13:15', end: '2026-07-12T15:00', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Roboti předvádění', start: '2026-07-12T15:00', end: '2026-07-12T17:00', cat: 'org', emoji: '🤖📋👩‍💼👨‍💼', orgs: 'J,B' },
  { t: 'Šifrovačka', start: '2026-07-12T17:30', end: '2026-07-13T04:00', cat: 'game', emoji: '📝🤔🔍🔦🚶‍♀️‍➡️🚶‍♂️‍➡️🎒', orgs: 'Á,K,P' },
  { t: 'Fyzcvička', start: '2026-07-13T09:00', end: '2026-07-13T09:30', cat: 'exercise', emoji: '', orgs: '' },
  { t: 'Snída', start: '2026-07-13T09:30', end: '2026-07-13T10:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Přednáška', start: '2026-07-13T10:00', end: '2026-07-13T11:45', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Směnkovací', start: '2026-07-13T11:45', end: '2026-07-13T14:00', cat: 'game', emoji: '🤸‍♀️💪🤝💵', orgs: 'V,H,M' },
  { t: 'Obíííídek', start: '2026-07-13T14:15', end: '2026-07-13T16:00', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Edge', start: '2026-07-13T16:00', end: '2026-07-13T18:45', cat: 'game', emoji: '💀➡️🔄️🎼💣🏀', orgs: 'V,J,P' },
  { t: 'Přednáška', start: '2026-07-13T20:15', end: '2026-07-13T22:00', cat: 'lecture', emoji: '👩‍🏫👩‍🎓📐📒', orgs: '' },
  { t: 'Noc, kdy se spí', start: '2026-07-13T22:00', end: '2026-07-14T07:00', cat: 'game', emoji: '😴💤🌑🌒🌓🌔🌕🌖🌗🌘🛏️', orgs: 'L' },
  { t: 'Fyzcvička', start: '2026-07-14T07:00', end: '2026-07-14T07:30', cat: 'exercise', emoji: '', orgs: '' },
  { t: 'Snída', start: '2026-07-14T07:30', end: '2026-07-14T08:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Náboj', start: '2026-07-14T08:00', end: '2026-07-14T12:15', cat: 'game', emoji: '💻📝📐📈🧮☢️♾️🚀👩‍🎓👨‍🎓', orgs: 'P,K,Á' },
  { t: 'Obíííídek', start: '2026-07-14T12:30', end: '2026-07-14T14:15', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-14T13:15', end: '2026-07-14T13:45', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Grandswang', start: '2026-07-14T14:30', end: '2026-07-15T08:00', cat: 'biggame', emoji: '', orgs: 'O,B' },
  { t: 'GP', start: '2026-07-14T16:30', end: '2026-07-14T18:15', cat: 'game', emoji: '', orgs: 'P,O' },
  { t: 'Vééééča', start: '2026-07-14T18:15', end: '2026-07-14T19:00', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Grafohra', start: '2026-07-14T19:00', end: '2026-07-14T22:00', cat: 'game', emoji: '', orgs: 'K,L,P' },
  { t: 'Večeře 2.0', start: '2026-07-14T22:00', end: '2026-07-14T22:45', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Přednáška', start: '2026-07-14T23:00', end: '2026-07-15T00:30', cat: 'lecture', emoji: '', orgs: '' },
  { t: 'Swang', start: '2026-07-15T00:30', end: '2026-07-15T03:30', cat: 'biggame', emoji: '', orgs: 'O,J' },
  { t: 'Fyzcvička', start: '2026-07-15T10:00', end: '2026-07-15T10:30', cat: 'exercise', emoji: '', orgs: '' },
  { t: 'Snída', start: '2026-07-15T10:30', end: '2026-07-15T11:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Mamuti', start: '2026-07-15T11:00', end: '2026-07-15T13:30', cat: 'biggame', emoji: '', orgs: 'L,J,V' },
  { t: 'Mamutí oběd', start: '2026-07-15T13:30', end: '2026-07-15T16:30', cat: 'meal', emoji: '', orgs: 'L,J,V' },
  { t: 'Přednáška', start: '2026-07-15T16:30', end: '2026-07-15T18:15', cat: 'lecture', emoji: '', orgs: '' },
  { t: 'Vééééča', start: '2026-07-15T18:30', end: '2026-07-15T19:45', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-15T19:00', end: '2026-07-15T19:30', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Příprava kačera', start: '2026-07-15T19:30', end: '2026-07-15T20:00', cat: 'org', emoji: '', orgs: '' },
  { t: 'Kačer', start: '2026-07-15T20:00', end: '2026-07-16T01:00', cat: 'biggame', emoji: '', orgs: 'H (Á,B,L)' },
  { t: 'Přednáška', start: '2026-07-16T08:00', end: '2026-07-16T09:45', cat: 'lecture', emoji: '', orgs: '' },
  { t: 'Byrokracie', start: '2026-07-16T09:45', end: '2026-07-16T13:00', cat: 'org', emoji: '', orgs: 'V,B,(K,J)' },
  { t: 'Obíííídek', start: '2026-07-16T13:00', end: '2026-07-16T14:45', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Přednáška', start: '2026-07-16T14:45', end: '2026-07-16T16:30', cat: 'lecture', emoji: '', orgs: '' },
  { t: 'Hra na velkém území + véča', start: '2026-07-16T16:30', end: '2026-07-16T22:45', cat: 'biggame', emoji: '', orgs: 'H,B,P' },
  { t: 'Vééééča (pro orgy)', start: '2026-07-16T19:00', end: '2026-07-16T20:15', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'OVčko', start: '2026-07-16T21:00', end: '2026-07-16T23:00', cat: 'game', emoji: '', orgs: 'M' },
  { t: 'Přednáška', start: '2026-07-17T08:00', end: '2026-07-17T09:45', cat: 'lecture', emoji: '', orgs: '' },
  { t: 'Jezero', start: '2026-07-17T09:45', end: '2026-07-17T12:45', cat: 'trip', emoji: '', orgs: 'J,B,V' },
  { t: 'Obíííídek', start: '2026-07-17T12:45', end: '2026-07-17T14:30', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Jezero reflexe', start: '2026-07-17T14:30', end: '2026-07-17T15:30', cat: 'org', emoji: '', orgs: '' },
  { t: 'Fiodpo', start: '2026-07-17T15:30', end: '2026-07-17T19:45', cat: 'game', emoji: '', orgs: 'V,H,K,P' },
  { t: 'Vééééča', start: '2026-07-17T19:45', end: '2026-07-17T21:00', cat: 'meal', emoji: '🥗🌮', orgs: '' },
  { t: 'Orgokokodák', start: '2026-07-17T20:15', end: '2026-07-17T20:45', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Gamebook', start: '2026-07-17T21:30', end: '2026-07-18T00:30', cat: 'biggame', emoji: '', orgs: 'H,J,Á,V-rit' },
  { t: 'Fyzcvička', start: '2026-07-18T09:00', end: '2026-07-18T09:30', cat: 'exercise', emoji: '', orgs: '' },
  { t: 'Snída', start: '2026-07-18T09:30', end: '2026-07-18T10:00', cat: 'meal', emoji: '🍳🥓🥞', orgs: '' },
  { t: 'Přednáška', start: '2026-07-18T10:00', end: '2026-07-18T11:45', cat: 'lecture', emoji: '', orgs: '' },
  { t: 'Obíííídek', start: '2026-07-18T12:00', end: '2026-07-18T13:00', cat: 'meal', emoji: '🍗🥩🍛🥘', orgs: '' },
  { t: 'Vzpomínkovka', start: '2026-07-18T13:00', end: '2026-07-18T17:00', cat: 'game', emoji: '', orgs: 'J,O' },
  { t: 'Orgokokodák', start: '2026-07-18T17:15', end: '2026-07-18T18:00', cat: 'org', emoji: '🛋️🕰️🎉🧹', orgs: '' },
  { t: 'Žraut', start: '2026-07-18T18:00', end: '2026-07-19T08:00', cat: 'biggame', emoji: '', orgs: 'L,M' },
  { t: 'Úklid', start: '2026-07-19T08:00', end: '2026-07-19T14:00', cat: 'org', emoji: '', orgs: 'K,P' },
];


// ---- helpers ---------------------------------------------------------------

const pad = (n) => String(n).padStart(2, '0');

// Format minutes-from-window-start back to "HH:MM".
function fmt(min) {
  const total = ((WINDOW_START_MIN + min) % DAY_MIN + DAY_MIN) % DAY_MIN;
  return pad(Math.floor(total / 60)) + ':' + pad(total % 60);
}

// Format an ABSOLUTE minute (day*1440 + clock) as clock "HH:MM".
function fmtClock(absMin) {
  const t = ((Math.round(absMin) % DAY_MIN) + DAY_MIN) % DAY_MIN;
  return pad(Math.floor(t / 60)) + ':' + pad(t % 60);
}

// Absolute minutes from CAMP_START midnight (= day-0 row, time 00:00). The day count
// is done in UTC so it is immune to local DST; the clock part is then added verbatim.
const CAMP_START_DAY_UTC = (() => {
  const [Y, Mo, D] = CAMP_START.split('-').map(Number);
  return Date.UTC(Y, Mo - 1, D);
})();
function absMin(iso) {                        // 'YYYY-MM-DDTHH:MM' (local, naive)
  const [d, t] = iso.split('T');
  const [Y, Mo, D] = d.split('-').map(Number);
  const [h, m] = t.split(':').map(Number);
  const day = Math.round((Date.UTC(Y, Mo - 1, D) - CAMP_START_DAY_UTC) / 86400000);
  return day * DAY_MIN + h * 60 + m;
}

// Turn each EVENT row (full start/end datetimes) into independently-placeable SLOTS.
// Every event produces a `main` slot and, optionally, `prep` / `cleanup` slots (also
// stored as full { start, end } datetimes) that are NOT glued to the main — each has
// its own absolute start/end and can live anywhere. All slots of one event share
// `actId` (for org assignment / audit grouping). The DB would store these datetimes
// verbatim; the renderers only ever see the derived absolute minutes.
// Slot: {id, actId, role:'main'|'prep'|'cleanup', sAbs, eAbs, title, cat, emoji, orgs}
function buildSlots() {
  const slots = [];
  let actId = 0;
  EVENTS.forEach((p) => {
    const base = { actId, title: p.t, cat: p.cat, emoji: p.emoji || '', orgs: p.orgs || '' };
    slots.push({ id: 'm' + actId, role: 'main', sAbs: absMin(p.start), eAbs: absMin(p.end), ...base });
    if (p.prep)    slots.push({ id: 'p' + actId, role: 'prep',    sAbs: absMin(p.prep.start),    eAbs: absMin(p.prep.end),    ...base });
    if (p.cleanup) slots.push({ id: 'c' + actId, role: 'cleanup', sAbs: absMin(p.cleanup.start), eAbs: absMin(p.cleanup.end), ...base });
    actId++;
  });
  return slots;
}

// Slice each program into per-day-ROW segments at the day-window boundaries.
// A program spanning two camp days yields one segment per row (the 03:00–05:00
// gap is simply skipped). Segment fields:
//   {segId, id, day, fromM, toM,   // fromM/toM = minutes within that row's window
//    contBack, contFwd,            // clipped on the left / right (continues …)
//    isStart, isEnd,               // segment holds the program's true start / end
//    sAbs, eAbs, title, cat, emoji, orgs, allDay, prep, cleanup}
function buildSegments(slots) {
  const segs = [];
  let segId = 0;
  slots.forEach((p) => {
    // derive the day range from the (mutable) absolute times, clamped to real rows.
    // Offset by WINDOW_START_MIN: the window runs 05:00→03:00, so an after-midnight time
    // is an offset of 24:00–27:00 and must still map to THIS camp day, not the next.
    const first = Math.max(0, Math.floor((p.sAbs - WINDOW_START_MIN) / DAY_MIN));
    const last = Math.min(DAYS.length - 1, Math.floor((p.eAbs - 1 - WINDOW_START_MIN) / DAY_MIN));
    for (let day = first; day <= last; day++) {
      const winLo = day * DAY_MIN + WINDOW_START_MIN;
      const winHi = day * DAY_MIN + WINDOW_END_MIN;
      const lo = Math.max(p.sAbs, winLo);
      const hi = Math.min(p.eAbs, winHi);
      if (hi <= lo) continue;                  // no overlap with this row's window
      segs.push({
        segId: segId++, id: p.id, day, role: p.role,
        fromM: lo - winLo, toM: hi - winLo,
        contBack: lo > p.sAbs + 0.5, contFwd: hi < p.eAbs - 0.5,
        isStart: Math.abs(lo - p.sAbs) < 0.5, isEnd: Math.abs(hi - p.eAbs) < 0.5,
        sAbs: p.sAbs, eAbs: p.eAbs,
        title: p.title, cat: p.cat, emoji: p.emoji, orgs: p.orgs,
      });
    }
  });
  return segs;
}

// Apply a drag/resize of ONE segment back onto its activity (the single source
// of truth), keeping both halves of a multi-day span linked.
//   - equal shift of both edges  → MOVE: translate the whole activity
//   - one edge moved             → RESIZE: change that end (cut edges are ignored,
//                                  so you can't resize at a row boundary)
// newStartAbs / newEndAbs are the segment's new absolute minutes after the edit.
// Idempotent: computes the new activity times from the segment's RENDER-TIME
// snapshot (seg.day/fromM/toM/sAbs/eAbs) and ASSIGNS them — so calling it twice
// for the same drag (GridStack fires both `change` and `dropped`) is harmless.
function applySegmentEdit(act, seg, newStartAbs, newEndAbs, snap = 15) {
  const winLo = seg.day * DAY_MIN + WINDOW_START_MIN;
  const dStart = Math.round(newStartAbs - (winLo + seg.fromM));
  const dEnd = Math.round(newEndAbs - (winLo + seg.toM));
  const MIN_DUR = snap;

  let s = seg.sAbs, e = seg.eAbs;               // baseline = activity at render time
  if (dStart === dEnd) {                        // MOVE → translate whole activity
    s += dStart; e += dStart;
  } else {                                      // RESIZE one end
    if (dStart !== 0 && seg.isStart) s = Math.min(seg.sAbs + dStart, seg.eAbs - MIN_DUR);
    if (dEnd !== 0 && seg.isEnd) e = Math.max(seg.eAbs + dEnd, seg.sAbs + MIN_DUR);
    // a moved cut edge (contBack start / contFwd end) is a window boundary → ignored
  }

  // snap to grid + keep a minimum duration
  let ns = Math.round(s / snap) * snap;
  let ne = Math.round(e / snap) * snap;
  if (ne <= ns) ne = ns + MIN_DUR;

  // guard: an edit that would leave NO visible segment (e.g. dragged past the last day)
  // is rejected — the slot stays put instead of vanishing.
  if (buildSegments([{ role: act.role, sAbs: ns, eAbs: ne }]).length === 0) return act;

  act.sAbs = ns;
  act.eAbs = ne;
  return act;
}

if (typeof module !== 'undefined') {
  module.exports = { CATEGORIES, DAYS, EVENTS, CAMP_START, absMin, buildSlots, buildSegments, applySegmentEdit,
    WINDOW_START_MIN, WINDOW_END_MIN, SLOT_MIN, SLOT_COUNT, DAY_MIN, fmt, fmtClock };
}
