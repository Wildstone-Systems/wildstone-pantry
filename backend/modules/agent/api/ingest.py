from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import require_user
from app.db import get_db
from modules.agent.schemas.ingest import ProductUrlIngestRequest, ProductUrlIngestResponse
from modules.agent.services.product_url import ingest_product_url

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_user)])


@router.post("/ingest-product-url", response_model=ProductUrlIngestResponse)
def ingest_product_url_route(
    payload: ProductUrlIngestRequest,
    db: Session = Depends(get_db),
) -> ProductUrlIngestResponse:
    try:
        result = ingest_product_url(
            db,
            url=payload.url,
            add_to_shopping=payload.add_to_shopping,
            add_to_inventory=payload.add_to_inventory,
            location_id=payload.location_id,
            container_id=payload.container_id,
            quantity=payload.quantity,
            unit=payload.unit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ProductUrlIngestResponse.model_validate(result)
