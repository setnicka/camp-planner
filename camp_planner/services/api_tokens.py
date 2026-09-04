"""API bearer tokens: create / list / revoke, and request-time authentication.

A token is scoped to one camp + role (editor/viewer). The secret (``cp_`` + 32
random bytes) is shown once at creation; only its SHA-256 is stored. Resolution
runs on the api blueprint before the session provider (see auth/token.py).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

from flask import g
from sqlalchemy.exc import IntegrityError

from camp_planner.extensions import db, db_session
from camp_planner.models.audit import AuditAction, EntityType
from camp_planner.models.auth import ApiToken
from camp_planner.models.common import naive_utcnow as _now
from camp_planner.services import audit, errors

if TYPE_CHECKING:
    from camp_planner.auth.identity import CampRole
    from camp_planner.models.camp import Camp

_PREFIX = "cp_"
# How stale last_used_at may get before a read refreshes it — avoids a write per request.
_TOUCH_AFTER = timedelta(minutes=1)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _audit(token: ApiToken, action: AuditAction) -> None:
    """Stage a token-event audit row; skipped outside a request (CLI has no identity)."""
    if g.get("identity") is None:
        return
    fields = {"name": token.name, "role": token.role}   # never the secret / hash
    created = action is AuditAction.create
    audit.record(camp_id=token.camp_id, entity_type=EntityType.api_token,
                 entity_id=token.id, action=action,
                 changes={k: [None, v] if created else [v, None] for k, v in fields.items()})


def create(camp: Camp, name: str, role: CampRole, created_by: str) -> tuple[ApiToken, str]:
    """Create a token for the camp and return (token, secret). The secret is the only
    time it's available in the clear. Raises Invalid on a duplicate name."""
    name = name.strip()
    if not name:
        raise errors.Invalid("Zadejte název tokenu.")
    secret = _PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(name=name, token_hash=_hash(secret), camp_id=camp.id,
                     role=role, created_by=created_by)
    db_session.add(token)
    try:
        db_session.flush()   # get token.id + catch a duplicate name before auditing
    except IntegrityError:
        db_session.rollback()
        raise errors.Invalid(f"Token „{name}“ už v tomto táboře existuje.") from None
    _audit(token, AuditAction.create)
    db_session.commit()
    return token, secret


def list_for_camp(camp: Camp) -> list[ApiToken]:
    return list(db_session.scalars(
        db.select(ApiToken).filter_by(camp_id=camp.id).order_by(ApiToken.name)
    ).all())


def revoke(token: ApiToken) -> dict:
    token_id = token.id
    _audit(token, AuditAction.delete)   # stage while id/name are still live
    db_session.delete(token)
    db_session.commit()
    return {"id": token_id}


def authenticate(secret: str) -> ApiToken | None:
    """Resolve a presented secret to its token (None for an unknown/revoked secret).
    Pure lookup; recording the use is the caller's touch() call."""
    return db_session.scalar(db.select(ApiToken).filter_by(token_hash=_hash(secret)))


def touch(token: ApiToken) -> None:
    """Refresh last_used_at, at most once per _TOUCH_AFTER. What counts as a use is the
    caller's rule: the API counts presenting a valid token, the iCal feed only a served
    response. Commits (expiring everything loaded in the session)."""
    now = _now()
    if token.last_used_at is None or now - token.last_used_at > _TOUCH_AFTER:
        token.last_used_at = now
        db_session.commit()
