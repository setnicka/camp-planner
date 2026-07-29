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
from camp_planner.api import bp as api_bp
from camp_planner.auth import permissions
from camp_planner.auth.callback import CallbackProvider
from camp_planner.auth.identity import ANONYMOUS
from camp_planner.auth.standalone import StandaloneProvider
from camp_planner.auth.standalone import bp as auth_bp
from camp_planner.extensions import db
from camp_planner.version import __version__
from camp_planner.views import bp as main_bp

if TYPE_CHECKING:
    from flask import Blueprint, Flask

    from camp_planner.auth.identity import AuthProvider


# Our shells: the standalone page, and the embedded fragment (the default when a host names
# no template of its own).
_BARE_TEMPLATE = "_layouts/bare.html"
_FULL_TEMPLATE = "_layouts/full.html"


# Slots every shell must declare: our pages' markup, stylesheets and scripts render into
# these. Checked at registration — a missing slot ships unstyled, inert pages.
_REQUIRED_BLOCKS = ("content", "cp_head", "cp_scripts")


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
            "(place it in <head>) and its scripts into `cp_scripts` (before </body>); a missing "
            "slot means those pages ship without them. Link css/content.css there too. "
            "See docs/DEPLOYMENT.md §2.",
            RuntimeWarning,
            stacklevel=3,
        )


def _state() -> dict[str, Any]:
    return current_app.extensions["camp_planner"]


def _load_identity() -> None:
    # A Bearer token may already have resolved the identity on the api blueprint
    # (see api._api_token_auth, which stashes g.api_token); otherwise the configured
    # provider takes over.
    if g.get("api_token") is None:
        g.identity = _state()["provider"].load_identity() or ANONYMOUS


def _inject() -> dict[str, Any]:
    base_template = _state()["base_template"]
    return {
        "layout": base_template,
        "app_version": __version__,
        # Anything but our standalone shell means we're inside someone else's page, and our
        # output needs the .cp-embed wrapper page.html puts around it.
        "embedded": base_template != _FULL_TEMPLATE,
        # "light" | "dark" | "auto" forces the theme and drops the switch; None = the
        # visitor chooses (and standalone then defaults to auto, embedded to light).
        "force_theme": _state()["force_theme"],
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
    shared across apps, so re-registering would stack duplicate hooks)."""
    if bp in _wired:
        return
    _wired.add(bp)
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
    base_template: str = _BARE_TEMPLATE,
    force_theme: str | None = None,
) -> None:
    """Mount Camp Planner's blueprints on a host Flask app (embedded mode).

    auth_callback supplies the current identity (see auth.callback). Our own
    SQLAlchemy instance binds to the host app and shares its SQLALCHEMY_DATABASE_URI
    (table prefix avoids clashes); pass database_uri only if the host sets none.
    Pass base_template (e.g. the host's base) to wrap our pages in its chrome.

    force_theme ("light" | "dark" | "auto") pins the theme and drops the switch; "auto"
    is for a host page that itself follows prefers-color-scheme (we can't read your
    background, so we only follow the OS when you say so). None = the visitor chooses,
    starting light. Works with a custom base_template too. See docs/DEPLOYMENT.md §2.
    """
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
    )
