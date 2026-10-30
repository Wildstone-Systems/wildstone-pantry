"""The pairing exchange Keystone calls (ADR 0026 §7).

No login: the one-time code IS the credential, as for device pairing
(ADR 0018 §8). Failed attempts count against the same limiter as logins, so
guessing an eight-character code in its five minutes is not a real option.
Every refusal of the code itself reads the same, whether it was wrong,
expired or already used.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ratelimit import login_limiter
from app.db import get_db
from modules.wildstone.services import pairing

router = APIRouter(prefix="/integrations/wildstone", tags=["wildstone"])


class PairingExchange(BaseModel):
    code: str = Field(max_length=32)
    keystone_url: str = Field(max_length=512, description="Where PantryOS should send household events.")
    publisher_token: str = Field(max_length=512, description="The publisher token Keystone minted for PantryOS.")


class PairingExchangeResult(BaseModel):
    agent_key: str = Field(description="An agent key for Wildstone's adapter. Shown once.")
    contract: str = Field(description="The contract version PantryOS speaks, e.g. pantry-1.")
    web_url: str | None = Field(
        default=None,
        description=(
            "Where a person opens PantryOS's web UI (not the API address), from PANTRYOS_PUBLIC_URL. "
            "Omitted when not configured."
        ),
    )


@router.post("/pairing/exchange", response_model=PairingExchangeResult, response_model_exclude_none=True)
def exchange_pairing_code(payload: PairingExchange, request: Request, db: Session = Depends(get_db)) -> dict:
    limiter_key = f"wildstone-pairing:{request.client.host if request.client else 'unknown'}"
    login_limiter.check(limiter_key)
    try:
        result = pairing.exchange(
            db, code=payload.code, keystone_url=payload.keystone_url, publisher_token=payload.publisher_token
        )
    except pairing.PairingRejected as exc:
        if exc.bad_code:
            login_limiter.record_failure(limiter_key)
        raise HTTPException(status_code=401 if exc.bad_code else 400, detail=str(exc)) from exc
    except RuntimeError as exc:  # connector secrets cannot be stored here (ADR 0009)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    login_limiter.record_success(limiter_key)
    return result
