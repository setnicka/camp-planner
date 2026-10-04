"""Proxy-auth header parsing, focused on the X-Remote-Name charset round-trip.

HTTP headers carry only latin-1, but org display names are UTF-8 (Czech
diacritics), so the proxy percent-encodes X-Remote-Name and ProxyProvider
unquotes it. A plain ASCII name (no '%') must pass through untouched.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from camp_planner.auth.proxy import ProxyProvider


@pytest.mark.parametrize("headers, name", [
    ({"X-Remote-User": "setnicka", "X-Remote-Roles": "admin",
      "X-Remote-Name": quote("Jiří Setnička")}, "Jiří Setnička"),
    ({"X-Remote-User": "bob", "X-Remote-Roles": "editor:*", "X-Remote-Name": "Bob Plain"},
     "Bob Plain"),
    ({"X-Remote-User": "alice", "X-Remote-Roles": ""}, "alice"),   # no name: the user id
])
def test_display_name(app, headers, name):
    with app.test_request_context(headers=headers):
        assert ProxyProvider().load_identity().display_name == name


@pytest.mark.parametrize("dev_user, is_admin", [("dev admin", True), ("dev", False)])
def test_dev_user_stands_in_for_the_headers(app, dev_user, is_admin):
    with app.test_request_context():
        ident = ProxyProvider(dev_user=dev_user).load_identity()
    assert (ident.user_id, ident.is_admin) == ("dev", is_admin)
