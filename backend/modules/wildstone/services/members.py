"""Household profile → Wildstone member links (ADR 0026 §5, ADR-W024 A8).

Optional per profile. An event about an unlinked profile is still sent, with
no subject: valid for household-wide rules, invisible to per-member ones.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.profiles import get_profile, list_profiles
from modules.wildstone import repository

# Keystone's subject is an identifier, not prose: bounded, no control chars.
_MEMBER = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")


def valid_member(value) -> str | None:
    """A Wildstone member name as Keystone accepts a subject, or None."""
    member = str(value or "").strip()
    return member if _MEMBER.match(member) else None


def member_for(db: Session, profile_id: str | None) -> str | None:
    if not profile_id:
        return None
    try:
        key = UUID(str(profile_id))
    except ValueError:
        return None
    link = repository.get_member_link(db, key)
    return link.member if link else None


def list_links(db: Session) -> list[dict]:
    links = repository.member_links(db)
    return [{"profile_id": p.id, "display_name": p.display_name, "member": links.get(p.id)} for p in list_profiles(db)]


def set_link(db: Session, *, profile_id: UUID, member: str | None) -> dict:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise LookupError("Household profile not found")
    if member:
        member = member.strip()
        if not _MEMBER.match(member):
            raise ValueError("member must be a Wildstone member name: letters, digits, . _ @ - (max 128)")
    repository.set_member_link(db, profile_id, member or None)
    db.commit()
    return {"profile_id": profile.id, "display_name": profile.display_name, "member": member or None}
