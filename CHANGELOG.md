# Changelog

All notable changes to Camp Planner are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Categories: **Added** / **Changed** / **Fixed** / **Removed** / **Deprecated** / **Security**.

Versioning convention: a release that ships a new DB migration should bump the
**minor** version (used since 0.2.0).

## [Unreleased]

### Added

- Google sync panel shows when changes were last pulled from Google
  („Naposledy načteno z Google“).
- Google import: selected changes that vanished between preview and apply are
  reported as „přeskočeno“.

### Security

- Production refuses to start with the built-in default `SECRET_KEY` and marks
  the session cookie `Secure`/`SameSite=Lax`.
- `ProxyFix` (trusting `X-Forwarded-*`) is now opt-in via `BEHIND_PROXY=1`.
  **Upgrade note:** a deployment served under a reverse-proxy path prefix must now
  set `BEHIND_PROXY=1` — otherwise `url_for` drops the prefix and CSS/JS/links break
  (previously ProxyFix was always on). The app logs a warning if it sees
  `X-Forwarded-*` headers while `BEHIND_PROXY` is unset.

### Fixed

- PATCH endpoints: explicit `null` for a required field is a 400, not a 500.
- Embedded auth: a malformed grant from the host callback is skipped with a
  warning instead of turning every request into a 500.
- Timeline save: slots outside the camp's day range are rejected (they used to
  persist invisibly); seconds in spans are normalized to whole minutes. Google
  import likewise skips out-of-window events instead of importing them clipped.

### Removed

- Dead columns and code (**adds a DB migration**): `camps.google_sync_token`,
  leftover `seed-demo` CLI helpers, and the unused `Tag.activities` proxy.

### Changed

- Google push failures show a short Czech explanation instead of the raw
  English API error (which stays in the server log).
- `materials.acquisition_labels` is now NOT NULL (existing `NULL` rows
  backfilled to `[]`).
- `DEV_USER` (header-less local dev for proxy auth mode) is now settable via
  the env var, e.g. `DEV_USER="dev admin"`.
- Timeline save: `rev` is required; the conflict-dialog overwrite is an explicit
  `force: true`.
- Camp settings: `PUT` → `PATCH /api/camps/<slug>`, all fields optional.

## [0.2.2] - 2026-07-13

### Fixed

- CSRF token no longer expires on a long-open page: it's refreshed proactively
  (every 30 min and on tab refocus), with a transparent refresh-and-retry as a
  fallback. New `GET /csrf-token` endpoint.

### Added

- Activity overview: a chronological sort mode grouping activities by camp day,
  with one row per main slot ordered by time (with trailing section of
  activities without main slots). Filters still apply; column sorting is
  disabled while active.

## [0.2.1] - 2026-07-04

### Changed

- Timeline: every slot now shows a hover tooltip (heading + time, then any assigned
  orgs), not only slots that already have organizers.
- Timeline: the filter row now shows the current time in the camp's timezone, with the
  timezone name in small grey when it differs from the viewer's own timezone. The
  current-time line is likewise positioned by the camp's wall clock.

## [0.2.0] - 2026-06-27

### Added

- Todos can now have assigned organizers (any number). _(DB migration)_
- Filtering and sorting of TODOs in the activity detail.
- Camp-wide TODOs overview page: a filterable, sortable table of every
  activity's todos showing status, activity, assigned orgs, due date and note.
- Material catalog items now carry free acquisition labels (e.g. `kup: mefisto`,
  `sklad: bedna K14`, `půjčit: jirka`) — any number per item, edited via a chips input
  with prefix autocomplete. _(DB migration)_
- Material catalog: per-item amount-aggregation strategy (sum, max). _(DB migration)_
- Resync all button on the Google Calendar settings tab.

### Changed

- Progress (percent) tag values can now be typed directly as a number.

### Fixed

- Google sync: a slot whose Google event vanished upstream (deleted, or a
  recurring-event instance Google cancelled when its series was re-timed) no
  longer loops on a 400 Bad Request forever. The drain now forgets the dead
  event id and re-creates the slot as a fresh standalone event.

## [0.1.4] - 2026-06-16

### Added

- Change-history tab for activities and the camp, backed by the audit log.
- Current-day time line drawn on the timeline.

### Changed

- Activities overview persists its active filter in the URL hash.
- Merges of materials or activities now saved as a "merge" action in the audit
  log (instead of a "change" action). _(DB migration)_

### Fixed

- Timeline day/night background now renders correctly while zooming.

## [0.1.3] - 2026-06-14

### Added

- Optional per-slot override name, used as the timeline label and Google
  Calendar event title. _(DB migration)_

### Changed

- Google sync batches outbound pushes (≤50 ops per HTTP round-trip).

## [0.1.2] - 2026-06-14

### Added

- Admins can delete camps from camp settings.

### Changed

- Header condensed into a single bar; camp list reworked.

## [0.1.1] - 2026-06-14

### Added

- Google sync: shared-calendar guards, foreign-slot import, and live status.

### Changed

- Concurrent Google sync drains are serialized with a per-camp advisory lock.
- `sync-google` CLI command has cleaner periodic logs.
- Shared frontend helpers (plural, tabHash).

## [0.1.0] - 2026-06-14

Initial tagged release. Flask + SQLAlchemy app for planning summer camps with
overlapping activities across days.

### Added

- DB model.
- Auth: identity contract, permissions, providers, and a standalone login UI.
- Two blueprints: server-rendered web UI (`main`) and a pydantic-validated JSON
  REST API (`api`, Swagger at `/apidoc/swagger`).
- Editable vis-timeline view with configurable slot types.
- Activity detail page, camp-wide materials overview, and camp-wide activity
  overview pages.
- Two-way Google Calendar sync (using Google service-account) with settings UI.
- Per-activity audit logs with structured `{field: [old, new]}` change diffs.
- Alembic DB migrations with runtime `DB_TABLE_PREFIX` support.
- Unit tests, README, and deployment docs.

[Unreleased]: https://github.com/setnicka/camp-planner/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/setnicka/camp-planner/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/setnicka/camp-planner/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/setnicka/camp-planner/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/setnicka/camp-planner/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/setnicka/camp-planner/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/setnicka/camp-planner/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/setnicka/camp-planner/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/setnicka/camp-planner/releases/tag/v0.1.0
