"""Product areas a deployment may switch off: CP_DISABLED_FEATURES (standalone/proxy) or
register_camp_planner(disabled_features=...). Every feature is on unless named there.

Kept in app.extensions["camp_planner"], not app.config: embedded, that dict is the host's.
"""

from __future__ import annotations

from typing import Iterable

from flask import abort, request

from camp_planner.extensions import state

# inventory: the warehouse pages and API, and the camp materials' link into it.
FEATURES = ("inventory",)


def parse(names: str | Iterable[str] | None) -> frozenset[str]:
    """The disabled set, from a comma/space separated string or an iterable. An unknown name
    fails startup: a typo would otherwise leave the feature quietly on."""
    if isinstance(names, str):
        names = names.replace(",", " ").split()
    disabled = frozenset(names or ())
    unknown = disabled - set(FEATURES)
    if unknown:
        raise ValueError(
            f"Unknown feature(s) {', '.join(sorted(unknown))}; expected any of "
            f"{', '.join(FEATURES)}")
    return disabled


def enabled(name: str) -> bool:
    return name not in state().get("disabled_features", ())


def gate() -> None:
    """before_request: an endpoint named after a feature (main.inventory_box,
    api.inventory_item_list, …) is that feature's and answers 404 while it is off, ahead of
    any auth or body validation, so a visitor cannot tell it from a missing page."""
    endpoint = (request.endpoint or "").partition(".")[2]
    if any(endpoint.startswith(name + "_") for name in state().get("disabled_features", ())):
        abort(404)
