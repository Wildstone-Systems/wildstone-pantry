"""API routes for email receipt discovery, preview, and import."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk
from app.db import get_db
from modules.shopping.schemas.email_receipt import (
    EmailDiscoverResponse,
    EmailReceiptImportRequest,
    EmailReceiptImportResponse,
    EmailReceiptListRequest,
    EmailReceiptListResponse,
    EmailReceiptPreview,
    EmailReceiptPreviewBatchRequest,
    EmailReceiptPreviewBatchResponse,
    EmailRetailerRead,
)
from modules.shopping.services.email_receipt_service import EmailReceiptService

router = APIRouter(
    prefix="/receipts/email",
    tags=["email-receipts"],
    # Kiosk-open: placed in the plain nav with no login UI, like the rest of
    # the shopping/intake surfaces; only the mailbox credential setup itself
    # is admin-gated (Integrations, require_authenticated_admin).
    dependencies=[Depends(allow_kiosk)],
)


def _get_service() -> EmailReceiptService:
    return EmailReceiptService()


@router.get("/discover", response_model=EmailDiscoverResponse)
def discover_retailers(
    max_age_days: int = 90,
) -> EmailDiscoverResponse:
    """Scan Gmail for receipt-sending retailers and return availability info."""
    service = _get_service()
    result = service.discover_retailers(max_age_days=max_age_days)

    if result.error:
        raise HTTPException(status_code=503, detail=result.error)

    return EmailDiscoverResponse(
        retailers=[
            EmailRetailerRead(
                retailer=r.retailer,
                senders=r.senders,
                estimated_count=r.estimated_count,
                latest_date=r.latest_date,
            )
            for r in result.retailers
        ],
    )


@router.post("/list", response_model=EmailReceiptListResponse)
def list_receipt_emails(
    payload: EmailReceiptListRequest,
) -> EmailReceiptListResponse:
    """List receipt emails from Gmail matching the query (metadata only)."""
    service = _get_service()
    result = service.list_receipts(
        retailer=payload.retailer,
        max_age_days=payload.max_age_days,
        max_results=payload.max_results,
    )

    if result.error:
        raise HTTPException(status_code=503, detail=result.error)

    from modules.shopping.schemas.email_receipt import EmailReceiptSummary

    return EmailReceiptListResponse(
        emails=[
            EmailReceiptSummary(
                message_id=e.message_id,
                retailer=e.retailer,
                sender=e.sender,
                subject=e.subject,
                date=e.date,
                snippet=e.snippet,
            )
            for e in result.emails
        ],
        total_available=result.total_available,
    )


@router.get("/preview/{message_id}", response_model=EmailReceiptPreview)
def preview_single_receipt(
    message_id: str,
) -> EmailReceiptPreview:
    """Fetch and parse a single receipt email for preview before importing."""
    service = _get_service()
    preview = service.preview_receipt(message_id)

    if not preview:
        raise HTTPException(status_code=404, detail="Receipt email not found or could not be parsed")

    return EmailReceiptPreview(
        message_id=preview.message_id,
        retailer=preview.retailer,
        store_name=preview.store_name,
        store_number=preview.store_number,
        purchase_date=preview.purchase_date,
        subtotal=preview.subtotal,
        tax=preview.tax,
        total=preview.total,
        items=[
            {
                "description": i["description"],
                "product_name": i.get("product_name"),
                "quantity": i.get("quantity", "1"),
                "unit": i.get("unit", "each"),
                "price": i.get("price"),
                "category": i.get("category"),
            }
            for i in preview.items
        ],
        item_count=preview.item_count,
        parse_method=preview.parse_method,
    )


@router.post("/preview", response_model=EmailReceiptPreviewBatchResponse)
def preview_batch_receipts(
    payload: EmailReceiptPreviewBatchRequest,
) -> EmailReceiptPreviewBatchResponse:
    """Fetch and parse multiple receipt emails for batch preview."""
    service = _get_service()
    result = service.preview_receipts(
        retailer=payload.retailer,
        max_age_days=payload.max_age_days,
        max_results=payload.max_results,
    )

    return EmailReceiptPreviewBatchResponse(
        previews=[
            EmailReceiptPreview(
                message_id=p.message_id,
                retailer=p.retailer,
                store_name=p.store_name,
                store_number=p.store_number,
                purchase_date=p.purchase_date,
                subtotal=p.subtotal,
                tax=p.tax,
                total=p.total,
                items=[
                    {
                        "description": i["description"],
                        "product_name": i.get("product_name"),
                        "quantity": i.get("quantity", "1"),
                        "unit": i.get("unit", "each"),
                        "price": i.get("price"),
                        "category": i.get("category"),
                    }
                    for i in p.items
                ],
                item_count=p.item_count,
                parse_method=p.parse_method,
            )
            for p in result.previews
        ],
        errors=result.errors,
    )


@router.post("/import", response_model=EmailReceiptImportResponse)
def import_receipt(
    payload: EmailReceiptImportRequest,
    db: Session = Depends(get_db),
) -> EmailReceiptImportResponse:
    """Import a receipt email into PantryOS inventory.

    Fetches the email, parses line items, creates Items and Receipt records,
    and adds items to the specified inventory location.
    """
    service = _get_service()

    try:
        location_uuid = UUID(payload.location_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid location_id format")

    result = service.import_receipt(
        db,
        message_id=payload.message_id,
        location_id=location_uuid,
    )

    if result.error:
        raise HTTPException(status_code=422, detail=result.error)

    return EmailReceiptImportResponse(
        receipt_id=result.receipt_id,
        status=result.status,
        item_count=result.item_count,
        inventory_transactions=result.inventory_transactions,
    )