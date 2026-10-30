from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from app.schemas.agent_key import (
    AgentKeyCreate,
    AgentKeyCreatedResponse,
    AgentKeyListResponse,
    AgentKeyRead,
)
from app.services.agent_keys import create_agent_key, list_agent_keys, revoke_agent_key

router = APIRouter(prefix="/agent-keys", tags=["agent-keys"])


@router.get("", response_model=AgentKeyListResponse)
def list_agent_keys_route(_user=Depends(require_user), db: Session = Depends(get_db)) -> AgentKeyListResponse:
    return AgentKeyListResponse(keys=[AgentKeyRead.model_validate(row) for row in list_agent_keys(db)])


@router.post("", response_model=AgentKeyCreatedResponse)
def create_agent_key_route(
    payload: AgentKeyCreate,
    user=Depends(require_user),
    db: Session = Depends(get_db),
) -> AgentKeyCreatedResponse:
    try:
        row, secret = create_agent_key(db, user=user, name=payload.name, scopes=payload.scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AgentKeyCreatedResponse(key=AgentKeyRead.model_validate(row), secret=secret)


@router.delete("/{key_id}", response_model=AgentKeyRead)
def revoke_agent_key_route(
    key_id: UUID,
    _user=Depends(require_user),
    db: Session = Depends(get_db),
) -> AgentKeyRead:
    try:
        row = revoke_agent_key(db, key_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentKeyRead.model_validate(row)
