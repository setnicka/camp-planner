"""Integration seam: attach Camp Planner's blueprints to a Flask app.

Two entrypoints:
  - wire_app(app) — used for standalone/proxy mode (auth provider chosen by AUTH_MODE)
  - register_camp_planner(host_app, ...) — mount on a host Flask app (embedded);
    identity comes from the host's auth_callback.

The wiring is blueprint-scoped: a per-blueprint before_request loads g.identity and
a per-blueprint context_processor injects the page layout + auth helpers. Both read
per-app state from app.extensions["camp_planner"], so the same blueprint object works
across apps and a host's own routes/templates are untouched.
"""

from __future__ import annotations

import re
import warnings
from typing import TYPE_CHECKING, Any, Callable

from flask import current_app, g

import camp_planner.models  # noqa: F401  (register mappers on the shared Base)
from camp_planner.api import api_token_auth
from camp_planner.api import bp as api_bp
from camp_planner.auth import permissions
from camp_planner.auth.callback import CallbackProvider
from camp_planner.auth.identity import ANONYMOUS
from camp_planner.auth.standalone import StandaloneProvider
from camp_planner.auth.standalone import bp as auth_bp
from camp_planner.extensions import db, db_session, state
from camp_planner.version import __version__
from camp_planner.views import bp as main_bp

if TYPE_CHECKING:
    from flask import Blueprint, Flask
    from sqlalchemy.orm import Session

    from camp_planner.auth.identity import AuthProvider


# Our shells: the standalone page, and the embedded fragment (the default when a host names
# no template of its own).
_BARE_TEMPLATE = "_layouts/bare.html"
_FULL_TEMPLATE = "_layouts/full.html"


# Slots every shell must declare: our pages' markup, stylesheets, scripts and
# section links render into these. Checked at registration (a missing slot ships
# pages that are unstyled, inert or impossible to navigate).
_REQUIRED_BLOCKS = ("content", "cp_head", "cp_scripts", "cp_nav")


def _check_host_template(app: Flask, base_template: str) -> None:
    """Warn when a host's base template declares none of the slots our pages render into.

    Static, so it only sees blocks written in that file: a template that itself extends another
    is skipped rather than guessed at. Unreadable templates are skipped too — a host may install
    its loader after registering us, and a check must not be the thing that breaks startup.
    """
    try:
        source = app.jinja_env.loader.get_source(app.jinja_env, base_template)[0]
    except Exception:
        return
    if re.search(r"{%-?\s*extends\b", source):
        return
    missing = [b for b in _REQUIRED_BLOCKS
               if not re.search(r"{%-?\s*block\s+" + b + r"\b", source)]
    if missing:
        warnings.warn(
            f"base_template={base_template!r} declares no {' / '.join(missing)} block. Camp "
            "Planner renders each page's markup into `content`, its stylesheets into `cp_head` "
            "(place it in <head>), its scripts into `cp_scripts` (before </body>) and the links "
            "between a camp's sections into `cp_nav`; a missing slot means those pages ship "
            "without them. Link css/content.css there too. See docs/DEPLOYMENT.md §2.",
            RuntimeWarning,
            stacklevel=3,
        )


def _check_session_contract() -> None:
    """Refuse to run on an injected session the host left mid-transaction: our services
    commit mid-request, which would end a transaction the host still believes is open.
    Reads local state only, no round-trip."""
    if state().get("session") is None:      # ours; nobody else to disturb
        return
    if db_session.in_transaction():
        raise RuntimeError(
            "camp_planner: the injected session has an active transaction at request "
            "entry; the host must commit/rollback before planner views run (autobegin "
            "means even a bare SELECT opens one)")


def _load_identity() -> None:
    # A Bearer token may already have resolved the identity on the api blueprint
    # (see api.api_token_auth, which stashes g.api_token); otherwise the configured
    # provider takes over.
    if g.get("api_token") is None:
        g.identity = state()["provider"].load_identity() or ANONYMOUS


def _inject() -> dict[str, Any]:
    base_template = state()["base_template"]
    return {
        "layout": base_template,
        "app_version": __version__,
        # Anything but our standalone shell means we're inside someone else's page, and our
        # output needs the .cp-embed wrapper page.html puts around it.
        "embedded": base_template != _FULL_TEMPLATE,
        # "light" | "dark" | "auto" forces the theme and drops the switch; None = the
        # visitor chooses (and standalone then defaults to auto, embedded to light).
        "force_theme": state()["force_theme"],
        "identity": g.get("identity", ANONYMOUS),
        # standalone only (we own login/logout); lets templates skip url_for('auth.*').
        "auth_enabled": bool(current_app.config.get("AUTH_LOGIN_ENDPOINT")),
        "can_view": permissions.can_view,
        "can_edit": permissions.can_edit,
        "can_edit_camp_meta": permissions.can_edit_camp_meta,
        "can_create_camp": permissions.can_create_camp,
        "can_manage_users": permissions.can_manage_users,
    }


_wired: set[Blueprint] = set()


