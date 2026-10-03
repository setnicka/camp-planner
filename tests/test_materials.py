from __future__ import annotations

import pytest

from tests.conftest import (
    ADMIN,
    get_json,
    make_material,
    ok,
)


# --- materials ---------------------------------------------------------------

def test_material_catalog_create_list_and_dedup(client, seeded):
    slug = seeded["slug"]
    material = make_material(client, slug, note="bílý", url="https://shop/a4")
    assert material["name"] == "A4 papír" and material["url"] == "https://shop/a4"

    mats = get_json(client, f"/api/camps/{slug}/materials")["materials"]
    assert [m["name"] for m in mats] == ["A4 papír"] and mats[0]["note"] == "bílý"

    # a normalized-equal name can't create a second catalog row
    resp = client.post(f"/api/camps/{slug}/materials", json={"name": "papír A4"}, headers=ADMIN)
    assert resp.status_code == 400


def test_material_need_add_by_id(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    material_id = make_material(client, slug, unit="ks")["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": material_id, "amount": 10}, headers=ADMIN)
    n = ok(resp)["need"]
    assert n["amount"] == 10 and n["material"]["name"] == "A4 papír" and n["material"]["unit"] == "ks"

    # one catalog material at most once per activity
    dup = client.post(f"/api/activities/{aid}/materials", json={"material_id": material_id}, headers=ADMIN)
    assert dup.status_code == 400


@pytest.mark.parametrize("amount", [float("inf"), float("nan"), -5])
def test_material_need_amount_must_be_a_finite_number(client, seeded, amount):
    """json.dumps writes inf as the bare token Infinity, which no JSON parser reads back:
    one such need would blank every page that renders from an inlined payload."""
    slug, aid = seeded["slug"], seeded["activity_id"]
    material_id = make_material(client, slug)["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": material_id, "amount": amount}, headers=ADMIN)
    assert resp.status_code == 422

    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": material_id, "amount": 3}, headers=ADMIN)
    need_id = ok(resp)["need"]["id"]
    patched = client.patch(f"/api/material-needs/{need_id}", json={"amount": amount}, headers=ADMIN)
    assert patched.status_code == 422
    assert get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"][0]["amount"] == 3


def test_material_merge_migrates_usages_and_pins_unit(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src = make_material(client, slug, "papír", unit="ks")["id"]
    dst = make_material(client, slug, "kancelářský papír", unit="balení")["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": src, "amount": 5}, headers=ADMIN)

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert ok(resp)["material"]["id"] == dst

    mats = get_json(client, f"/api/camps/{slug}/materials")["materials"]
    assert src not in [m["id"] for m in mats]
    needs = get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    # the usage keeps its effective unit: the source's default, pinned as an override
    assert len(needs) == 1
    assert needs[0]["material"]["id"] == dst and needs[0]["unit"] == "ks"


def _two_materials_needed(client, slug, aid, *, src_unit="ks"):
    """Two catalog materials, both needed by the activity (50 dst + 20 src); returns (src, dst)."""
    dst = make_material(client, slug, "papíry", unit="ks")["id"]
    src = make_material(client, slug, "papíry A4", unit=src_unit)["id"]
    client.post(f"/api/activities/{aid}/materials", json={"material_id": dst, "amount": 50}, headers=ADMIN)
    client.post(f"/api/activities/{aid}/materials", json={"material_id": src, "amount": 20}, headers=ADMIN)
    return src, dst


def test_material_merge_sums_amounts_when_activity_uses_both(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src, dst = _two_materials_needed(client, slug, aid)

    ok(client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN))

    needs = get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert len(needs) == 1
    assert needs[0]["material"]["id"] == dst
    assert needs[0]["amount"] == 70


def test_material_delete_waits_for_its_needs(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]

    def catalog():
        return [m["id"] for m in get_json(client, f"/api/camps/{slug}/materials")["materials"]]

    mid = make_material(client, slug)["id"]
    resp = client.post(f"/api/activities/{aid}/materials",
                       json={"material_id": mid, "amount": 3}, headers=ADMIN)
    need_id = ok(resp)["need"]["id"]

    resp = client.delete(f"/api/camps/{slug}/materials/{mid}", headers=ADMIN)
    assert resp.status_code == 400 and "nelze smazat" in resp.get_json()["error"]
    assert mid in catalog()
    needs = get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]
    assert [n["material"]["id"] for n in needs] == [mid]

    ok(client.delete(f"/api/material-needs/{need_id}", headers=ADMIN))
    assert ok(client.delete(f"/api/camps/{slug}/materials/{mid}", headers=ADMIN))["id"] == mid
    assert mid not in catalog()


