from __future__ import annotations

import pytest

from tests.conftest import (
    ADMIN,
    editor,
    get_json,
    make_camp,
    make_material,
    ok,
    viewer,
)


_NEW_CAMP = {
    "name": "Letní tábor", "start_date": "2026-08-01", "length_days": 5,
    "timezone": "Europe/Prague", "window_start_min": 240, "snap_minutes": 15,
}


# --- landing ------------------------------------------------------------------------

def test_landing_page_renders_camp_rows(client, seeded):
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert "css/landing.css" in html
    assert "cp-camp-rows" in html and "cp-camp-row-name" in html
    assert "Úkoly" in html                                # row links come from the shared sections list
    assert "Tábor" in html
    assert "4. 7. 2026 – 6. 7. 2026" in html              # start_date.end_date via Camp.end_date
    assert "3 dny" in html                                # Czech plural for length_days


def test_landing_page_orders_newest_first(client, seeded):
    make_camp(client, "pozd", name="Pozdější")
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert html.index("Pozdější") < html.index("Tábor")   # 2026-09 before 2026-07


# --- camp detail --------------------------------------------------------------------

def test_camp_detail_has_history_tab(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/detail", headers=ADMIN).get_data(as_text=True)
    assert 'data-tax-tab="history"' in html
    assert 'data-history-root' in html
    assert 'data-history-mode' in html                    # the camp-level / full-history toggle
    assert f"/api/camps/{slug}/audit" in html
    assert "js/history-feed.js" in html


def test_camp_detail_token_tab_for_editor(client, seeded):
    slug = seeded["slug"]
    # a token is embedded so the list-with-data path renders too
    client.post(f"/api/camps/{slug}/tokens", json={"name": "sync", "role": "editor"}, headers=ADMIN)
    html = client.get(f"/camps/{slug}/detail", headers=editor(slug)).get_data(as_text=True)
    assert 'data-tax-tab="tokens"' in html
    assert 'data-tokens-root' in html
    assert 'id="cp-tokens-data"' in html
    assert "js/token-admin.js" in html
    assert f"/api/camps/{slug}/tokens" in html
    assert "/api/tokens/0" in html                        # revoke url (0 sentinel)
    assert '"sync"' in html and '"created_by"' in html    # the embedded token, without its secret
    assert "token_hash" not in html


def test_camp_detail_token_tab_hidden_from_viewer(client, seeded):
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/detail", headers=viewer(slug)).get_data(as_text=True)
    assert 'data-tax-tab="tokens"' not in html
    assert 'id="cp-tokens-data"' not in html
    assert "js/token-admin.js" not in html


# --- camp settings -------------------------------------------------------------------

def _delete_button(html):
    start = html.index("data-delete-camp")
    return html[start:html.index("</button>", start)]


def test_camp_edit_delete_button(client, seeded):
    """Admin-only (can_edit_camp_meta), and disabled while the camp has activities, with
    the reason as visible text rather than a hover-only tooltip."""
    html = client.get("/camps/t/edit", headers=ADMIN).get_data(as_text=True)
    assert "/api/camps/t" in html and "js/camp-settings.js" in html
    assert "disabled" in _delete_button(html)
    assert "Akci nelze smazat, dokud má aktivity" in html

    make_camp(client, "prazdna")
    empty = client.get("/camps/prazdna/edit", headers=ADMIN).get_data(as_text=True)
    assert "disabled" not in _delete_button(empty)

    as_editor = client.get("/camps/t/edit", headers=editor("t")).get_data(as_text=True)
    assert "data-delete-camp" not in as_editor


def test_camp_edit_form_time_input_roundtrip(client, seeded):
    # "Začátek dne" is an <input type="time">: HH:MM in the form, minutes past midnight stored
    slug = seeded["slug"]
    html = client.get(f"/camps/{slug}/edit", headers=ADMIN).get_data(as_text=True)
    assert 'type="time" name="window_start_min" value="04:00"' in html

    resp = client.post(f"/camps/{slug}/edit", data={
        "name": "Tábor", "slug": slug, "start_date": "2026-07-04", "length_days": "3",
        "timezone": "Europe/Prague", "window_start_min": "06:30", "snap_minutes": "15",
    }, headers=ADMIN)
    assert resp.status_code == 302
    camp = client.get(f"/api/camps/{slug}", headers=ADMIN).get_json()["camp"]
    assert camp["window_start_min"] == 390


# --- camps (create + edit) ---------------------------------------------------

def test_camp_create_starts_empty(client, app):
    camp = ok(client.post("/api/camps", json=_NEW_CAMP, headers=ADMIN))["camp"]
    assert camp["slug"] == "letni-tabor" and camp["length_days"] == 5
    assert get_json(client, "/api/camps/letni-tabor")["camp"]["name"] == "Letní tábor"
    assert [c["slug"] for c in get_json(client, "/api/camps")["camps"]] == ["letni-tabor"]
    assert get_json(client, "/api/camps/letni-tabor/timeline")["categories"] == []


@pytest.mark.parametrize("copy, categories", [
    ({"copy_from": "t"}, ["hra"]),
    ({"copy_from": "t", "copy_parts": ["orgs"]}, []),
])
def test_camp_create_copies_taxonomies_from_source(client, seeded, copy, categories):
    slug = ok(client.post("/api/camps", json={**_NEW_CAMP, **copy}, headers=ADMIN))["camp"]["slug"]
    tl = get_json(client, f"/api/camps/{slug}/timeline")
    assert [c["key"] for c in tl["categories"]] == categories
    assert [o["initials"] for o in tl["orgs"]] == ["K"]


def test_camp_create_unknown_copy_source(client):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "copy_from": "nope"}, headers=ADMIN)
    assert resp.status_code == 400


