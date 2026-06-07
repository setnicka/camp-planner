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

from typing import TYPE_CHECKING, Any, Callable

from flask import current_app, g

import camp_planner.models  # noqa: F401  (register mappers on the shared Base)
from camp_planner.auth import permissions
from camp_planner.auth.callback import CallbackProvider
from camp_planner.auth.identity import ANONYMOUS
from camp_planner.auth.standalone import StandaloneProvider
from camp_planner.auth.standalone import bp as auth_bp
from camp_planner.extensions import db
from camp_planner.views import bp as main_bp

if TYPE_CHECKING:
    from flask import Blueprint, Flask

    from camp_planner.auth.identity import AuthProvider


def _state() -> dict[str, Any]:
    return current_app.extensions["camp_planner"]


def _load_identity() -> None:
    g.identity = _state()["provider"].load_identity() or ANONYMOUS


def _inject() -> dict[str, Any]:
    return {
        "layout": _state()["base_template"],
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
) -> None:
    app.extensions["camp_planner"] = {"provider": provider, "base_template": base_template}
    if login_endpoint:
        app.config["AUTH_LOGIN_ENDPOINT"] = login_endpoint
    for bp in blueprints:
        _wire_blueprint(bp)
        app.register_blueprint(bp, url_prefix=url_prefix)


def wire_app(app: Flask) -> None:
    """Wire Camp Planner onto our own app: pick the provider by AUTH_MODE and
    register the blueprints + request/template hooks. Called by create_app."""
    mode = app.config["AUTH_MODE"]
    blueprints = [main_bp]
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
    base_template: str = "_layouts/bare.html",
) -> None:
    """Mount Camp Planner's blueprints on a host Flask app (embedded mode).

    auth_callback supplies the current identity (see auth.callback). Our own
    SQLAlchemy instance binds to the host app and shares its SQLALCHEMY_DATABASE_URI
    (table prefix avoids clashes); pass database_uri only if the host sets none.
    Pass base_template (e.g. the host's base) to wrap our pages in its chrome.
    """
    if database_uri:
        host_app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_uri)
    db.init_app(host_app)

    _attach(
        host_app,
        [main_bp],
        CallbackProvider(auth_callback),
        base_template=base_template,
        url_prefix=url_prefix,
    )
