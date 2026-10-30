from sqlalchemy import text
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends

from app.core import settings
from app.db import get_db
from app.schemas import HealthRead

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthRead)
def health(db: Session = Depends(get_db)) -> HealthRead:
    db.execute(text("SELECT 1"))
    return HealthRead(status="ok", database="connected", version=settings.app_version)
