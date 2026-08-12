# Camp Planner — agent guide

Flask + SQLAlchemy planner for summer camps (overlapping activities across days).
`main` blueprint renders HTML, `api` blueprint is pydantic-validated JSON under `/api`.
Setup, architecture and data model: `README.md`.

## Commands

```bash
uv run pytest                              # tests
uv run ruff check camp_planner/ tests/    # lint (line-length 100, py311)
uv run flask --app wsgi db upgrade        # apply migrations
node --check camp_planner/static/js/X.js  # JS has no test runner, syntax-check only
```

## Non-obvious conventions

- The app runs in production: schema changes ship as migrations that are safe on a
  populated DB; breaking API or URL changes need a deliberate, announced release.
- A service performing a complete operation owns its transaction and commits (incl. its
  audit row via `audit.record`, which only stages); readers don't commit. Raise
  `errors.Invalid` for business failures. Never roll back before raising (request
  teardown discards the session); roll back only after a failed flush/commit.
- Naive datetimes stay naive: slot times are local wall-clock, day/window math is pure
  calendar arithmetic. Never convert through `ZoneInfo`; `camp.timezone` is display only.
- KISS the data model, but `Org.external_id` and `Activity.config`/external types are
  deliberate integration hooks, not dead code.
- Embedded mode (app can mount under a path prefix): build URLs only with `url_for`,
  never hardcode paths. CSRF travels as the `X-CSRFToken` header.
- Migrations: generate with `DB_TABLE_PREFIX` unset, then wrap new names with
  `table_name()` / `_fk()` / `_ix()`. Verify on a populated DB; empty DBs hide
  batch/FK failures.
- SemVer in `pyproject.toml`, tags `vX.Y.Z`. A release shipping a new migration bumps
  the minor version; patch is reserved for migration-free releases. `CHANGELOG.md`
  follows Keep a Changelog (English), noting which entries add a migration.

## Frontend

- Vanilla JS, no bundler; libs vendored as UMD globals. Timeline is vis-timeline only.
  Vendored CSS is never edited — restyle via overrides in our stylesheets.
- Pages read data from an inline JSON script (no fetch on load); mutations go through
  `cpDom.api`. Item api URLs carry a `0` sentinel the client swaps for the real id.
- Every colour is a `--cp-*` token in `content.css` — no literals (test-enforced).

## Language & style

- All user-facing strings are Czech; the day-grid editor is called „rozvrh“, not „osa“.
- Sort names with `czech_sort_key` server-side, `localeCompare(s, "cs")` client-side.
- Czech text uses the en dash (–); the em dash is banned everywhere, including comments,
  docstrings and commit messages.
- Comments, changelog and commit messages stay terse: keep the non-obvious why, drop the
  derivation. Commit messages are self-contained (no references to uncommitted docs or
  review-finding numbers). Czech documentation never addresses the reader in second person.
