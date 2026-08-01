"""Deterministic demo camp for the documentation screenshots (docs/pruvodce.md).

Writes a standalone SQLite file rather than touching the configured database, so the
demo can be regenerated at any time without disturbing a real deployment. Everything
random is drawn from one seeded Random, so the same seed yields the same camp.

Rows are inserted directly through the ORM (not the service layer): timestamps are
backdated across the planning weeks and the audit trail is seeded explicitly, which
the services — stamping everything "now" from a request identity — cannot produce.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from camp_planner.auth.identity import CampRole
from camp_planner.extensions import Base
from camp_planner.models.activity import (
    Activity,
    ActivityAssignment,
    ActivityTag,
    OrgRole,
    Todo,
    TodoAssignment,
)
from camp_planner.models.audit import AuditAction, AuditLog, EntityType
from camp_planner.models.auth import User, UserCampRole
from camp_planner.models.camp import Camp, Category, Tag, TagKind
from camp_planner.models.material import Material, MaterialAssignment, MaterialNeed, SumStrategy
from camp_planner.models.common import slugify
from camp_planner.models.google import GoogleSyncOp, SyncOpKind
from camp_planner.models.org import Org
from camp_planner.models.slot import Slot, SlotAssignment, SlotRole
from camp_planner.services import errors

CAMP_NAME = "Soustředění 2026"
PREV_CAMP_NAME = "Soustředění 2025"
SLUG = slugify(CAMP_NAME)
PREV_SLUG = slugify(PREV_CAMP_NAME)
PASSWORD = "demo1234"          # both demo accounts; the DB is a throwaway artifact
EDITOR_USER = "org"            # the everyday-organiser account the docs shots log in as
ADMIN_USER = "admin"
# The activities the docs screenshots point at (imported by scripts/shoot_docs.py).
HERO_ACTIVITY = "Šifrovačka"   # richest detail: description, material, todos, history
SLOTS_ACTIVITY = "Přednáška"   # the override_name showcase — eight individually named slots
START = date(2026, 8, 1)       # Saturday → Sunday 9. 8., nine day-rows
LENGTH_DAYS = 9
LOCATION = {"latitude": 49.5940, "longitude": 15.5800}   # Vysočina — day/night shading

# --- taxonomy ---------------------------------------------------------------

# Colours are entries of the Google event palette (google_client._EVENT_COLORS), so a
# connected camp round-trips them exactly instead of snapping to the nearest swatch.
CATEGORIES = [
    ("rozcvicka", "Rozcvička", "#e67c73"),
    ("jidlo", "Jídlo", "#039be5"),
    ("prednaska", "Přednáška", "#f6bf26"),
    ("hra-premysleci", "Přemýšlecí hra", "#7986cb"),
    ("hra-fyzicka", "Fyzická hra", "#0b8043"),
    ("hra-klidna", "Klidná hra", "#33b679"),
    ("org", "Orgování", "#f4511e"),
    ("noc", "Noc", "#616161"),
]

# Single-letter initials, all distinct; Š sorts between M and T only under czech_sort_key.
ORGS = [
    ("Adam", "A"), ("Bára", "B"), ("Eliška", "E"), ("Filip", "F"), ("Honza", "H"),
    ("Jirka", "J"), ("Klára", "K"), ("Matěj", "M"), ("Šimon", "Š"), ("Tereza", "T"),
]

TAGS = [
    ("Příprava před akcí", TagKind.progress, True),
    ("Příprava na akci", TagKind.progress, True),
    ("Uklizeno", TagKind.check, True),
    ("Vyhlášeno", TagKind.check, True),
    ("Venku", TagKind.label, True),
    ("Místo konání", TagKind.text, False),
    ("Vyžaduje plán B", TagKind.check, False),
]

PLACES = ["louka za chatou", "klubovna", "velký sál", "les nad rybníkem", "hřiště", "jídelna"]

# Materials sorted out for every activity that needs them, so the overview shows the
# fully-done state too and not only partial progress.
FULLY_READY = {"Buzoly", "Ozvučení", "Dřevo na oheň"}
# PINNED_TAG_VALUES / PINNED_NEEDS are derived from AUDIT below — see _audit_final_pins.

# --- descriptions -----------------------------------------------------------

_FILLER = [
    """Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor
incididunt ut labore et dolore magna aliqua.

- ut enim ad minim veniam
- quis nostrud exercitation ullamco
- duis aute irure dolor in reprehenderit

Excepteur sint occaecat cupidatat non proident.""",
    """### Poznámky k přípravě

Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium.

1. nemo enim ipsam voluptatem
2. neque porro quisquam est
3. ut aliquid ex ea commodi

> Consequatur vel illum qui dolorem eum fugiat.""",
    """At vero eos et accusamus et iusto odio dignissimos ducimus qui **blanditiis
praesentium** voluptatum deleniti atque corrupti.

| Část | Čas |
| --- | --- |
| úvod | 15 min |
| hra | 90 min |
| reflexe | 20 min |""",
]

HERO_DESCRIPTIONS = {
    "Šifrovačka": """## Průběh hry

Hráči se rozdělí do **čtyř týmů** po pěti. Každý tým dostane mapku s osmi
stanovišti a startovní šifru.

