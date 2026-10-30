"""Connecting PantryOS to Wildstone, and who is who (ADR 0026 §5, §7).

Admin-only, with a real login: this is where a credential for another system
is entered, so the kiosk identity is never a path in.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from modules.wildstone.schemas import ConnectionUpdate, MemberLinkUpdate
from modules.wildstone.services import connection, delivery, members, pairing

router = APIRouter(
    prefix="/integrations/wildstone",
    tags=["wildstone"],
    dependencies=[Depends(require_authenticated_admin)],
)


@router.get("/connection")
def get_connection(db: Session = Depends(get_db)) -> dict:
    """Whether PantryOS is connected, and how event delivery is going. Never
    returns the token."""
    return connection.status(db)


@router.put("/connection")
def put_connection(payload: ConnectionUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        connection.configure(db, keystone_url=payload.keystone_url, publisher_token=payload.publisher_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:  # secret encryption unavailable (ADR 0009)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return connection.status(db)


@router.delete("/connection")
def delete_connection(db: Session = Depends(get_db)) -> dict:
    """Disconnect. Undelivered events are dropped; PantryOS itself is untouched."""
    connection.disconnect(db)
    return connection.status(db)


@router.post("/connection/test")
def test_connection(db: Session = Depends(get_db)) -> dict:
    """Send one test event to Wildstone now, to prove the address and token."""
    try:
        return delivery.send_test(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/members")
def list_member_links(db: Session = Depends(get_db)) -> list[dict]:
    return members.list_links(db)


@router.put("/members/{profile_id}")
def put_member_link(profile_id: UUID, payload: MemberLinkUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        return members.set_link(db, profile_id=profile_id, member=payload.member)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pairing")
def create_pairing_code(admin=Depends(require_authenticated_admin)) -> dict:
    """A one-time code to type into Wildstone, with this PantryOS's address.

    Five minutes, single use, one at a time: asking again replaces it. The
    agent key Wildstone receives belongs to the admin who asked.
    """
    code, ttl = pairing.mint_code(admin.id)
    return {"code": code, "expires_in": ttl}
