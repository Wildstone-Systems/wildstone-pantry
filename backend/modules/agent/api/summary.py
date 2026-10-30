from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from modules.agent.schemas.summary import AgentSummaryResponse
from modules.agent.services.summary import build_summary

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_user)])


@router.get("/summary", response_model=AgentSummaryResponse)
def summary_route(
    within_days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
) -> AgentSummaryResponse:
    """Household snapshot for integrations: one poll instead of five (ADR 0011)."""
    return AgentSummaryResponse.model_validate(build_summary(db, within_days=within_days))
