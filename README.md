# Camp Planner

Planning tool for camps made of many activities across several days. A Python
Flask web app that runs on **SQLite, PostgreSQL or MySQL**.

It has three layers: a relational **data model** with a service layer on top,
a server-rendered **web UI** (vis-timeline day-grid editor, camp settings,
activity and material pages), and a pydantic-validated **JSON REST API** under
`/api` (Swagger at `/apidoc/swagger`).

## Quick start

```bash
uv venv                             # Python 3.11+
uv pip install -e '.[dev]'          # [dev] adds pytest + ruff; add [postgres] / [mysql] for those drivers
cp .env.example .env                # default backend is SQLite — no DB server needed

uv run flask --app wsgi db upgrade                 # create the schema
uv run flask --app wsgi create-user admin --admin  # bootstrap the first admin (prompts for a password)
uv run flask --app wsgi run                        # http://127.0.0.1:5000/
```

Run the tests with `uv run pytest`.

## Database

The schema uses only portable SQLAlchemy types, DB selection is by environment:

- **`DATABASE_URL`**: if set, used instead of other DB config (highest priority)
- **`DB_BACKEND`** = `sqlite` | `postgresql` | `mysql`
  - when `sqlite`: `SQLITE_PATH` needed
  - otherwise: `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` needed

Install the matching driver as needed: `uv pip install -e '.[postgres]'` (psycopg 3) or
`uv pip install -e '.[mysql]'` (PyMySQL).

Set **`DB_TABLE_PREFIX`** (e.g. `cp_`) to prefix every table name so the schema can share
a database with another app without collisions. The prefix is fixed at import time, so it
must come from the environment and match between when a migration is generated and when it
runs.

## Authentication modes

The same codebase runs in three shapes, chosen by **`AUTH_MODE`** — they differ only in how
the current user is identified; identical role rules apply on top. Full walkthroughs are in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

- **`standalone`** (default) — own users + login. Bootstrap the first admin with `create-user`;
  admins then manage users in-app at `/auth/users`.
- **`embedded`** — a bigger Flask app mounts Camp Planner's blueprints with
  `register_camp_planner(...)`; identity comes from a callback.
- **`proxy`** — runs behind nginx alongside an existing app; identity arrives as trusted
  `X-Remote-*` headers.

**Roles** (all modes): `admin` (global), `editor` (edit within a camp except its name/slug),
`viewer` (read-only). `editor`/`viewer` are scoped to specific camps or unscoped. Identity
resolves to an `Identity` ([auth/identity.py](camp_planner/auth/identity.py)) and views gate
on the helpers in [auth/permissions.py](camp_planner/auth/permissions.py).

## Data model

```text
Camp ─┬─ Category        (per-camp palette; user-defined key / label / color)
      ├─ Org             (per-camp roster; shown as initials)
      ├─ Tag             (kind: label | check | progress | text; pinned tags show as columns)
      ├─ Material        (canonical catalog, deduplicated per camp)
      └─ Activity ─┬─ (type: basic | external | external_lecture | …; config JSON per type)
                   ├─ Slot ── SlotAssignment   (orgs staffing that block during the camp)
                   ├─ ActivityAssignment        (Org × role{garant,helper} — planning responsibility)
                   ├─ MaterialNeed (→ Material; amount/unit/note/is_ready)
                   ├─ Todo
                   └─ ActivityTag (→ Tag; per-activity value)

User / UserCampRole   (standalone auth; present but empty under proxy/embedded)
AuditLog              (append-only; grouped by activity_id; field-level JSON diff)
```

Slots carry real clock times (naive datetimes in the camp timezone), 24h day rows may cross
midnight (`Camp.window_start_min`), overlaps are allowed, and prep/cleanup are independent
`Slot` rows. See `docs/REQUIREMENTS.md` for how each decision maps to a requirement.

## Migrations

Flask-Migrate (Alembic) is wired up with `render_as_batch=True` so column changes migrate on
SQLite too.

```bash
uv run flask --app wsgi db upgrade            # apply the committed migrations
uv run flask --app wsgi db migrate -m "…"     # autogenerate a new one after changing models
```

`flask init-db` does a quick `create_all()` without migration tracking.

## Layout

```text
camp_planner/
  __init__.py     app factory
  config.py       config + DB-backend selection + table-name prefix
  extensions.py   db, migrate, declarative Base
  cli.py          init-db / create-user / grant-role
  api.py          JSON REST API blueprint (/api)
  schemas.py      pydantic request/response models (validation + OpenAPI)
  views.py        HTML pages (camp list, timeline, detail, settings)
  integration.py  embedding hook for a host Flask app
  auth/           identity contract, permissions, standalone/proxy/embedded providers
  models/         camp, org, activity, slot, material, audit, auth
  services/       business logic (activities, camps, slots, materials, taxonomy, audit, …)
  templates/      Jinja2 templates
  static/         CSS / JS (timeline editor) / vendored vis-timeline
migrations/       Alembic migration scripts
tests/            pytest suite
wsgi.py           entry point
docs/             requirements, deployment, frontend research, timeline mockups
```
