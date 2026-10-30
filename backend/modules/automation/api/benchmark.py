"""Bounded admin synthetic corpus review and request-owned benchmark steps."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import require_authenticated_admin
from app.db import get_db
from modules.automation import benchmark_service as service
from modules.automation.api.classifier import _payload, _request_body
from modules.automation.benchmark_contract import (
    FreezeCommand,
    PolicyCommand,
    ReviewCommand,
    RunCommand,
    UpgradeCommand,
)

router = APIRouter(
    prefix="/ai/classifier/benchmark",
    tags=["classifier evaluation"],
    dependencies=[Depends(require_authenticated_admin)],
)


def _call(function, *args):
    try:
        return function(*args)
    except ValueError:
        raise HTTPException(
            409, "Invalid, changed or unavailable benchmark evidence; refresh and check prerequisites"
        ) from None


@router.get("/corpus")
def corpus(db: Session = Depends(get_db)):
    return _call(service.corpus_view, db)


@router.get("/corpus/compatibility")
def compatibility(db: Session = Depends(get_db)):
    return _call(service.compatibility_view, db)


@router.post("/corpus/upgrade", openapi_extra=_request_body(UpgradeCommand))
def upgrade(payload: dict = Depends(_payload), db: Session = Depends(get_db)):
    return _call(service.upgrade_draft, db, payload)


@router.post("/corpus/review", openapi_extra=_request_body(ReviewCommand))
def review(
    payload: dict = Depends(_payload), db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return _call(service.review_case, db, payload, actor.id)


@router.post("/corpus/freeze", openapi_extra=_request_body(FreezeCommand))
def freeze(
    payload: dict = Depends(_payload), db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return _call(service.freeze, db, payload, actor.id)


@router.post("/policy", openapi_extra=_request_body(PolicyCommand))
def policy(payload: dict = Depends(_payload), db: Session = Depends(get_db)):
    return _call(service.set_policy, db, payload)


@router.post("/corpus/diagnostic-freeze", openapi_extra=_request_body(FreezeCommand))
def diagnostic_freeze(
    payload: dict = Depends(_payload), db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return _call(service.diagnostic_freeze, db, payload, actor.id)


@router.post("/corpus/reopen", openapi_extra=_request_body(FreezeCommand))
def reopen(
    payload: dict = Depends(_payload), db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return _call(service.reopen, db, payload, actor.id)


@router.post("/runs", openapi_extra=_request_body(RunCommand))
def create(
    payload: dict = Depends(_payload), db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    return _call(service.create_run, db, payload, actor.id)


@router.get("/runs")
def runs(db: Session = Depends(get_db)):
    return _call(service.list_runs, db)


@router.get("/runs/{run_id}")
def read(run_id: UUID, db: Session = Depends(get_db)):
    return _call(service.read_run, db, run_id)


@router.post("/runs/{run_id}/step")
async def step(run_id: UUID, request: Request, db: Session = Depends(get_db)):
    try:
        return await service.step(db, run_id, request.is_disconnected)
    except ValueError:
        raise HTTPException(409, "Run not ready or evidence/configuration changed; refresh") from None


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: UUID, db: Session = Depends(get_db)):
    return _call(service.cancel, db, run_id)


@router.delete("/runs/{run_id}")
def remove(run_id: UUID, db: Session = Depends(get_db)):
    _call(service.remove_run, db, run_id)
    return {"deleted": True}