def test_camp_create_duplicate_slug(client, seeded):
    resp = client.post("/api/camps", json={**_NEW_CAMP, "slug": seeded["slug"]}, headers=ADMIN)
    assert resp.status_code == 400 and "Slug" in resp.get_json()["error"]


def test_camp_form_survives_a_slug_clash(client, seeded):
    """The form re-renders, querying the session the failed flush left behind: only the
    service's rollback keeps that working."""
    form = {"name": "Jiný", "slug": seeded["slug"], "start_date": "2026-08-01", "length_days": "3",
            "window_start_min": "240", "snap_minutes": "15"}
    resp = client.post("/camps/new", data=form, headers=ADMIN)
    assert resp.status_code == 200 and "Slug" in resp.get_data(as_text=True)


def test_camp_create_forbidden_for_editor(client, seeded):
    resp = client.post("/api/camps", json=_NEW_CAMP, headers=editor(seeded["slug"]))
    assert resp.status_code == 403


def test_camp_update_is_partial(client, seeded):
    """PATCH semantics: only the sent fields change; null is ignored for non-nullable
    settings and clears the nullable coordinates."""
    slug = seeded["slug"]
    camp = ok(client.patch(f"/api/camps/{slug}", json={"length_days": 9}, headers=ADMIN))["camp"]
    assert camp["length_days"] == 9
    assert camp["name"] == "Tábor" and camp["window_start_min"] == 240

    resp = client.patch(f"/api/camps/{slug}",
                        json={"window_start_min": None, "latitude": 50.1, "longitude": 14.4},
                        headers=ADMIN)
    assert ok(resp)["camp"]["window_start_min"] == 240
    resp = client.patch(f"/api/camps/{slug}", json={"latitude": None}, headers=ADMIN)
    assert ok(resp)["camp"]["latitude"] is None