1. Rozdejte mapky a vysvětlete pravidla (15 min)
2. Start ve 14:00 od hlavní budovy
3. Cíl: vyluštit všech osm šifer a najít závěrečné heslo

### Co je potřeba

- lano na vyznačení startu
- fixy a papír A4 na průběžné luštění
- baterky pro případ, že se protáhne do tmy

> Za deště se hraje zkrácená varianta v klubovně, stanoviště jsou po budově.

Pravidla i tisková předloha jsou v [archivu her](https://example.org/hry/sifrovacka).""",
    "Velká strategická hra": """## Zadání

Týmy spravují vlastní území a obchodují mezi sebou. Vyhrává tým s nejvyšším
počtem bodů po **osmi kolech**.

- jedno kolo trvá 25 minut
- mezi koly je pětiminutová pauza na vyjednávání
- orgové sedí na stanovištích, nezasahují do strategie

### Role orgů

| Role | Kdo | Co dělá |
| --- | --- | --- |
| banka | 1 org | vydává a přijímá body |
| stanoviště | 4 orgové | zadávají úkoly |
| rozhodčí | 1 org | řeší spory |""",
    "Orientační běh": """## Trasa

Klasický orientační běh na **osmi kontrolách** v lese nad rybníkem.

1. Rozdělení do dvojic
2. Postupný start po 2 minutách
3. Časový limit 90 minut

Buzoly a mapy jsou v krabici s materiálem. Kdo se vrátí po limitu, dostává
trestné body.

> Kontrola č. 5 je špatně vidět, u ní stojí org.""",
    "Noční bojovka": """## Zadání

Účastníci procházejí trasu **po jednom** ve dvouminutových intervalech. Na trase
je pět stanovišť, na každém jeden org.

- start ve 21:30 od chaty
- trasa je značená reflexními páskami
- každý má baterku, ale používá ji jen v nouzi

### Bezpečnost

1. Trasa se před setměním projde a zkontroluje
2. Na konci trasy čeká org se seznamem
3. Nikdo nejde do lesa sám bez ohlášení

> Kdo nechce jít sám, může jít ve dvojici. Nikoho nenutíme.""",
    "Burza": """## Simulační hra

Týmy nakupují a prodávají komodity, jejichž cena se každé kolo mění podle
nabídky a poptávky.

- **8 kol** po 10 minutách
- ceny se vyhlašují na začátku každého kola
- vyhrává tým s největším majetkem

Kartičky s komoditami a tabulku cen připraví garanti den předem.""",
    "Divadelní scénky": """## Zadání

Každý tým dostane žánr a připraví scénku na 5 minut.

1. **Nácvik** – týmy si rozdělí role a zkouší
2. **Generálka** – jeden průchod na jevišti kvůli technice
3. **Představení** – veřejné vystoupení pro celou akci

### Materiál

- kostýmy a rekvizity z krabice v klubovně
- ozvučení a mikrofon
- plátno a projektor na pozadí""",
}

# --- schedule ---------------------------------------------------------------
# (day, start, end); end <= start means the slot runs past midnight into the next day.

RECURRING = [
    # title, category, [(day, start, end, override_name)]
    ("Rozcvička", "rozcvicka",
     [(d, "08:00", "08:30", None) for d in (2, 3, 4, 6, 7, 9)]
     + [(5, "07:00", "07:30", None), (8, "09:00", "09:30", None)]),
    ("Snídaně", "jidlo",
     [(d, "08:30", "09:00", None) for d in (2, 3, 4, 6, 7, 9)]
     + [(5, "07:30", "08:00", None), (8, "09:30", "10:00", None)]),
    ("Oběd", "jidlo",
     [(d, "13:00", "14:00", None) for d in (2, 3, 4, 6, 7, 8)]
     + [(9, "12:30", "13:30", None)]),
    ("Večeře", "jidlo",
     [(d, "19:00", "20:00", None) for d in (1, 2, 3, 4, 6, 8)]
     + [(5, "18:30", "19:30", None), (7, "20:00", "21:00", None)]),
    # One activity, eight slots, each named by its topic — the override_name showcase.
    ("Přednáška", "prednaska", [
        (2, "09:00", "10:45", "Základy algoritmizace"),
        (2, "17:00", "18:45", "První pomoc"),
        (3, "09:00", "10:45", "Rétorika a prezentace"),
        (4, "09:00", "10:45", "Základy fotografování"),
        (6, "09:00", "10:45", "Multidimenzionální geometrie"),
        (6, "17:45", "18:45", "Astronomie"),
        (7, "09:00", "10:45", "Kryptografie a šifry"),
        (8, "10:00", "11:45", "Jak vést tým (offtopic)"),
    ]),
    ("Porada orgů", "org", [
        (1, "22:45", "23:15", None), (2, "22:00", "22:30", None),
        (3, "22:15", "22:45", None), (4, "22:00", "22:30", None),
        (5, "21:00", "21:30", None), (6, "22:00", "22:30", None),
        (7, "21:00", "21:20", None), (8, "23:15", "23:45", None),
    ]),
]

assert HERO_ACTIVITY in HERO_DESCRIPTIONS
# The slot shot expands the collapsed list, which only renders above three slots.
assert next(len(s) for t, _c, s in RECURRING if t == SLOTS_ACTIVITY) > 3

