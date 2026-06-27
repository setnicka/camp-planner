# Changelog

All notable changes to Camp Planner are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Categories: **Added** / **Changed** / **Fixed** / **Removed** / **Deprecated** / **Security**.

Versioning convention: a release that ships a new DB migration should bump the
**minor** version (used since 0.2.0).

## [Unreleased]

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

[Unreleased]: https://github.com/setnicka/camp-planner/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/setnicka/camp-planner/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/setnicka/camp-planner/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/setnicka/camp-planner/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/setnicka/camp-planner/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/setnicka/camp-planner/releases/tag/v0.1.0
