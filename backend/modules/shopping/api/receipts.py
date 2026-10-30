from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, deny_agent_keys, require_authenticated_admin
from app.db import get_db
from app.schemas import (
    ReceiptCommitRequest,
    ReceiptCommitResponse,
    ReceiptIngestRequest,
    ReceiptIngestResponse,
    ReceiptPreviewRequest,
    ReceiptPreviewResponse,
)
from app.services import commit_receipt_lines, ingest_receipt_text, preview_receipt_text
from modules.automation import image_bytes_to_text
from modules.shopping.schemas.receipt_review import DraftAction, ReceiptClassifierReviewRead, ReceiptDraftRead
from modules.shopping.services import receipt_review

# Kiosk-open (ADR 0004): scanning a physical receipt with the tablet camera is
# the same trust level as scanning a barcode — a receipt someone is physically
# handing to the device right now, not a stored credential. Unlike the Gmail
# receipt-email endpoints (modules/shopping/api/email_receipts.py), this never
# reaches into anything the household didn't just hand it.
router = APIRouter(prefix="/receipts", tags=["receipts"], dependencies=[Depends(allow_kiosk)])


@router.post("/ingest", response_model=ReceiptIngestResponse)
def ingest_receipt_route(payload: ReceiptIngestRequest, db: Session = Depends(get_db)) -> ReceiptIngestResponse:
    try:
        result = ingest_receipt_text(
            db,
            raw_text=payload.raw_text,
            location_id=payload.location_id,
            source_filename=payload.source_filename,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ReceiptIngestResponse.model_validate(result)


@router.post("/preview-image", response_model=ReceiptPreviewResponse)
async def preview_receipt_image_route(
    location_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ReceiptPreviewResponse:
    """Receipt photo -> DRAFT lines, never a ledger write (ADR 0018 §9).

    Rules first; a configured AI provider takes over when OCR is unavailable
    or the parser finds nothing. AI lines carry source='ai' + confidence."""
    from app.services import preview_receipt_image

    payload = await file.read()
    try:
        result = preview_receipt_image(
            db,
            image=payload,
            media_type=file.content_type or "image/jpeg",
            location_id=location_id,
            source_filename=file.filename,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ReceiptPreviewResponse.model_validate(result)


@router.post("/ingest-image", response_model=ReceiptIngestResponse)
async def ingest_receipt_image_route(
    location_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ReceiptIngestResponse:
    payload = await file.read()
    try:
        text = image_bytes_to_text(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        result = ingest_receipt_text(
            db,
            raw_text=text,
            location_id=location_id,
            source_filename=file.filename,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ReceiptIngestResponse.model_validate(result)


@router.post("/preview", response_model=ReceiptPreviewResponse)
def preview_receipt_route(payload: ReceiptPreviewRequest, db: Session = Depends(get_db)) -> ReceiptPreviewResponse:
    try:
        result = preview_receipt_text(
            db,
            raw_text=payload.raw_text,
            location_id=payload.location_id,
            source_filename=payload.source_filename,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ReceiptPreviewResponse.model_validate(result)


@router.post("/commit", response_model=ReceiptCommitResponse)
def commit_receipt_route(payload: ReceiptCommitRequest, db: Session = Depends(get_db)) -> ReceiptCommitResponse:
    try:
        result = commit_receipt_lines(
            db,
            location_id=payload.location_id,
            source_filename=payload.source_filename,
            raw_text=payload.raw_text,
            lines=[line.model_dump() for line in payload.lines],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ReceiptCommitResponse.model_validate(result)


# Dedicated draft endpoints leave legacy ingestion/preview compatibility intact.


@router.post("/draft", response_model=ReceiptDraftRead)
def receipt_draft_route(payload: ReceiptPreviewRequest, db: Session = Depends(get_db), actor=Depends(allow_kiosk)):
    try:
        preview = preview_receipt_text(
            db, raw_text=payload.raw_text, location_id=payload.location_id, source_filename=payload.source_filename
        )
        return receipt_review.create_draft(
            db,
            actor_id=actor.id,
            preview=preview,
            location_id=payload.location_id,
            raw_text=payload.raw_text,
            source_filename=payload.source_filename,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/draft-image", response_model=ReceiptDraftRead)
async def receipt_image_draft_route(
    location_id: UUID, file: UploadFile = File(...), db: Session = Depends(get_db), actor=Depends(allow_kiosk)
):
    from app.services import preview_receipt_image

    image = await file.read()
    try:
        preview = preview_receipt_image(
            db,
            image=image,
            media_type=file.content_type or "image/jpeg",
            location_id=location_id,
            source_filename=file.filename,
        )
        return receipt_review.create_draft(
            db,
            actor_id=actor.id,
            preview=preview,
            location_id=location_id,
            raw_text=preview.get("extracted_text")
            or "\n".join(review_line["raw_line"] for review_line in preview["lines"]),
            source_filename=file.filename,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post(
    "/drafts/{draft_id}/review", response_model=ReceiptClassifierReviewRead, dependencies=[Depends(deny_agent_keys)]
)
def receipt_classifier_review_route(
    draft_id: UUID, payload: DraftAction, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)
):
    try:
        return receipt_review.review_draft(
            db,
            draft_id=draft_id,
            actor_id=actor.id,
            token=payload.token,
            lines=[review_line.model_dump(mode="json") for review_line in payload.lines],
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/drafts/{draft_id}/commit", response_model=ReceiptCommitResponse, dependencies=[Depends(deny_agent_keys)])
def receipt_draft_commit_route(
    draft_id: UUID, payload: DraftAction, db: Session = Depends(get_db), actor=Depends(allow_kiosk)
):
    try:
        return receipt_review.commit_draft(
            db,
            draft_id=draft_id,
            actor_id=actor.id,
            token=payload.token,
            lines=[review_line.model_dump(mode="json") for review_line in payload.lines],
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