def test_material_merge_fails_on_unit_mismatch(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    src, dst = _two_materials_needed(client, slug, aid, src_unit="balení")

    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    assert resp.status_code == 400
    # nothing changed: both materials and both needs survive for manual fixing
    mats = [m["id"] for m in get_json(client, f"/api/camps/{slug}/materials")["materials"]]
    assert src in mats and dst in mats
    assert len(get_json(client, f"/api/activities/{aid}")["activity"]["material_needs"]) == 2


# --- catalog material update + overview --------------------------------------

def test_material_update_fields(client, seeded):
    slug = seeded["slug"]
    mid = make_material(client, slug, "papír", unit="ks")["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}",
                        json={"name": "kancelářský papír", "unit": "balení", "note": "A4"}, headers=ADMIN)
    m = ok(resp)["material"]
    assert (m["name"], m["unit"], m["note"]) == ("kancelářský papír", "balení", "A4")


def test_material_acquisition_labels_and_orgs(client, seeded):
    slug, oid = seeded["slug"], seeded["org_id"]
    mid = make_material(client, slug, "lano")["id"]
    assert make_material(client, slug, "provázek")["acquisition_labels"] == []

    def patch(**body):
        resp = client.patch(f"/api/camps/{slug}/materials/{mid}", json=body, headers=ADMIN)
        return ok(resp)["material"]

    # blanks dropped, duplicates removed, order preserved
    m = patch(acquisition_labels=["půjčit: jirka", "  ", "půjčit: kačka", "půjčit: jirka"],
              org_ids=[oid])
    assert m["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert m["orgs"] == [{"org_id": oid, "initials": "K"}]

    # the overview serializer carries the same catalog fields (shared via _material())
    ov = next(x for x in get_json(client, f"/api/camps/{slug}/materials/overview")["materials"]
              if x["id"] == mid)
    assert ov["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert ov["orgs"] == [{"org_id": oid, "initials": "K"}]

    # not sent and an explicit null both leave a field unchanged; [] clears
    m = patch(org_ids=[])
    assert m["orgs"] == [] and m["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert patch(acquisition_labels=None)["acquisition_labels"] == ["půjčit: jirka", "půjčit: kačka"]
    assert patch(acquisition_labels=[])["acquisition_labels"] == []


def test_material_merge_carries_labels_and_orgs(client, seeded):
    slug, oid = seeded["slug"], seeded["org_id"]
    src = make_material(client, slug, "lano")["id"]
    dst = make_material(client, slug, "provaz")["id"]
    client.patch(f"/api/camps/{slug}/materials/{dst}",
                 json={"acquisition_labels": ["kup: mefisto"], "org_ids": [oid]}, headers=ADMIN)
    client.patch(f"/api/camps/{slug}/materials/{src}",
                 json={"acquisition_labels": ["kup: mefisto", "sklad: K14"], "org_ids": [oid]}, headers=ADMIN)
    resp = client.post(f"/api/camps/{slug}/materials/{src}/merge", json={"into": dst}, headers=ADMIN)
    m = ok(resp)["material"]
    assert m["id"] == dst
    assert m["acquisition_labels"] == ["kup: mefisto", "sklad: K14"]   # union, target first
    assert m["orgs"] == [{"org_id": oid, "initials": "K"}]              # not doubled


def test_material_sum_strategy(client, seeded):
    m = make_material(client, seeded["slug"], "projektor")
    assert m["sum_strategy"] == "sum"
    url = f"/api/camps/{seeded['slug']}/materials/{m['id']}"

    def patch(**body):
        return client.patch(url, json=body, headers=ADMIN)

    assert ok(patch(sum_strategy="max"))["material"]["sum_strategy"] == "max"
    assert ok(patch(unit="ks"))["material"]["sum_strategy"] == "max"
    assert patch(sum_strategy="avg").status_code == 422


def test_material_update_rename_collision(client, seeded):
    slug = seeded["slug"]
    make_material(client, slug, "papír")
    mid = make_material(client, slug, "lepidlo")["id"]
    resp = client.patch(f"/api/camps/{slug}/materials/{mid}", json={"name": "papír"}, headers=ADMIN)
    assert resp.status_code == 400 and "už v katalogu" in resp.get_json()["error"]


def test_material_overview_lists_usages(client, seeded):
    slug, aid = seeded["slug"], seeded["activity_id"]
    mid = make_material(client, slug, "papír", unit="ks")["id"]
    client.post(f"/api/activities/{aid}/materials",
                json={"material_id": mid, "amount": 30, "is_ready": True}, headers=ADMIN)
    m = next(x for x in get_json(client, f"/api/camps/{slug}/materials/overview")["materials"]
             if x["id"] == mid)
    assert len(m["usages"]) == 1
    u = m["usages"][0]
    assert u["activity_id"] == aid and u["amount"] == 30 and u["is_ready"] is True
    assert u["activity_title"] == "Akce"