# title, category, [(role, day, start, end, override_name)]
ONE_OFF = [
    ("Příjezd a ubytování", "org", [("main", 1, "14:00", "16:00", None)]),
    ("Zahajovací nástup", "org", [("main", 1, "16:00", "16:30", None)]),
    ("Seznamovací hry", "hra-klidna", [("main", 1, "16:30", "18:30", None)]),
    ("Zahajovací táborák", "hra-klidna", [
        ("prep", 1, "18:00", "19:00", None),
        ("main", 1, "20:30", "22:30", None),
    ]),
    ("Orientační běh", "hra-fyzicka", [
        ("prep", 2, "08:00", "09:00", None),
        ("main", 2, "10:45", "12:45", None),
    ]),
    # Two parallel options at the same time — the overlap showcase.
    ("Vybíjená", "hra-fyzicka", [("main", 2, "14:30", "16:30", None)]),
    ("Deskovky", "hra-klidna", [("main", 2, "14:30", "16:30", None)]),
    ("Diskuzní kroužky", "hra-klidna", [("main", 2, "20:30", "22:00", None)]),
    ("Divadelní scénky", "hra-klidna", [
        ("main", 3, "10:45", "12:45", "nácvik"),
        ("main", 3, "15:00", "16:30", "generálka"),
        ("main", 3, "20:00", "22:00", "představení"),
    ]),
    ("Turnaj ve fotbale", "hra-fyzicka", [("main", 3, "16:45", "18:45", None)]),
    ("Fotografická výprava", "hra-klidna", [("main", 4, "10:45", "12:45", None)]),
    ("Velká strategická hra", "hra-premysleci", [
        ("prep", 4, "11:00", "12:45", None),
        ("main", 4, "14:30", "18:30", None),
    ]),
    ("Zpívání s kytarou", "hra-klidna", [("main", 4, "20:30", "22:00", None)]),
    ("Celodenní výlet", "hra-fyzicka", [("main", 5, "08:30", "18:00", None)]),
    ("Vědomostní kvíz", "hra-premysleci", [("main", 5, "19:45", "21:00", None)]),
    ("Burza", "hra-premysleci", [
        ("prep", 6, "08:00", "09:00", None),
        ("main", 6, "10:45", "12:45", None),
    ]),
    ("Vodní bitva u rybníka", "hra-fyzicka", [("main", 6, "14:30", "17:30", None)]),
    ("Tvořivá dílna", "hra-klidna", [("main", 6, "14:30", "17:30", None)]),
    ("Pozorování hvězd", "hra-klidna", [("main", 6, "22:30", "00:00", None)]),
    # Runs past the 04:00 day boundary, so build_timeline slices it across two day-rows.
    ("Přespání pod širákem", "noc", [("main", 6, "23:00", "08:00", None)]),
    # prep / main / cleanup on one activity, main running through the afternoon.
    ("Šifrovačka", "hra-premysleci", [
        ("prep", 7, "09:00", "11:00", None),
        ("main", 7, "14:00", "19:00", None),
        ("cleanup", 7, "19:00", "20:00", None),
    ]),
    # Crosses midnight: stays on day 7's row because the day window starts at 04:00.
    ("Noční bojovka", "noc", [("main", 7, "21:30", "01:30", None)]),
    ("Sportovní odpoledne", "hra-fyzicka", [("main", 8, "14:30", "17:30", None)]),
    ("Závěrečný večer a vyhlášení", "org", [("main", 8, "20:00", "23:00", None)]),
    ("Balení a úklid", "org", [("main", 9, "09:00", "11:30", None)]),
    ("Závěrečné kolečko", "org", [("main", 9, "11:30", "12:30", None)]),
    ("Odjezd", "org", [("main", 9, "14:00", "15:00", None)]),
]

# Activities with no slots yet — the "not scheduled" pool.
UNSCHEDULED = [
    ("Stopovaná", "hra-fyzicka"),
    ("Hra na velkém území", "hra-fyzicka"),
    ("Náboj", "hra-premysleci"),
    ("Bojovka s baterkami", "noc"),
]

# --- materials --------------------------------------------------------------
# name, unit, strategy, acquisition labels, [activities that need it], typical amount

