"""Proxy-auth header parsing — focus on the X-Remote-Name charset round-trip.

HTTP headers carry only latin-1, but org display names are UTF-8 (Czech
diacritics), so the proxy percent-encodes X-Remote-Name and ProxyProvider
unquotes it. A plain ASCII name (no '%') must pass through untouched.
"""

from __future__ import annotations

from urllib.parse import quote

from camp_planner.auth.proxy import ProxyProvider


def _identity(app, headers):
    with app.test_request_context(headers=headers):
        return ProxyProvider().load_identity()


def test_remote_name_percent_decoded(app):
    name = "Jiří Setnička"
    ident = _identity(app, {"X-Remote-User": "setnicka",
                            "X-Remote-Roles": "admin",
                            "X-Remote-Name": quote(name)})
    assert ident.display_name == name
    assert ident.is_admin


def test_plain_ascii_name_unchanged(app):
    ident = _identity(app, {"X-Remote-User": "bob",
                            "X-Remote-Roles": "editor:*",
                            "X-Remote-Name": "Bob Plain"})
    assert ident.display_name == "Bob Plain"


def test_missing_name_falls_back_to_user_id(app):
    ident = _identity(app, {"X-Remote-User": "alice", "X-Remote-Roles": ""})
    assert ident.display_name == "alice"
