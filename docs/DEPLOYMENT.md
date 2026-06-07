# Deployment & integration

Camp Planner can run in three different deployment modes. They differ by how it
learns **who the current user is** and **what they may do**. The rest of the app
is identical: every mode resolves the request into one `Identity` (a `user_id`
for the audit log, an `is_admin` flag, per-camp role grants); the same
permission rules apply on top.

| Mode           | Who authenticates           | Identity source              | Users table |
| -------------- | --------------------------- | ---------------------------- | ----------- |
| **standalone** | Camp Planner                | Flask session (own login)    | used        |
| **embedded**   | a host Flask app            | `auth_callback`              | empty       |
| **proxy**      | an existing app (any stack) | trusted `X-Remote-*` headers | empty       |

**Roles:**

- **`admin`** — global: create camps, manage users, edit anything (incl. name/slug).
- **`editor`** — edit within a camp *except* its name/slug.
- **`viewer`** — read-only.

`editor`/`viewer` are scoped to specific camps or unscoped (all); editor implies
viewer. The users table exists in every deployment but stays empty under proxy/embedded.

## 1. Standalone

The default mode. The app owns its users and renders full pages including the
simple login page. Uses SQLite by default, different database could be set by
`DB_BACKEND=postgresql|mysql` with `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`,
or a single `DATABASE_URL`.

```bash
# .env
AUTH_MODE=standalone
SECRET_KEY=<random>
DB_BACKEND=sqlite        # or postgresql / mysql (install the [postgres] / [mysql] extra)
```

```bash
uv run flask --app wsgi db upgrade               # schema
uv run flask --app wsgi create-user alice --admin   # first admin (prompts for a password)
uv run flask --app wsgi create-user bob --grant editor:12 --grant viewer:*
uv run flask --app wsgi run                       # /auth/login, then /
```

Grants are `role:scope` — `editor:12` (one camp) or `viewer:*` (all). Once the first
admin exists, admins can create/delete users in-app at `/auth/users`; `grant-role` on
the CLI still manages per-camp grants. In production run `gunicorn wsgi:app` with
`FLASK_ENV=production`.

## 2. Embedded in a Flask app

The host registers Camp Planner's blueprints on its own app; pages share the host's
request/Jinja env and wrap in its layout. Identity comes from a callback.

Camp Planner keeps its own `SQLAlchemy` instance (same database; the table
prefix avoids name clashes).

```python
import os
os.environ.setdefault("DB_TABLE_PREFIX", "cp_")   # set BEFORE importing camp_planner (see below)

from camp_planner import register_camp_planner

register_camp_planner(
    app,
    auth_callback=current_identity,   # () -> dict | Identity | None  (None = anonymous)
    url_prefix="/planner",
    base_template="base.html",        # host template with {% block content %}; omit for a bare fragment
)
```