MATERIALS = [
    ("Papír A4", "ks", SumStrategy.sum, ["koupit: papírnictví"],
     ["Šifrovačka", "Velká strategická hra", "Burza", "Tvořivá dílna", "Vědomostní kvíz"], 80),
    ("Fixy", "ks", SumStrategy.sum, ["koupit: papírnictví"],
     ["Divadelní scénky", "Tvořivá dílna", "Burza", "Šifrovačka"], 12),
    ("Izolepa", "ks", SumStrategy.sum, [],
     ["Šifrovačka", "Tvořivá dílna", "Orientační běh"], 3),
    ("Provázek", "m", SumStrategy.sum, ["koupit: železářství"],
     ["Šifrovačka", "Noční bojovka", "Orientační běh"], 50),
    ("Špekáčky", "kg", SumStrategy.sum, ["koupit: Makro"],
     ["Zahajovací táborák"], 8),
    ("Dřevo na oheň", "m³", SumStrategy.sum, ["zajistí chata"],
     ["Zahajovací táborák"], 1),
    ("Kartičky na hru", "ks", SumStrategy.sum, ["vyrobit"],
     ["Burza", "Velká strategická hra"], 200),
    ("Projektor", "ks", SumStrategy.max, ["půjčit: škola"],
     ["Přednáška", "Divadelní scénky", "Vědomostní kvíz"], 1),
    ("Plátno", "ks", SumStrategy.max, ["půjčit: škola"],
     ["Přednáška", "Vědomostní kvíz"], 1),
    ("Ozvučení", "ks", SumStrategy.max, ["půjčit: Klára"],
     ["Divadelní scénky", "Závěrečný večer a vyhlášení", "Zpívání s kytarou"], 1),
    ("Lano 20 m", "ks", SumStrategy.max, [],
     ["Šifrovačka", "Sportovní odpoledne"], 2),
    ("Baterky", "ks", SumStrategy.max, ["koupit: baterie zvlášť"],
     ["Noční bojovka", "Šifrovačka", "Pozorování hvězd"], 15),
    ("Lékárnička", "ks", SumStrategy.max, [],
     ["Celodenní výlet", "Noční bojovka", "Sportovní odpoledne", "Vodní bitva u rybníka"], 1),
    ("Míče", "ks", SumStrategy.max, [],
     ["Vybíjená", "Turnaj ve fotbale", "Sportovní odpoledne"], 4),
    ("Rozlišovací dresy", "ks", SumStrategy.max, ["půjčit: tělocvična"],
     ["Vybíjená", "Turnaj ve fotbale", "Sportovní odpoledne"], 20),
    ("Buzoly", "ks", SumStrategy.max, ["půjčit: skauti"],
     ["Orientační běh", "Celodenní výlet"], 10),
]

# --- todos ------------------------------------------------------------------
# activity, title, due offset in days before camp start, done

TODOS = [
    ("Šifrovačka", "Vymyslet a otestovat všech osm šifer", 21, True),
    ("Šifrovačka", "Vytisknout zadání pro čtyři týmy", 7, True),
    ("Šifrovačka", "Obejít trasu a vybrat stanoviště", 3, False),
    ("Šifrovačka", "Připravit náhradní variantu do klubovny", 5, False),
    ("Velká strategická hra", "Dopsat pravidla", 28, True),
    ("Velká strategická hra", "Nachystat bankovní kartičky", 10, False),
    ("Velká strategická hra", "Zaškolit orgy na stanoviště", 2, False),
    ("Orientační běh", "Zajistit mapy oblasti", 14, True),
    ("Orientační běh", "Rozvěsit kontroly", 1, False),
    ("Noční bojovka", "Projít trasu za světla", 1, False),
    ("Noční bojovka", "Zkontrolovat baterky a náhradní baterie", 4, True),
    ("Noční bojovka", "Domluvit se s chatou na nočním klidu", 12, True),
    ("Burza", "Nastavit tabulku cen komodit", 9, False),
    ("Burza", "Natisknout komoditní kartičky", 6, False),
    ("Divadelní scénky", "Sesbírat kostýmy a rekvizity", 15, True),
    ("Divadelní scénky", "Ověřit ozvučení v sále", 2, False),
    ("Přednáška", "Potvrdit témata s přednášejícími", 30, True),
    ("Přednáška", "Půjčit projektor ze školy", 8, True),
    ("Zahajovací táborák", "Objednat špekáčky", 5, False),
    ("Zahajovací táborák", "Domluvit dřevo s chatou", 20, True),
    ("Celodenní výlet", "Naplánovat trasu a zjistit spoje", 18, True),
    ("Celodenní výlet", "Připravit balíčky na oběd", 1, False),
    ("Vodní bitva u rybníka", "Ověřit kvalitu vody", 4, False),
    ("Závěrečný večer a vyhlášení", "Připravit diplomy", 3, False),
    ("Vědomostní kvíz", "Sestavit otázky", 11, False),
]

# --- audit trail ------------------------------------------------------------
# days before camp start, author initials, activity, entity, action, target, changes.
# `target` names the row the entry is about (slot: its (day, "HH:MM") start; todo: its
# title; material_need / material: the material name; tag: tag name). None = the activity
# itself, or no single row (assignment, timeline). A typo fails loudly at seed time
# instead of silently mis-filing the entry, and a slot entry whose final start/end
# disagrees with the schedule tables fails the seed too.
#
# The `changes` dicts mirror what the services actually record (booleans, floats, ISO
# datetimes, initials *lists* for garant/helper) — history-feed.js renders unknown keys
# raw and string booleans as English literals, so an invented shape shows in screenshots.


def _dt(start: date, day: int, hhmm: str, *, end_of: str | None = None) -> datetime:
    """Wall-clock datetime of `hhmm` on the camp's `day` (1-based). When `end_of` is the
    matching start time and the end is not after it, the slot runs past midnight."""
    h, m = (int(x) for x in hhmm.split(":"))
    moment = datetime.combine(start + timedelta(days=day - 1), time(h, m))
    if end_of is not None:
        sh, sm = (int(x) for x in end_of.split(":"))
        if (h, m) <= (sh, sm):
            moment += timedelta(days=1)
    return moment


