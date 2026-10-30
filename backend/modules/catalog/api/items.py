from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, deny_agent_keys
from app.db import get_db
from app.schemas import ItemCreate, ItemListResponse, ItemRead, ItemUpdate
from app.services import (
    archive_item,
    create_item,
    get_item,
    item_usage,
    list_items,
    purge_item,
    restore_item,
    update_item,
)
from modules.catalog.schemas.item import (
    ApplyDensityRequest,
    ApplyUsdaNutritionRequest,
    ConversionCandidateRead,
    ConversionReviewResponse,
    CreateItemFromFoodSearchRequest,
    DataQualityResponse,
    DataQualityRowRead,
    DensitySuggestionRead,
    DuplicateMacroGroupRead,
    FoodSearchResponse,
    ItemPurgeResult,
    ItemRescanResult,
    ItemUsageRead,
    LoggableUnitsResponse,
    NutritionCandidate,
    NutritionCandidatesResponse,
)
from modules.catalog.services.barcodes import (
    apply_usda_nutrition,
    get_or_create_item_from_food_search,
    nutrition_candidates,
    rescan_item,
    search_food_by_name,
)
from modules.catalog.services.conversions import (
    apply_density,
    items_needing_conversion,
    suggest_density,
)
from modules.catalog.services.identity import IdentityConflict
from modules.catalog.services.ingredient_reference import loggable_units

router = APIRouter(prefix="/items", tags=["items"], dependencies=[Depends(allow_kiosk)])


