"""API for IN purchase connectors (step 5b).

Connectors are discovered from the registry so a dormant one still shows up
(with ``configured=false``). ``preview`` pulls normalized orders without writing
anything — a dormant connector answers 409, mirroring the OUT export contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, resolve_actor
from app.db import get_db
from modules.shopping.connectors import NotConfiguredError, get_connector, list_connectors
from modules.shopping.schemas.purchase_connectors import (
    NormalizedLineRead,
    NormalizedOrderRead,
    PurchaseConnectorImportRequest,
    PurchaseConnectorImportResponse,
    PurchaseConnectorPreviewResponse,
    PurchaseConnectorRead,
    PurchaseConnectorsResponse,
)
from modules.shopping.services.connector_config import get_effective, is_configurable
from modules.shopping.services.purchase_connector_import import commit_connector

router = APIRouter(
    prefix="/purchases/connectors",
    tags=["purchase-connectors"],
    # Kiosk-open: the shopping list's "Purchase intake" card lives in the
    # kiosk-open nav with no login UI; only connector credential setup itself
    # is admin-gated (Integrations screen).
    dependencies=[Depends(allow_kiosk)],
)


@router.get("", response_model=PurchaseConnectorsResponse)
def list_route(db: Session = Depends(get_db)) -> PurchaseConnectorsResponse:
    """Available IN connectors + whether each is configured (dormant until set)."""
    return PurchaseConnectorsResponse(
        connectors=[
            PurchaseConnectorRead(
                key=c.key,
                label=c.label,
                configured=c.configured(get_effective(db, c.key)),
                configurable=is_configurable(c.key),
            )
            for c in list_connectors()
        ]
    )


@router.post("/{key}/preview", response_model=PurchaseConnectorPreviewResponse)
def preview_route(key: str, db: Session = Depends(get_db)) -> PurchaseConnectorPreviewResponse:
    """Fetch + normalize recent orders for a connector (no write).

    404 unknown connector; 409 dormant (not configured).
    """
    connector = get_connector(key)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"Unknown purchase connector: {key}")
    try:
        orders = connector.fetch_orders(get_effective(db, key))
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return PurchaseConnectorPreviewResponse(
        connector=key,
        orders=[
            NormalizedOrderRead(
                vendor=o.vendor,
                purchased_on=o.purchased_on,
                external_id=o.external_id,
                lines=[
                    NormalizedLineRead(
                        name=line.name,
                        quantity=line.quantity,
                        unit=line.unit,
                        upc=line.upc,
                        unit_price=line.unit_price,
                    )
                    for line in o.lines
                ],
            )
            for o in orders
        ],
    )


@router.post("/{key}/import", response_model=PurchaseConnectorImportResponse)
def import_route(
    key: str,
    payload: PurchaseConnectorImportRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> PurchaseConnectorImportResponse:
    """Fetch a connector's orders and commit them to inventory at a location.

    Idempotent per order (dedup on the source Message-ID). 404 unknown connector
    or missing location; 409 dormant (not configured).
    """
    try:
        result = commit_connector(db, connector_key=key, location_id=payload.location_id, actor=actor)
    except NotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PurchaseConnectorImportResponse(
        connector=key,
        orders=result.orders,
        imported_orders=result.imported_orders,
        skipped_orders=result.skipped_orders,
        imported_lines=result.imported_lines,
        transaction_ids=result.transaction_ids,
    )
