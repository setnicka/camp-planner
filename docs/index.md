# Camp Planner documentation

Camp Planner plans camps made of many overlapping activities across several days: a
day-grid timeline editor, per-activity material and todo tracking, and reviewed two-way
Google Calendar sync. Flask + SQLAlchemy, runs on SQLite, PostgreSQL or MySQL.

## Where to start

| Document | Covers |
| --- | --- |
| [../README.md](../README.md) | Install, database backends, auth modes, first run |
| [pruvodce.md](pruvodce.md) | **User guide (Czech).** Every screen and feature, with screenshots |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Standalone, embedded and proxy deployments; theming a host app |
| [google_calendar_setup.md](google_calendar_setup.md) | Service account, calendar sharing, sync scheduling |
| `/apidoc/swagger` | REST API reference, served by a running instance |

Pick the deployment shape first: `standalone` brings its own users and login, `proxy`
trusts `X-Remote-*` headers, `embedded` mounts the blueprints inside a bigger Flask app.
The role rules (`admin` / `editor` / `viewer`) are identical in all three.

## Demo data

The camp in every screenshot in [pruvodce.md](pruvodce.md) is generated, not real:

```bash
uv run flask --app wsgi seed-demo --out demo/demo.sqlite
DATABASE_URL="sqlite:///$PWD/demo/demo.sqlite" uv run flask --app wsgi run
```

Log in as `org` (editor) or `admin`, password `demo1234`. The generator
([../camp_planner/demo_data.py](../camp_planner/demo_data.py)) is deterministic — the same
`--seed` rebuilds the same camp.

To re-shoot the screenshots after a UI change:

```bash
uv pip install -e '.[docs]' && uv run playwright install chromium
uv run python scripts/shoot_docs.py
```

Note that a freshly seeded database has no Google event ids. If the demo camp was already
pushed to a calendar, re-seeding orphans those events; delete them before pushing again,
or the next pull offers all of them back as import candidates.