def _iso(day: int, hhmm: str) -> str:
    """Slot-time literal for an AUDIT diff: ISO form of `hhmm` on camp day `day`, as
    audit.record serialises datetimes. Computed so the literals move with START."""
    return _dt(START, day, hhmm).isoformat()


def _category_id(key: str) -> int:
    """Autoincrement id a 2026-camp category gets: CATEGORIES order, 1-based (the camp is
    seeded first and _seed_taxonomy inserts categories before orgs/tags)."""
    return 1 + [k for k, _label, _color in CATEGORIES].index(key)


AUDIT = [
    (44, "K", "Šifrovačka", EntityType.activity, AuditAction.create, None,
     {"title": [None, "Šifrovačka"]}),
    (43, "K", "Šifrovačka", EntityType.tag, AuditAction.update, "Příprava před akcí",
     {"Příprava před akcí": ["0", "20"]}),
    (41, "M", "Velká strategická hra", EntityType.activity, AuditAction.create, None,
     {"title": [None, "Velká strategická hra"]}),
    (39, "A", "Orientační běh", EntityType.activity, AuditAction.update, None,
     {"category_id": [_category_id("hra-klidna"), _category_id("hra-fyzicka")]}),
    (37, "K", "Šifrovačka", EntityType.material_need, AuditAction.create, "Papír A4",
     {"material": [None, "Papír A4"]}),
    (35, "T", "Přednáška", EntityType.slot, AuditAction.create, (2, "09:00"),
     {"role": [None, "main"], "start_at": [None, _iso(2, "09:00")],
      "end_at": [None, _iso(2, "10:45")]}),
    (33, "M", "Velká strategická hra", EntityType.assignment, AuditAction.update, None,
     {"garant": [[], ["M", "Š"]]}),
    (29, "K", "Šifrovačka", EntityType.todo, AuditAction.create,
     "Vymyslet a otestovat všech osm šifer",
     {"title": [None, "Vymyslet a otestovat všech osm šifer"]}),
    (27, "T", "Divadelní scénky", EntityType.activity, AuditAction.create, None,
     {"title": [None, "Divadelní scénky"]}),
    (26, "A", "Noční bojovka", EntityType.slot, AuditAction.update, (7, "21:30"),
     {"end_at": [_iso(8, "00:30"), _iso(8, "01:30")]}),
    (24, "K", "Šifrovačka", EntityType.tag, AuditAction.update, "Příprava před akcí",
     {"Příprava před akcí": ["20", "45"]}),
    (23, "E", None, EntityType.material, AuditAction.create, "Buzoly",
     {"name": [None, "Buzoly"]}),
    (21, "M", "Burza", EntityType.activity, AuditAction.create, None,
     {"title": [None, "Burza"]}),
    (20, "F", "Celodenní výlet", EntityType.todo, AuditAction.update,
     "Naplánovat trasu a zjistit spoje",
     {"is_done": [False, True]}),
    (18, "K", "Šifrovačka", EntityType.slot, AuditAction.update, (7, "14:00"),
     {"start_at": [_iso(7, "13:00"), _iso(7, "14:00")],
      "end_at": [_iso(7, "18:00"), _iso(7, "19:00")]}),
    (17, "T", "Divadelní scénky", EntityType.slot, AuditAction.create, (3, "15:00"),
     {"role": [None, "main"], "start_at": [None, _iso(3, "15:00")],
      "end_at": [None, _iso(3, "16:30")]}),
    (14, "A", "Orientační běh", EntityType.todo, AuditAction.update, "Zajistit mapy oblasti",
     {"is_done": [False, True]}),
    (12, "K", "Šifrovačka", EntityType.material_need, AuditAction.update, "Papír A4",
     {"amount": [40.0, 60.0], "is_ready": [False, True]}),
    (11, "B", "Vodní bitva u rybníka", EntityType.activity, AuditAction.update, None,
     {"description_md": ["", "…"]}),
    (10, "M", "Velká strategická hra", EntityType.tag, AuditAction.update, "Vyžaduje plán B",
     {"Vyžaduje plán B": ["false", "true"]}),
    (9, "J", "Šifrovačka", EntityType.assignment, AuditAction.update, None,
     {"helper": [["J"], ["J", "T"]]}),
    (8, "T", "Přednáška", EntityType.material_need, AuditAction.create, "Projektor",
     {"material": [None, "Projektor"]}),
    (7, "K", "Šifrovačka", EntityType.todo, AuditAction.update,
     "Vymyslet a otestovat všech osm šifer",
     {"is_done": [False, True]}),
    (6, "Š", "Šifrovačka", EntityType.slot, AuditAction.create, (7, "19:00"),
     {"role": [None, "cleanup"], "start_at": [None, _iso(7, "19:00")],
      "end_at": [None, _iso(7, "20:00")]}),
    (5, "A", None, EntityType.timeline, AuditAction.update, None,
     {"moved": 6, "created": 0, "retyped": 0, "deleted": 0}),
    (4, "K", "Šifrovačka", EntityType.tag, AuditAction.update, "Příprava před akcí",
     {"Příprava před akcí": ["45", "80"]}),
    (3, "H", "Zahajovací táborák", EntityType.todo, AuditAction.update,
     "Domluvit dřevo s chatou",
     {"is_done": [False, True]}),
    (2, "T", "Divadelní scénky", EntityType.tag, AuditAction.update, "Vyhlášeno",
     {"Vyhlášeno": ["false", "true"]}),
]