The callback returns `None`, or a dict (so the host needn't import our internals):

```python
{"user_id": "123", "display_name": "Foo Bar", "is_admin": False,
 "grants": [{"role": "editor", "camps": ["smf-2026", "letni-tabor"]},
            {"role": "viewer", "camps": "all"}]}
```

`camps` is a list of camp **slugs** (or `"all"`), resolved to ids per request; unknown
slugs are ignored — same convention as the proxy header.

- **Database:** Camp Planner binds its own SQLAlchemy to the host app and shares the
  host's `SQLALCHEMY_DATABASE_URI` (pass `database_uri=` only if the host sets none).
  Run its migrations once against that database.
- **Table prefix:** set `DB_TABLE_PREFIX` (e.g. `cp_`) so its tables don't clash. It's
  read at *import* time (table names are fixed then), so set it **before importing
  `camp_planner`** — in this module as shown, or in the environment. It can't be a
  `register_camp_planner()` argument: by then the models are already named.
- Auth hooks are blueprint-scoped — they never touch the host's other routes.
- **Styles:** link `css/content.css` (the `.cp-*` component styles for our forms/tables)
  from the host's template — e.g. `url_for("main.static", filename="css/content.css")`.
  Do *not* link `css/standalone.css`; that's our own shell's chrome (nav/body reset) and
  would fight the host's layout.
- **CSRF:** Camp Planner only enables its own `CSRFProtect` on *its own* app (standalone/
  proxy), never on the host — so CSRF for the mounted routes is the host's responsibility.
  A host using Flask-WTF's `CSRFProtect` covers Camp Planner's POSTs automatically and
  exposes the `csrf_token()` template global our forms use; a host with a different CSRF
  scheme must supply its own token to those forms. (Camp Planner has no forms of its own
  in embedded mode — login/logout exist only in standalone.)

## 3. Proxy (behind nginx, any-stack app)

Camp Planner runs as its own localhost process. The existing app authenticates; nginx
asks it "who is this?" via `auth_request` and forwards the answer as trusted
`X-Remote-*` headers — trusted only because nginx is the sole route to it and
overwrites any client-supplied ones.

```bash
# .env (bind to localhost only)
AUTH_MODE=proxy
SECRET_KEY=<random>
DB_BACKEND=mysql  DB_HOST=localhost  DB_NAME=existing_app_db  DB_USER=camp  DB_PASSWORD=secret
DB_TABLE_PREFIX=cp_
```

```bash
uv run gunicorn --bind 127.0.0.1:8000 wsgi:app
```

The auth-check is a plain `proxy_pass`, so the backend can be any HTTP app:

```nginx
upstream camp_planner { server 127.0.0.1:8000; }

location = /_planner_auth {
    internal;
    proxy_pass http://127.0.0.1:9000/auth/planner_check;  # the existing app's auth-check URL
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";                    # required: else POSTs hang (body stripped, but length still sent)
    proxy_set_header X-Original-URI $request_uri;          # Cookie is forwarded by default
}

location /planner/ {
    auth_request /_planner_auth;
    auth_request_set $u     $upstream_http_x_remote_user;
    auth_request_set $name  $upstream_http_x_remote_name;
    auth_request_set $roles $upstream_http_x_remote_roles;
    auth_request_set $login $upstream_http_x_login_url;

    proxy_set_header X-Remote-User      $u;
    proxy_set_header X-Remote-Name      $name;
    proxy_set_header X-Remote-Roles     $roles;
    proxy_set_header X-Forwarded-Prefix /planner;
    proxy_set_header X-Forwarded-Proto  $scheme;   # https awareness (secure cookies, external URLs)
    proxy_set_header X-Forwarded-Host   $host;      # real public host (proxy_pass would override Host)
    proxy_pass http://camp_planner/;        # trailing slash strips /planner/

    error_page 401 = @planner_login;
}
location @planner_login { return 302 $login; }
```

Auth-check endpoint (PHP example — reads the app's session, replies 200+headers or
401+login URL):

```php
<?php session_start();
if (empty($_SESSION['user'])) {
    header('X-Login-URL: /login?return=' . rawurlencode($_SERVER['HTTP_X_ORIGINAL_URI'] ?? '/'));
    http_response_code(401); exit;
}
$u = $_SESSION['user'];
header('X-Remote-User: '  . $u['username']);
// percent-encode the name: HTTP headers are latin-1 only, names may be UTF-8
header('X-Remote-Name: '  . rawurlencode($u['display_name']));
// roles: space-separated  admin | editor:* | viewer:smf-2026 | editor:smf-2026,letni-tabor
// scopes are camp slugs (resolved to ids per request; unknown slugs are ignored)
header('X-Remote-Roles: ' . ($u['is_admin'] ? 'admin' : 'editor:* viewer:*'));
```

Notes:

- The endpoint returns **200 or 401**, never a 3xx (`auth_request` errors on it); the
  browser redirect is the `error_page 401` line.
- The trailing slash on `proxy_pass …/` strips `/planner/`; `X-Forwarded-Prefix`,
  `-Proto` and `-Host` (all honored by `ProxyFix` in [wsgi.py](../wsgi.py)) give the
  app its public prefix, scheme and host so `url_for` and cookies stay correct. Only
  `-Prefix` is strictly required for routing; `-Proto`/`-Host` matter behind HTTPS.
- Local dev without nginx: set `DEV_USER` in config, e.g. `{"user_id": "dev", "roles": "admin"}`.