@router.get("", response_model=ItemListResponse)
def list_items_route(
    include_inactive: bool = False,
    search: str | None = None,
    in_stock_only: bool = False,
    db: Session = Depends(get_db),
) -> ItemListResponse:
    """List catalog items, optionally narrowed by ``search`` (name / brand / canonical
    key) and/or ``in_stock_only`` (has a positive balance somewhere)."""
    rows = list_items(db, include_inactive=include_inactive, search=search, in_stock_only=in_stock_only)
    return ItemListResponse(items=[ItemRead.model_validate(row) for row in rows])


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item_route(payload: ItemCreate, db: Session = Depends(get_db)) -> ItemRead:
    try:
        item = create_item(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Item violates uniqueness constraints") from exc
    except ValueError as exc:
        # Impossible nutrition (see services.plausibility) — a client error with
        # a message that names the offending field, not a 500.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ItemRead.model_validate(item)


@router.get("/search-nutrition", response_model=FoodSearchResponse)
def search_nutrition_route(q: str, limit: int = 20, db: Session = Depends(get_db)) -> FoodSearchResponse:
    """USDA generic foods matching ``q`` — the picker for logging a meal that's
    neither in the pantry catalog nor has a barcode ("3 hard boiled eggs")."""
    result = search_food_by_name(db, q, limit=limit)
    return FoodSearchResponse(
        throttled=result["throttled"],
        searched_term=result.get("searched_term", q),
        candidates=[NutritionCandidate.model_validate(r) for r in result["candidates"]],
    )


@router.post("/from-food-search", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item_from_food_search_route(
    payload: CreateItemFromFoodSearchRequest, db: Session = Depends(get_db)
) -> ItemRead:
    """Find-or-create a lightweight, barcode-less item for a chosen USDA food
    (the search-nutrition picker's "use this") — plated off-pantry, never added
    to inventory."""
    try:
        item = get_or_create_item_from_food_search(db, query=payload.q, fdc_id=payload.fdc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ItemRead.model_validate(item)


# NOTE: must stay ABOVE `GET /{item_id}` — FastAPI matches in declaration
# order, so a literal path declared after a `/{uuid}` route is swallowed by
# it and 422s trying to parse "conversion-review" as a UUID.
@router.get("/data-quality", response_model=DataQualityResponse)
def data_quality_route(db: Session = Depends(get_db)) -> DataQualityResponse:
    """Stored nutrition values that disagree with themselves.

    The counterpart to the conversion queue: that lists what is *missing*, this
    lists what is *present and doubtful*. Pure arithmetic — no provider is
    contacted — so it is cheap enough to load on sight.
    """
    from modules.catalog.services.plausibility import (
        IMPOSSIBLE,
        audit_duplicate_macros,
        audit_items,
    )

    rows = audit_items(db)
    impossible = sum(1 for row in rows if any(i["severity"] == IMPOSSIBLE for i in row["issues"]))
    return DataQualityResponse(
        items=[DataQualityRowRead(**row) for row in rows],
        impossible=impossible,
        suspect=len(rows) - impossible,
        duplicate_macros=[DuplicateMacroGroupRead(**g) for g in audit_duplicate_macros(db)],
    )


@router.get("/conversion-review", response_model=ConversionReviewResponse)
def conversion_review_route(db: Session = Depends(get_db)) -> ConversionReviewResponse:
    """Items with no volume↔weight conversion — the human review queue.

    Per item, not per recipe: filling one in clears it from every recipe that
    mentions it, now and later.
    """
    from modules.automation import food_judge

    return ConversionReviewResponse(
        items=[ConversionCandidateRead(**row) for row in items_needing_conversion(db)],
        judge_available=food_judge.available(db),
    )


@router.get("/{item_id}", response_model=ItemRead)
def get_item_route(item_id: UUID, db: Session = Depends(get_db)) -> ItemRead:
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return ItemRead.model_validate(item)


@router.put("/{item_id}", response_model=ItemRead)
def update_item_route(item_id: UUID, payload: ItemUpdate, db: Session = Depends(get_db)) -> ItemRead:
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    try:
        updated_item = update_item(db, item, payload)
    except IdentityConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Item violates uniqueness constraints") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ItemRead.model_validate(updated_item)


@router.get("/{item_id}/loggable-units", response_model=LoggableUnitsResponse)
def loggable_units_route(item_id: UUID, db: Session = Depends(get_db)) -> LoggableUnitsResponse:
    """Which units this item can actually be logged in, and what one of each is
    worth. The meal logger offers only these, so you can't pick a unit that
    silently fails to convert and logs zero calories."""
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return LoggableUnitsResponse.model_validate(loggable_units(db, item))


@router.post("/{item_id}/rescan", response_model=ItemRescanResult)
def rescan_item_route(item_id: UUID, db: Session = Depends(get_db)) -> ItemRescanResult:
    """Re-query the external provider for this item's barcode and fill any gaps.

    Fills only missing fields (never overwrites edits) and reports what it added.
    The catalog page drives a whole-catalog refresh by calling this per item.
    """
    try:
        result = rescan_item(db, item_id)
    except IdentityConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    item = get_item(db, item_id)
    return ItemRescanResult(
        item_id=result["item_id"],
        status=result["status"],
        filled=result["filled"],
        estimated=result.get("estimated", False),
        estimated_source=result.get("estimated_source"),
        reference_review=result.get("reference_review"),
        item=ItemRead.model_validate(item) if item else None,
    )


@router.get("/{item_id}/nutrition-candidates", response_model=NutritionCandidatesResponse)
def nutrition_candidates_route(
    item_id: UUID,
    q: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> NutritionCandidatesResponse:
    """USDA foods to choose from for this item's nutrition (the picker). ``q``
    overrides the search term (defaults to the item name) so the user can refine."""
    try:
        result = nutrition_candidates(db, item_id, query=q, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return NutritionCandidatesResponse(
        item_id=item_id,
        throttled=result["throttled"],
        candidates=[NutritionCandidate.model_validate(r) for r in result["candidates"]],
    )


@router.post("/{item_id}/nutrition-from-usda", response_model=ItemRead)
def apply_usda_nutrition_route(
    item_id: UUID, payload: ApplyUsdaNutritionRequest, db: Session = Depends(get_db)
) -> ItemRead:
    """Apply a chosen USDA food's macros to the item (the picker's 'use this')."""
    try:
        item = apply_usda_nutrition(db, item_id, payload.fdc_id, query=payload.q)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ItemRead.model_validate(item)


@router.delete("/{item_id}", response_model=ItemRead)
def delete_item_route(item_id: UUID, db: Session = Depends(get_db)) -> ItemRead:
    """Archive an item. Reversible, and nothing is destroyed.

    This used to hard-delete, which cascaded through every child table and took
    the item's inventory events with it — an append-only ledger (ADR 0002)
    quietly losing rows, with no warning and no way back. Deleting an item is
    almost always "stop showing me this", not "erase that this ever existed",
    so that is what it does now. ``DELETE /items/{id}/purge`` is the other one,
    and it asks first.
    """
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return ItemRead.model_validate(archive_item(db, item))


@router.post("/{item_id}/restore", response_model=ItemRead)
def restore_item_route(item_id: UUID, db: Session = Depends(get_db)) -> ItemRead:
    """Put an archived item back in the catalogue."""
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return ItemRead.model_validate(restore_item(db, item))


@router.get("/{item_id}/usage", response_model=ItemUsageRead)
def item_usage_route(item_id: UUID, db: Session = Depends(get_db)) -> ItemUsageRead:
    """What a purge of this item would destroy — for the confirmation dialog.

    Fetched before offering the choice, not after making it: "this also deletes
    16 ledger events and 3 recipe references" is a decision someone can make,
    where "are you sure?" is not.
    """
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return ItemUsageRead.model_validate(item_usage(db, item))


@router.delete(
    "/{item_id}/purge",
    response_model=ItemPurgeResult,
    dependencies=[Depends(deny_agent_keys)],
)
def purge_item_route(
    item_id: UUID,
    confirm_name: str = Query(
        description="Must exactly match the item's name. Guards against an accidental purge."
    ),
    db: Session = Depends(get_db),
) -> ItemPurgeResult:
    """Permanently delete an item and everything that cascades from it.

    Irreversible. The cascade reaches ``inventory_events``, so this genuinely
    rewrites history rather than hiding a row.

    Two guards. ``deny_agent_keys`` keeps automations out entirely — an agent
    credential can be handed to a script or driven by a model, and neither
    should be able to rewrite the ledger. Admin-ness deliberately isn't the
    test: in personal mode the kiosk identity already resolves to an admin, so
    it would have excluded nobody while breaking every auth-disabled install.

    The second guard is the one that stops accidents: ``confirm_name`` must
    echo the item's exact name. That is something a person does on purpose and
    a mis-fired request never does by chance.
    """
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if confirm_name.strip() != item.name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"confirm_name must exactly match the item's name to purge it "
                f"(expected {item.name!r})."
            ),
        )

    destroyed = purge_item(db, item)
    return ItemPurgeResult(purged=True, **{k: v for k, v in destroyed.items() if k != "is_active"})


@router.get("/{item_id}/density-suggestion", response_model=DensitySuggestionRead | None)
def density_suggestion_route(
    item_id: UUID,
    q: str | None = Query(
        default=None,
        description=(
            "Search USDA for this food instead of the item's own name. Brand names "
            "('Black pepper grinder refill') rarely match; the plain food does."
        ),
    ),
    db: Session = Depends(get_db),
) -> DensitySuggestionRead | None:
    """A volume↔weight conversion for this item, looked up from USDA portion data.

    Returns null when USDA has no usable portions — a blank is the honest answer,
    and better than a plausible wrong number nobody can check.
    """
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    suggestion = suggest_density(db, item_id, query=q)
    if suggestion is None:
        return None

    return DensitySuggestionRead(
        item_id=item.id,
        item_name=item.name,
        density_g_per_ml=suggestion.density_g_per_ml,
        source_description=suggestion.source_description,
        fdc_id=suggestion.fdc_id,
        verified=suggestion.verified,
        verified_why=suggestion.verified_why,
        portions=suggestion.portions,
        grams_per_tsp=suggestion.grams_for("tsp"),
        grams_per_tbsp=suggestion.grams_for("tbsp"),
        grams_per_cup=suggestion.grams_for("cup"),
    )


@router.post("/{item_id}/density", response_model=ItemRead)
def apply_density_route(
    item_id: UUID, payload: ApplyDensityRequest, db: Session = Depends(get_db)
) -> ItemRead:
    """Accept a conversion for this item. Applied only on an explicit call —
    a suggestion never writes itself."""
    try:
        item = apply_density(db, item_id, payload.density_g_per_ml)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ItemRead.model_validate(item)