def _audit_final_pins() -> tuple[dict, dict]:
    """Values the audit trail narrates reaching, folded out of AUDIT (last write wins,
    made structural by sorting on days-before). The generator pins the current state to
    these so the history tab and the pages it describes agree — kept as a derivation, not
    a second hand-maintained table that could drift."""
    tag_pins: dict[tuple[str, str], str] = {}
    need_pins: dict[tuple[str, str], tuple[float, bool]] = {}
    for _days, _who, title, entity, _action, target, changes in sorted(
            AUDIT, key=lambda row: -row[0]):
        if entity is EntityType.tag:
            tag_pins[(title, target)] = changes[target][1]
        elif entity is EntityType.material_need and ("amount" in changes or "is_ready" in changes):
            need_pins[(title, target)] = (changes["amount"][1], changes["is_ready"][1])
    return tag_pins, need_pins


PINNED_TAG_VALUES, PINNED_NEEDS = _audit_final_pins()
# FULLY_READY outranks a PINNED_NEEDS ready-flag (see the `or` in the need constructor);
# keep the two disjoint so the history can't contradict the materials overview.
assert not FULLY_READY & {material for _title, material in PINNED_NEEDS}

# Previous year's camp: taxonomy plus a few activities, so the landing page has two
# rows and the new-camp form has something to copy settings from.
PREV_ACTIVITIES = [
    ("Seznamovací hry", "hra-klidna", 1, "16:30", "18:30"),
    ("Šifrovačka", "hra-premysleci", 4, "14:00", "19:00"),
    ("Celodenní výlet", "hra-fyzicka", 5, "08:30", "18:00"),
    ("Turnaj ve vybíjené", "hra-fyzicka", 6, "14:30", "17:00"),
    ("Táborák", "hra-klidna", 7, "20:00", "23:00"),
    ("Závěrečný večer", "org", 8, "20:00", "23:00"),
]