def _wire_blueprint(bp: Blueprint) -> None:
    """Register our hooks once per blueprint (they're module-level singletons
    shared across apps, so re-registering would stack duplicate hooks).

    Registration order is the hook order, and it is load-bearing: the contract check
    must see the session before anything of ours queries it, and _load_identity defers
    to the token api_token_auth resolves. Hence api.py registers no hook of its own.
    """
    if bp in _wired:
        return
    _wired.add(bp)
    bp.before_request(_check_session_contract)
    if bp is api_bp:
        bp.before_request(api_token_auth)
    bp.before_request(_load_identity)
    bp.context_processor(_inject)


def _attach(
    app: Flask,
    blueprints: list[Blueprint],
    provider: AuthProvider,
    *,
    base_template: str,
    login_endpoint: str | None = None,
    url_prefix: str | None = None,
    force_theme: str | None = None,
    session: Session | Callable[[], Session] | None = None,
) -> None:
    # An explicit argument (embedded) wins over the CP_FORCE_THEME env var (standalone/
    # proxy). Kept in our own extensions state, not app.config: embedded, that dict belongs
    # to the host and a key of ours could collide with one of theirs.
    if force_theme is None:
        force_theme = app.config.get("CP_FORCE_THEME")
    if force_theme not in (None, "", "light", "dark", "auto"):
        raise ValueError(
            f"force_theme={force_theme!r}: expected 'light', 'dark', 'auto' or None "
            "(None = show the switch and let the visitor choose)"
        )
    _check_host_template(app, base_template)
    app.extensions["camp_planner"] = {
        "provider": provider,
        "base_template": base_template,
        "force_theme": force_theme or None,
        # None = ours; else a callable giving the host's, normalized once here.
        "session": session if session is None or callable(session) else lambda: session,
    }
    if login_endpoint:
        app.config["AUTH_LOGIN_ENDPOINT"] = login_endpoint
    for bp in blueprints:
        _wire_blueprint(bp)
        # A blueprint with its own url_prefix (e.g. the API's /api) nests under the
        # mount point, so embedded mode gets /planner/api while standalone gets /api.
        prefix = (url_prefix or "") + bp.url_prefix if bp.url_prefix else url_prefix
        app.register_blueprint(bp, url_prefix=prefix)


def wire_app(app: Flask) -> None:
    """Wire Camp Planner onto our own app: pick the provider by AUTH_MODE and
    register the blueprints + request/template hooks. Called by create_app."""
    mode = app.config["AUTH_MODE"]
    blueprints = [main_bp, api_bp]
    login_endpoint = None
    if mode == "standalone":
        provider: AuthProvider = StandaloneProvider()
        blueprints.append(auth_bp)
        login_endpoint = "auth.login"
    elif mode == "proxy":
        from camp_planner.auth.proxy import ProxyProvider

        provider = ProxyProvider(dev_user=app.config.get("DEV_USER"))
    else:
        raise ValueError(f"Unknown AUTH_MODE={mode!r}; expected 'standalone' or 'proxy'")
    _attach(
        app,
        blueprints,
        provider,
        base_template=app.config["BASE_TEMPLATE"],
        login_endpoint=login_endpoint,
    )


def register_camp_planner(
    host_app: Flask,
    *,
    auth_callback: Callable[[], Any],
    url_prefix: str = "/planner",
    database_uri: str | None = None,
    session: Session | Callable[[], Session] | None = None,
    base_template: str = _BARE_TEMPLATE,
    force_theme: str | None = None,
) -> None:
    """Mount Camp Planner's blueprints on a host Flask app (embedded mode).

    auth_callback supplies the current identity (see auth.callback). Our own
    SQLAlchemy instance binds to the host app and shares its SQLALCHEMY_DATABASE_URI
    (table prefix avoids clashes); pass database_uri only if the host sets none.
    Pass base_template (e.g. the host's base) to wrap our pages in its chrome.

    session is optional: pass the host's own session, normally its scoped_session and
    never a sessionmaker, and we run on it instead of an engine of our own (which rules
    out database_uri). It must then carry no open transaction when a request starts, or
    we raise at request entry, and its cleanup stays the host's. See docs/DEPLOYMENT.md §2.

    force_theme ("light" | "dark" | "auto") pins the theme and drops the switch; "auto"
    is for a host page that itself follows prefers-color-scheme (we can't read your
    background, so we only follow the OS when you say so). None = the visitor chooses,
    starting light. Works with a custom base_template too. See docs/DEPLOYMENT.md §2.
    """
    if session is not None and database_uri:
        raise ValueError(
            "session and database_uri are mutually exclusive: with an injected session we "
            "open no connection of our own, so there is nothing to point at a URI")
    if session is None:
        # init_app is what gives us an engine, a pool and a teardown of our own.
        if database_uri:
            host_app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_uri)
        db.init_app(host_app)

    _attach(
        host_app,
        [main_bp, api_bp],
        CallbackProvider(auth_callback),
        base_template=base_template,
        url_prefix=url_prefix,
        force_theme=force_theme,
        session=session,
    )
