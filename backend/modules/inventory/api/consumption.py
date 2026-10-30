"""Per-eater consumption API — step 4b (kiosk-open).

``POST /api/consumption/plate`` attributes food intake to a household profile;
``GET /api/consumption/calories`` folds a profile's plates into a daily total vs
their target. Everyday kiosk actions: ``allow_kiosk`` (open in kiosk mode,
falling back to the personal identity with no credential — see its docstring)
+ ``resolve_actor`` for the active-profile header.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, resolve_actor
from app.db import get_db
from app.services.profiles import resolve_actor_profile
from modules.inventory.schemas.consumption import (
    CalorieEntryRead,
    NutritionSummaryResponse,
    PlateBatchRequest,
    PlateBatchResponse,
    PlateRecipeRequest,
    PlateRecipeResponse,
    CalorieRollupResponse,
    CalorieSummaryResponse,
    EditPlateRequest,
    PlateRequest,
    PlateResponse,
    ProfileCalorieSummaryRead,
)
from modules.inventory.services.consumption import (
    daily_calorie_rollup,
    delete_plate,
    edit_plate,
    household_calorie_summary,
    nutrition_summary,
    plate,
    plate_batch,
    plate_recipe,
    today_in_zone,
)

router = APIRouter(prefix="/consumption", tags=["consumption"], dependencies=[Depends(allow_kiosk)])


@router.post("/plate", response_model=PlateResponse)
def plate_route(
    payload: PlateRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> PlateResponse:
    """Plate/eat: consume an item as a profile with a calorie snapshot. The eater
    comes from the body ``profile_id`` or the ``X-Household-Profile`` header —
    plating is inherently per-eater, so one is required (400 otherwise)."""
    raw_profile = str(payload.profile_id) if payload.profile_id is not None else actor
    profile_id = resolve_actor_profile(db, raw_profile) if raw_profile else None
    if profile_id is None:
        raise HTTPException(status_code=400, detail="A household profile is required to plate food")

    try:
        event_id, row, item, qty, calories, protein_g, fat_g, carbs_g = plate(
            db,
            item_id=payload.item_id,
            location_id=payload.location_id,
            profile_id=profile_id,
            quantity=payload.quantity,
            unit=payload.unit,
            weight_grams=payload.weight_grams,
            meal_type=payload.meal_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PlateResponse(
        event_id=event_id,
        item_id=item.id,
        item_name=item.name,
        location_id=row.location_id if row is not None else None,
        profile_id=UUID(profile_id),
        quantity=qty,
        # The amount/unit actually eaten (`qty` is already this), not the stock
        # unit the row/ledger moved in — see plate()'s docstring.
        unit=payload.unit,
        calories=calories,
        protein_g=protein_g,
        fat_g=fat_g,
        carbs_g=carbs_g,
        balance=row.quantity if row is not None else None,
    )


@router.post("/plate-batch", response_model=PlateBatchResponse)
def plate_batch_route(
    payload: PlateBatchRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> PlateBatchResponse:
    """Log several things in one confirmation (a bar, a bag of chips, a soda).

    Validated as a whole before anything is written — if any entry is unloggable
    the whole batch 400s with nothing recorded, so you never end up having to
    work out which half of a meal landed."""
    raw_profile = str(payload.profile_id) if payload.profile_id is not None else actor
    profile_id = resolve_actor_profile(db, raw_profile) if raw_profile else None
    if profile_id is None:
        raise HTTPException(status_code=400, detail="A household profile is required to log a meal")

    try:
        results = plate_batch(db, profile_id=profile_id, entries=[e.model_dump() for e in payload.entries])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logged = [
        PlateResponse(
            event_id=r["event_id"],
            item_id=r["item_id"],
            item_name=r["item_name"],
            location_id=r["location_id"],
            profile_id=UUID(profile_id),
            quantity=r["quantity"],
            unit=r["unit"],
            calories=r["calories"],
            protein_g=r["protein_g"],
            fat_g=r["fat_g"],
            carbs_g=r["carbs_g"],
            balance=r["balance"],
        )
        for r in results
    ]
    return PlateBatchResponse(
        profile_id=UUID(profile_id),
        logged=logged,
        total_calories=sum((r["calories"] or Decimal(0) for r in results), Decimal(0)),
        complete=all(r["calories"] is not None for r in results),
    )


@router.post("/plate-recipe", response_model=PlateRecipeResponse)
def plate_recipe_route(
    payload: PlateRecipeRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> PlateRecipeResponse:
    """Log eating a portion of a recipe — nutrition from the recipe's own
    ingredients, scaled to `servings`. Never re-deducts the ingredients (cooking
    already did that), but eats into the recipe's OUTPUT stock when it declares
    one and has some on hand (ADR 0025 §5)."""
    raw_profile = str(payload.profile_id) if payload.profile_id is not None else actor
    profile_id = resolve_actor_profile(db, raw_profile) if raw_profile else None
    if profile_id is None:
        raise HTTPException(status_code=400, detail="A household profile is required to log a meal")

    try:
        event_id, item, servings, macros, complete, deducted, deducted_unit, location_id = plate_recipe(
            db,
            recipe_id=payload.recipe_id,
            profile_id=profile_id,
            servings=payload.servings,
            note=payload.note,
            meal_type=payload.meal_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PlateRecipeResponse(
        event_id=event_id,
        recipe_id=payload.recipe_id,
        item_id=item.id,
        item_name=item.name,
        profile_id=UUID(profile_id),
        servings=servings,
        calories=macros["calories"],
        protein_g=macros["protein_g"],
        fat_g=macros["fat_g"],
        carbs_g=macros["carbs_g"],
        complete=complete,
        deducted_quantity=deducted,
        deducted_unit=deducted_unit,
        location_id=location_id,
    )


@router.get("/nutrition-summary", response_model=NutritionSummaryResponse)
def nutrition_summary_route(
    from_: date = Query(alias="from"),
    to: date = Query(),
    profile_id: UUID | None = Query(default=None),
    tz: str | None = Query(default=None, description="IANA zone — which days the range means"),
    db: Session = Depends(get_db),
) -> NutritionSummaryResponse:
    """Calories/macros per day over a range, plus range total and daily average.
    Omit `profile_id` to pool the whole household. Same `tz` rule as /calories."""
    try:
        data = nutrition_summary(db, start=from_, end=to, profile_id=profile_id, tz=tz)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return NutritionSummaryResponse.model_validate(data)


@router.patch("/plate/{event_id}", response_model=PlateResponse)
def edit_plate_route(event_id: UUID, payload: EditPlateRequest, db: Session = Depends(get_db)) -> PlateResponse:
    """Correct a mistaken plate's amount ("Calories" page's edit button) —
    same item/location/profile/day, new quantity/unit. Internally undoes the
    original (see delete_plate) and plates the correction fresh."""
    try:
        event_id_new, row, item, qty, calories, protein_g, fat_g, carbs_g, unit, profile_id = edit_plate(
            db,
            event_id=event_id,
            quantity=payload.quantity,
            unit=payload.unit,
            weight_grams=payload.weight_grams,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PlateResponse(
        event_id=event_id_new,
        item_id=item.id,
        item_name=item.name,
        location_id=row.location_id if row is not None else None,
        profile_id=UUID(profile_id),
        quantity=qty,
        unit=unit,
        calories=calories,
        protein_g=protein_g,
        fat_g=fat_g,
        carbs_g=carbs_g,
        balance=row.quantity if row is not None else None,
    )


@router.delete("/plate/{event_id}", status_code=204)
def delete_plate_route(event_id: UUID, db: Session = Depends(get_db)) -> None:
    """Undo a mistaken plate ("Calories" page's delete button) — restores any
    inventory it decremented and nets it out of the calorie total."""
    try:
        delete_plate(db, event_id=event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/calories", response_model=CalorieRollupResponse)
def calories_route(
    profile_id: UUID,
    # Never `date.today` as a default_factory: that resolves the server's day
    # before `tz` is read. Resolved against the zone instead — see `today_in_zone`.
    on: date | None = Query(default=None, description="Calendar day; defaults to today in `tz`"),
    tz: str | None = Query(default=None, description="IANA zone (e.g. America/New_York) — which day 'on' means"),
    db: Session = Depends(get_db),
) -> CalorieRollupResponse:
    try:
        data = daily_calorie_rollup(db, profile_id=profile_id, on=on or today_in_zone(tz), tz=tz)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CalorieRollupResponse(
        profile_id=data["profile_id"],
        date=data["date"],
        complete=data["complete"],
        total_calories=data["total_calories"],
        total_protein_g=data["total_protein_g"],
        total_fat_g=data["total_fat_g"],
        total_carbs_g=data["total_carbs_g"],
        target=data["target"],
        entries=[CalorieEntryRead.model_validate(e) for e in data["entries"]],
    )


@router.get("/calories/summary", response_model=CalorieSummaryResponse)
def calories_summary_route(
    on: date | None = Query(default=None, description="Calendar day; defaults to today in `tz`"),
    tz: str | None = Query(default=None, description="IANA zone (e.g. America/New_York) — which day 'on' means"),
    db: Session = Depends(get_db),
) -> CalorieSummaryResponse:
    # Resolved here as well as in the service, because the response echoes the
    # day it actually reported on.
    day = on or today_in_zone(tz)
    rows = household_calorie_summary(db, on=day, tz=tz)
    return CalorieSummaryResponse(
        date=day,
        profiles=[ProfileCalorieSummaryRead.model_validate(r) for r in rows],
    )