def build(path: str | Path, seed: int = 20260801, calendar_id: str | None = None) -> dict[str, int]:
    """Create a fresh demo SQLite file at `path`. Returns a count summary.

    Refuses to overwrite a database that is not recognisably a previous demo build, so a
    mistyped --out cannot delete a real one.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not _is_demo_db(target):
            raise errors.Invalid(
                f"{target} exists and does not look like a generated demo database "
                f"(no camp with slug {SLUG!r}) — refusing to overwrite it."
            )
        target.unlink()

    rnd = random.Random(seed)
    engine = create_engine(f"sqlite:///{target}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        counts = _populate(db, rnd, calendar_id)
        db.commit()
    engine.dispose()
    return counts


def _is_demo_db(path: Path) -> bool:
    """True when the file is a SQLite DB holding this generator's camp (and so is ours to
    replace). Anything unreadable or unrecognised counts as not ours."""
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            return db.scalar(select(Camp).filter_by(slug=SLUG)) is not None
    except Exception:      # noqa: BLE001 — not a readable DB of ours, treat as foreign
        return False
    finally:
        engine.dispose()


def _seed_taxonomy(db: Session, camp: Camp, cat_rows, org_rows,
                   tag_rows) -> tuple[dict, dict, dict]:
    """Categories, orgs and tags for one camp — parameters and the returned triple share
    that order; returned dicts are keyed by key / initials / name."""
    cats, orgs, tags = {}, {}, {}
    for i, (key, label, color) in enumerate(cat_rows):
        cats[key] = Category(camp_id=camp.id, key=key, label=label, color=color, sort_order=i)
    for name, initials in org_rows:
        orgs[initials] = Org(camp_id=camp.id, name=name, initials=initials)
    for i, (name, kind, pinned) in enumerate(tag_rows):
        tags[name] = Tag(camp_id=camp.id, name=name, kind=kind, pinned=pinned, sort_order=i)
    db.add_all([*cats.values(), *orgs.values(), *tags.values()])
    db.flush()
    return cats, orgs, tags


def _populate(db: Session, rnd: random.Random, calendar_id: str | None) -> dict[str, int]:
    planning_start = datetime.combine(START - timedelta(days=60), time(9, 0))

    camp = Camp(
        name=CAMP_NAME, slug=SLUG,
        start_date=START, length_days=LENGTH_DAYS,
        # timezone / window_start_min / snap_minutes deliberately left at the model defaults.
        **LOCATION,
        google_calendar_id=calendar_id,
        timeline_rev=47,
        created_at=planning_start, updated_at=datetime.combine(START - timedelta(days=2), time(18, 0)),
    )
    db.add(camp)
    db.flush()

    cats, orgs, tags = _seed_taxonomy(db, camp, CATEGORIES, ORGS, TAGS)
    org_list = list(orgs.values())
    activities: dict[str, Activity] = {}
    materials: dict[str, Material] = {}
    all_slots: list[Slot] = []

    def add_activity(title: str, cat_key: str, created_offset: int) -> Activity:
        act = Activity(
            camp_id=camp.id,
            category_id=cats[cat_key].id,
            title=title,
            description_md=HERO_DESCRIPTIONS.get(title, rnd.choice(_FILLER)),
            created_at=datetime.combine(START - timedelta(days=created_offset), time(rnd.randrange(9, 21))),
            updated_at=datetime.combine(START - timedelta(days=rnd.randrange(1, 10)), time(rnd.randrange(9, 21))),
        )
        db.add(act)
        db.flush()          # id needed by the slots added right after
        activities[title] = act
        return act

    def add_slot(act: Activity, role: str, day: int, start: str, end: str,
                 override: str | None) -> Slot:
        slot = Slot(
            activity_id=act.id, role=SlotRole(role),
            start_at=_dt(START, day, start),
            end_at=_dt(START, day, end, end_of=start),
            override_name=override,
        )
        db.add(slot)
        db.flush()
        all_slots.append(slot)
        # Meals are nobody's shift — everyone just eats — so they carry no attendants.
        if act.category_id != cats["jidlo"].id:
            # Two to four orgs staff each block; prep/cleanup are run by a smaller crew.
            size = rnd.randrange(2, 5) if slot.role is SlotRole.main else rnd.randrange(1, 3)
            for org in rnd.sample(org_list, size):
                db.add(SlotAssignment(slot_id=slot.id, org_id=org.id))
        return slot

    # created_at countdown: the earliest-planned activities are the recurring staples.
    for i, (title, cat_key, slots) in enumerate(RECURRING):
        act = add_activity(title, cat_key, 50 - 2 * i)
        for day, start, end, override in slots:
            add_slot(act, "main", day, start, end, override)

    one_off_base = 50 - 2 * len(RECURRING)
    for j, (title, cat_key, slots) in enumerate(ONE_OFF):
        act = add_activity(title, cat_key, one_off_base - j)
        for role, day, start, end, override in slots:
            add_slot(act, role, day, start, end, override)

    for title, cat_key in UNSCHEDULED:
        add_activity(title, cat_key, rnd.randrange(3, 12))

    # Garants and helpers, independent of who staffs the individual blocks.
    for act in activities.values():
        picks = rnd.sample(org_list, rnd.randrange(2, 4))
        db.add(ActivityAssignment(activity_id=act.id, org_id=picks[0].id, role=OrgRole.garant))
        for helper in picks[1:]:
            db.add(ActivityAssignment(activity_id=act.id, org_id=helper.id, role=OrgRole.helper))

    # Tags: every activity carries the two progress tags, the rest are sprinkled with the
    # given probability — except values pinned in PINNED_TAG_VALUES, which always emit.
    def put_tag(act: Activity, name: str, value: str | None) -> None:
        db.add(ActivityTag(activity_id=act.id, tag_id=tags[name].id,
                           value=PINNED_TAG_VALUES.get((act.title, name), value)))

    sprinkled = [   # tag name, probability, value drawn when the tag is emitted
        ("Uklizeno", 0.55, lambda: "true" if rnd.random() < 0.4 else "false"),
        ("Vyhlášeno", 0.5, lambda: "true" if rnd.random() < 0.45 else "false"),
        ("Venku", 0.35, lambda: None),
        ("Místo konání", 0.4, lambda: rnd.choice(PLACES)),
        ("Vyžaduje plán B", 0.25, lambda: "true" if rnd.random() < 0.5 else "false"),
    ]
    for act in activities.values():
        put_tag(act, "Příprava před akcí", str(rnd.randrange(0, 21) * 5))
        put_tag(act, "Příprava na akci", str(rnd.randrange(0, 11) * 5))
        for name, prob, value in sprinkled:
            if (act.title, name) in PINNED_TAG_VALUES or rnd.random() < prob:
                put_tag(act, name, value())

    for name, unit, strategy, labels, users, amount in MATERIALS:
        mat = Material(camp_id=camp.id, name=name, unit=unit, sum_strategy=strategy,
                       acquisition_labels=labels,
                       url="https://example.org/eshop" if labels and
                       labels[0].startswith("koupit") else None)
        db.add(mat)
        db.flush()
        materials[name] = mat
        for org in rnd.sample(org_list, rnd.randrange(1, 3)):
            db.add(MaterialAssignment(material_id=mat.id, org_id=org.id))
        for title in users:
            act = activities[title]
            pin_amount, pin_ready = PINNED_NEEDS.get((title, name), (None, None))
            db.add(MaterialNeed(
                activity_id=act.id, material_id=mat.id,
                amount=pin_amount if pin_amount is not None
                else float(max(1, int(amount * rnd.uniform(0.4, 1.0)))),
                note="půjčeno od skautů" if rnd.random() < 0.15 else None,
                is_ready=name in FULLY_READY or
                (pin_ready if pin_ready is not None else rnd.random() < 0.4),
            ))

    for title, todo_title, due_offset, done in TODOS:
        act = activities[title]
        todo = Todo(activity_id=act.id, title=todo_title, is_done=done,
                    due_date=START - timedelta(days=due_offset) if rnd.random() < 0.7 else None,
                    note="domluveno na poradě" if rnd.random() < 0.2 else None)
        db.add(todo)
        db.flush()
        for org in rnd.sample(org_list, rnd.randrange(0, 3)):
            db.add(TodoAssignment(todo_id=todo.id, org_id=org.id))

    def audit_row_id(entity: EntityType, act: Activity | None, target, changes: dict) -> int:
        """The row an AUDIT `target` points at — resolved from the relationships of the
        rows just created, so a typo fails loudly (StopIteration / KeyError) at seed time
        instead of silently mis-filing the entry."""
        if entity in (EntityType.slot, EntityType.todo, EntityType.material_need):
            assert act is not None   # these targets are always activity-scoped
            if entity is EntityType.slot:
                slot = next(s for s in act.slots if s.start_at == _dt(START, *target))
                # The narrated final times must match the schedule tables, else the
                # history feed contradicts the slot chips in the same screenshot.
                for field in ("start_at", "end_at"):
                    told = changes.get(field, [None, None])[1]
                    assert told is None or told == getattr(slot, field).isoformat(), \
                        f"AUDIT {act.title} slot {target}: {field} disagrees with the schedule"
                return slot.id
            if entity is EntityType.todo:
                return next(t.id for t in act.todos if t.title == target)
            return next(n.id for n in act.material_needs if n.material.name == target)
        return {EntityType.tag: tags, EntityType.material: materials}[entity][target].id

    db.flush()   # ids for everything the audit rows point at
    for days_before, who, title, entity, action, target, changes in AUDIT:
        act = activities.get(title) if title else None
        if target is not None:
            entity_id = audit_row_id(entity, act, target, changes)
        else:
            entity_id = act.id if act and entity is EntityType.activity else None
        db.add(AuditLog(
            camp_id=camp.id,
            activity_id=act.id if act else None,
            entity_type=entity, entity_id=entity_id, action=action,
            author=slugify(orgs[who].name), changes=changes,
            created_at=datetime.combine(START - timedelta(days=days_before),
                                        time(rnd.randrange(9, 22), rnd.randrange(0, 60))),
        ))

    prev = _build_previous_camp(db, rnd)

    # Explicit timestamps: the defaults are func.now(), which would make two runs of the
    # same seed differ. (The scrypt salt still does, unavoidably.)
    accounts_at = datetime.combine(START - timedelta(days=70), time(8, 0))
    admin = User(username=ADMIN_USER, display_name="Správce", is_admin=True,
                 created_at=accounts_at, updated_at=accounts_at)
    admin.set_password(PASSWORD)
    editor = User(username=EDITOR_USER, display_name="Org Pilný", is_admin=False,
                  created_at=accounts_at, updated_at=accounts_at)
    # Same password → reuse the hash; the second scrypt derivation is a third of the
    # whole build time and buys nothing for a throwaway demo artifact.
    editor.password_hash = admin.password_hash
    db.add_all([admin, editor])
    db.flush()
    db.add(UserCampRole(user_id=editor.id, camp_id=camp.id, role=CampRole.editor))
    db.add(UserCampRole(user_id=editor.id, camp_id=prev.id, role=CampRole.editor))

    # A connected camp starts with its outbound queue full, exactly as the app's connect
    # button leaves it — so `flask sync-google` pushes the schedule with no extra step.
    if calendar_id:
        queued_at = datetime.combine(START - timedelta(days=1), time(12, 0))
        db.add_all(GoogleSyncOp(camp_id=camp.id, slot_id=s.id, op=SyncOpKind.upsert,
                                created_at=queued_at, updated_at=queued_at)
                   for s in all_slots)

    return {
        "activities": len(activities),
        "slots": len(all_slots),
        "orgs": len(orgs),
        "materials": len(MATERIALS),
        "todos": len(TODOS),
        "audit": len(AUDIT),
    }


def _build_previous_camp(db: Session, rnd: random.Random) -> Camp:
    start = date(2025, 8, 2)
    camp = Camp(
        name=PREV_CAMP_NAME, slug=PREV_SLUG,
        start_date=start, length_days=LENGTH_DAYS,
        **LOCATION,
        created_at=datetime.combine(start - timedelta(days=70), time(10, 0)),
        updated_at=datetime.combine(start + timedelta(days=LENGTH_DAYS), time(20, 0)),
    )
    db.add(camp)
    db.flush()

    # Smaller than this year on every axis — the team grew and the `noc` category
    # only appeared for 2026.
    prev_cats = [c for c in CATEGORIES if c[0] != "noc"]
    cats, org_map, _ = _seed_taxonomy(db, camp, prev_cats, ORGS[:7], TAGS[:5])
    orgs = list(org_map.values())

    for title, cat_key, day, start_hhmm, end_hhmm in PREV_ACTIVITIES:
        act = Activity(camp_id=camp.id, category_id=cats[cat_key].id,
                       title=title,
                       description_md=rnd.choice(_FILLER),
                       # Explicit, else the default lands on "now" and two runs of the same
                       # seed differ — the generator promises to be reproducible.
                       created_at=datetime.combine(start - timedelta(days=40), time(11, 0)),
                       updated_at=datetime.combine(start - timedelta(days=6), time(17, 30)))
        db.add(act)
        db.flush()
        slot = Slot(activity_id=act.id, role=SlotRole.main,
                    start_at=_dt(start, day, start_hhmm),
                    end_at=_dt(start, day, end_hhmm, end_of=start_hhmm))
        db.add(slot)
        db.flush()
        for org in rnd.sample(orgs, 2):
            db.add(SlotAssignment(slot_id=slot.id, org_id=org.id))
    return camp
