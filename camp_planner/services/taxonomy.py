"""Per-camp taxonomy management: categories, orgs, tags.

Each list is saved as a whole: the client PUTs the desired list and the server
reconciles it against the current rows — update matched ids, create id-less items,
delete the rest. Plus a copy-from-another-camp helper. None of this bumps timeline_rev.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.camp import Category, Tag, TagKind
from camp_planner.models.common import czech_sort_key
from camp_planner.models.org import Org
from camp_planner.services import audit, errors
from camp_planner.services.camps import slugify

if TYPE_CHECKING:
    from camp_planner.models.camp import Camp
    from camp_planner.schemas import CategoryIn, OrgIn, TagDefIn

# Czech labels for the tag-value kinds (TagKind), shown in the settings UI.
TAG_KIND_LABELS: dict[str, str] = {
    TagKind.label.value: "Štítek",
    TagKind.check.value: "Hotovo / ne",
    TagKind.progress.value: "Postup (0–100 %)",
    TagKind.text.value: "Text",
}


# --- serialization (initial page embed + save responses) ---------------------

def categories(camp: Camp) -> list[dict]:
    return [{"id": c.id, "key": c.key, "label": c.label, "color": c.color}
            for c in camp.categories]


def orgs(camp: Camp) -> list[dict]:
    return [{"id": o.id, "initials": o.initials, "name": o.name}
            for o in sorted(camp.orgs, key=lambda o: czech_sort_key(o.initials))]


def tags(camp: Camp) -> list[dict]:
    return [{"id": t.id, "name": t.name, "kind": t.kind.value, "pinned": t.pinned}
            for t in camp.tags]


def serialize(camp: Camp) -> dict:
    """All three lists for the settings-page embed; the API/save paths use the
    per-part builders above."""
    return {"categories": categories(camp), "orgs": orgs(camp), "tags": tags(camp)}


# --- batch reconcile ---------------------------------------------------------

def _reconcile(
    camp: Camp, current: list, items: list, *, model, apply_fn: Callable,
    unique_of: Callable, dup_msg: Callable[[str], str], entity_type: EntityType,
    block_delete: Callable | None = None,
) -> None:
    """Sync `current` rows to match `items` (schema-validated models, in final order).
    Raises errors.Invalid (after rollback) on a duplicate or a blocked delete.

    unique_of(obj) is the row's unique value (an in-list repeat → dup_msg). block_delete(obj)
    may veto removing a row (e.g. a category still used by activities)."""
    existing = {obj.id: obj for obj in current}
    seen: set[int] = set()
    seen_unique: set = set()
    for idx, item in enumerate(items):
        obj = existing.get(item.id)
        if obj is None:
            obj = model(camp_id=camp.id)
            db.session.add(obj)
        else:
            seen.add(obj.id)
        apply_fn(obj, item, idx)
        value = unique_of(obj)
        if value in seen_unique:
            db.session.rollback()
            raise errors.Invalid(dup_msg(value))
        seen_unique.add(value)
    for oid, obj in existing.items():
        if oid not in seen:
            if block_delete and (error := block_delete(obj)):
                db.session.rollback()
                raise errors.Invalid(error)
            db.session.delete(obj)
    audit.record(camp_id=camp.id, entity_type=entity_type, entity_id=None,
                 action=AuditAction.update, message="seznam uložen")
    try:
        db.session.commit()
    except IntegrityError:  # unique-constraint backstop (race / in-place swap)
        db.session.rollback()
        raise errors.Invalid("Uložení selhalo – některá hodnota se opakuje. Zkuste to prosím znovu.") from None


# Appliers just write to the ORM row; the schema already validated the fields.

def _apply_category(obj: Category, item: CategoryIn, idx: int) -> None:
    obj.label = item.label
    obj.key = (item.key or slugify(item.label))[:40]   # [:40] guards the slugify fallback
    obj.color = item.color or "#9e9e9e"
    obj.sort_order = idx


def _apply_org(obj: Org, item: OrgIn, idx: int) -> None:
    obj.name, obj.initials = item.name, item.initials


def _apply_tag(obj: Tag, item: TagDefIn, idx: int) -> None:
    obj.name, obj.kind, obj.pinned, obj.sort_order = item.name, item.kind, item.pinned, idx


def _block_category_delete(cat: Category) -> str | None:
    if cat.activities:
        return f'Kategorii „{cat.label}“ nelze smazat – je přiřazena k aktivitám.'
    return None


def save_categories(camp: Camp, items: list[CategoryIn]) -> list[dict]:
    _reconcile(
        camp, list(camp.categories), items, model=Category, apply_fn=_apply_category,
        unique_of=lambda o: o.key,
        dup_msg=lambda v: f'Klíč kategorie „{v}“ se v seznamu opakuje.',
        block_delete=_block_category_delete, entity_type=EntityType.category,
    )
    return categories(camp)


def save_orgs(camp: Camp, items: list[OrgIn]) -> list[dict]:
    _reconcile(
        camp, list(camp.orgs), items, model=Org, apply_fn=_apply_org,
        unique_of=lambda o: o.initials,
        dup_msg=lambda v: f'Iniciály „{v}“ se v seznamu opakují.',
        entity_type=EntityType.org,
    )
    return orgs(camp)


def save_tags(camp: Camp, items: list[TagDefIn]) -> list[dict]:
    _reconcile(
        camp, list(camp.tags), items, model=Tag, apply_fn=_apply_tag,
        unique_of=lambda o: o.name,
        dup_msg=lambda v: f'Tag „{v}“ se v seznamu opakuje.',
        entity_type=EntityType.tag,
    )
    return tags(camp)


# --- copy from another camp (only at creation) -------------------------------

# Taxonomy lists copyable from another camp at creation time.
COPY_PARTS = ("categories", "orgs", "tags")


def copy_into(dest: Camp, source: Camp, *, parts=None) -> None:
    """Seed a new camp's taxonomies from `source`: copy the chosen `parts` (subset of
    COPY_PARTS; None = all). `dest` is assumed empty. Does not commit — the caller
    owns the transaction."""
    parts = set(COPY_PARTS if parts is None else parts)
    if "categories" in parts:
        for cat in source.categories:
            db.session.add(Category(camp_id=dest.id, key=cat.key, label=cat.label,
                                    color=cat.color, sort_order=cat.sort_order))
    if "orgs" in parts:
        for org in source.orgs:
            db.session.add(Org(camp_id=dest.id, name=org.name, initials=org.initials))
    if "tags" in parts:
        for tag in source.tags:
            db.session.add(Tag(camp_id=dest.id, name=tag.name, kind=tag.kind,
                               pinned=tag.pinned, sort_order=tag.sort_order))
