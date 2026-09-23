"""Feature switches: a switched off warehouse is gone from every page and endpoint, while
the links camp materials hold into it survive untouched."""

from __future__ import annotations

import json
import re

import pytest

from camp_planner import features
from camp_planner.extensions import db_session
from camp_planner.models.material import Material
from tests.conftest import ADMIN, make_item


def _off(app):
    app.extensions["camp_planner"]["disabled_features"] = features.parse("inventory")


def _page_urls(client, url) -> dict:
    html = client.get(url, headers=ADMIN).get_data(as_text=True)
    blob = re.search(r'<script id="cp-\w+-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(blob.group(1))["urls"]


def test_parse_accepts_a_list_and_refuses_an_unknown_name():
    assert features.parse("inventory, ") == {"inventory"}
    assert features.parse(None) == frozenset()
    with pytest.raises(ValueError, match="nope"):
        features.parse(["inventory", "nope"])


def test_every_warehouse_route_answers_404(app, client):
    _off(app)
    rules = [r for r in app.url_map.iter_rules() if "inventory" in r.endpoint]
    assert len(rules) > 20
    for rule in rules:
        url = rule.build({a: 1 for a in rule.arguments}, append_unknown=False)[1]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            resp = client.open(url, method=method, json={}, headers=ADMIN)
            assert resp.status_code == 404, (method, url)


def test_camp_pages_lose_the_warehouse(app, client, seeded, box):
    item = make_item(client, box["id"])
    resp = client.post(f"/api/camps/{seeded['slug']}/materials",
                       json={"inventory_item_id": item["id"]}, headers=ADMIN)
    material_id = resp.get_json()["material"]["id"]
    _off(app)

    assert ">Sklad<" not in client.get("/", headers=ADMIN).get_data(as_text=True)
    for url in (f"/camps/{seeded['slug']}/materials",
                f"/camps/{seeded['slug']}/activities/{seeded['activity_id']}"):
        assert "inventoryItems" not in _page_urls(client, url)
    listed = client.get(f"/api/camps/{seeded['slug']}/materials", headers=ADMIN).get_json()
    assert [m["inventory_item"] for m in listed["materials"]] == [None]

    # Neither made nor let go while hidden: a null would otherwise drop the unseen link.
    resp = client.patch(f"/api/camps/{seeded['slug']}/materials/{material_id}",
                        json={"inventory_item_id": None}, headers=ADMIN)
    assert resp.status_code == 400
    resp = client.post(f"/api/camps/{seeded['slug']}/materials",
                       json={"inventory_item_id": item["id"]}, headers=ADMIN)
    assert resp.status_code == 400
    assert db_session.get(Material, material_id).inventory_item_id == item["id"]

    # A null where there was nothing to let go changes nothing, so it passes.
    plain = client.post(f"/api/camps/{seeded['slug']}/materials", json={"name": "Papír"},
                        headers=ADMIN).get_json()["material"]
    resp = client.patch(f"/api/camps/{seeded['slug']}/materials/{plain['id']}",
                        json={"inventory_item_id": None}, headers=ADMIN)
    assert resp.status_code == 200