def test_editor_cannot_change_name(client, seeded):
    slug = seeded["slug"]
    resp = client.patch(f"/api/camps/{slug}",
                        json={**_NEW_CAMP, "name": "Přejmenováno", "length_days": 4},
                        headers=editor(slug))
    camp = ok(resp)["camp"]
    assert camp["name"] == "Tábor"      # meta change ignored for editors
    assert camp["length_days"] == 4     # ...while the rest applies


@pytest.mark.parametrize("url, body, field", [
    ("/api/camps/t/activities", {}, "title"),
    ("/api/activities/{aid}/todos", {"title": ""}, "title"),
    ("/api/camps", {**_NEW_CAMP, "name": ""}, "name"),
])
def test_schema_errors_are_422_with_the_pydantic_list(client, seeded, url, body, field):
    resp = client.post(url.format(aid=seeded["activity_id"]), json=body, headers=ADMIN)
    assert resp.status_code == 422
    assert any(field in e["loc"] for e in resp.get_json())


# --- taxonomy ----------------------------------------------------------------

def test_taxonomy_categories_save(client, seeded):
    url = f"/api/camps/{seeded['slug']}/categories"
    items = [{"id": seeded["cat_id"], "key": "hra", "label": "Hra", "color": "#0b8043"},
             {"key": "jidlo", "label": "Jídlo", "color": "#4285f4"}]
    assert len(ok(client.put(url, json={"items": items}, headers=ADMIN))["items"]) == 2

    dup = [{"key": "x", "label": "A"}, {"key": "x", "label": "B"}]
    resp = client.put(url, json={"items": dup}, headers=ADMIN)
    assert resp.status_code == 400 and "opakuje" in resp.get_json()["error"]


def test_taxonomy_category_key_and_color_reject_injection(client, seeded):
    # key + color land unescaped in a <style> block / data-* on the timeline, so a
    # non-slug key or non-hex color must be rejected at the schema (422), not stored.
    url = f"/api/camps/{seeded['slug']}/categories"
    for bad in ({"key": "x</style>", "label": "A"}, {"label": "A", "color": "red}a{"}):
        assert client.put(url, json={"items": [bad]}, headers=ADMIN).status_code == 422


def test_taxonomy_reads_per_collection(client, seeded):
    s = seeded["slug"]
    assert {c["key"] for c in get_json(client, f"/api/camps/{s}/categories")["items"]} == {"hra"}
    assert get_json(client, f"/api/camps/{s}/orgs")["items"][0]["initials"] == "K"
    assert get_json(client, f"/api/camps/{s}/tags")["items"][0]["name"] == "Důležité"


# --- permissions -------------------------------------------------------------

def test_anonymous_is_401_and_viewer_cannot_edit(client, seeded):
    slug = seeded["slug"]
    assert client.get(f"/api/camps/{slug}/timeline").status_code == 401

    assert client.get(f"/api/camps/{slug}/timeline", headers=viewer(slug)).status_code == 200
    resp = client.post(f"/api/camps/{slug}/activities", json={"title": "X"}, headers=viewer(slug))
    assert resp.status_code == 403

    resp = client.post(f"/api/camps/{slug}/activities", json={"title": "X"}, headers=editor(slug))
    assert resp.status_code == 200


# --- camp deletion (admin-only, must be empty) -------------------------------

def test_camp_delete_admin_only_cascades_taxonomy(client, seeded):
    # copied taxonomy and a material, but no activities: still deletes (cascade)
    make_camp(client, "empty", copy_from=seeded["slug"])
    make_material(client, "empty", "papír")
    assert client.delete("/api/camps/empty", headers=editor("empty")).status_code == 403
    assert client.delete("/api/camps/empty", headers=ADMIN).status_code == 200
    assert client.get("/api/camps/empty", headers=ADMIN).status_code == 404


def test_camp_delete_blocked_with_activities(client, seeded):
    resp = client.delete(f"/api/camps/{seeded['slug']}", headers=ADMIN)
    assert resp.status_code == 400 and "nelze smazat" in resp.get_json()["error"]
