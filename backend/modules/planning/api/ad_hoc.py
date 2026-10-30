"""Ad hoc cooking routes (ADR 0024).

A separate router mounted BEFORE the recipes router (see the planning
manifest): route matching is declaration-ordered and ``GET /recipes/ad-hoc``
would otherwise be swallowed by ``GET /recipes/{recipe_id}`` and 422 as a
non-UUID. Same prefix, same kiosk-open posture — cooking from the kitchen
tablet must not require an admin login (ADR 0004).

Reads and corrections deliberately reuse the existing by-id recipe surface
(``GET /api/recipes/{id}``, ``PUT .../ingredients``, ``DELETE`` to abandon);
this router carries only what is new: start, append, finish.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, resolve_actor
from app.db import get_db
from modules.planning.schemas.ad_hoc import (
    AdHocEntry,
    AdHocFinishRequest,
    AdHocFinishResponse,
    AdHocStartRequest,
    AdHocStateRead,
)
from modules.planning.api.recipes import _to_recipe_read
from modules.planning.schemas.recipe import FlaggedUnit, RecipeIngredientRead, RecipeRead
from modules.planning.services.ad_hoc import (
    append_ad_hoc_entry,
    finish_ad_hoc_cook,
    list_ad_hoc_drafts,
    start_ad_hoc_cook,
)
from modules.planning.services.recipes import get_recipe_with_ingredients

router = APIRouter(prefix="/recipes", tags=["recipes"], dependencies=[Depends(allow_kiosk)])


def _entry_dict(entry: AdHocEntry) -> dict:
    # The schema validator has already folded a singular `ingredient` into
    # `ingredients`, so the list is the whole story here.
    return {
        "ingredients": [spec.model_dump() for spec in entry.ingredients],
        "step_text": entry.step_text,
    }


def _state(db: Session, recipe_id: UUID, flagged: list[dict]) -> AdHocStateRead:
    recipe, ingredients = get_recipe_with_ingredients(db, recipe_id)
    # has_upload=False, not a lookup: a mid-cook draft has no picture. (An
    # upload becomes possible the moment it's saved, via the normal editor.)
    return AdHocStateRead(
        recipe=_to_recipe_read(recipe, has_upload=False),
        ingredients=[RecipeIngredientRead(**entry) for entry in ingredients],
        flagged_units=[FlaggedUnit(**entry) for entry in flagged],
    )


@router.post("/ad-hoc", response_model=AdHocStateRead)
def start_ad_hoc_route(payload: AdHocStartRequest, db: Session = Depends(get_db)) -> AdHocStateRead:
    """Open an ad hoc cook: a draft recipe you append to as you go (ADR 0024 §1).
    Optionally seeded with first entries (e.g. expiring items to use up)."""
    try:
        recipe, flagged = start_ad_hoc_cook(
            db, name=payload.name, entries=[_entry_dict(e) for e in payload.entries]
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _state(db, recipe.id, flagged)


@router.get("/ad-hoc", response_model=list[RecipeRead])
def list_ad_hoc_drafts_route(db: Session = Depends(get_db)) -> list[RecipeRead]:
    """Open drafts, newest first — the resume surface. Drafts appear nowhere
    else: the recipe list and suggestions exclude them (ADR 0024 §1)."""
    return [_to_recipe_read(row, has_upload=False) for row in list_ad_hoc_drafts(db)]


@router.post("/{recipe_id}/ad-hoc/entries", response_model=AdHocStateRead)
def append_ad_hoc_entry_route(
    recipe_id: UUID, payload: AdHocEntry, db: Session = Depends(get_db)
) -> AdHocStateRead:
    """Record one cooking action — ingredient use(s), a step, or both at once
    (ADR 0024 §2). One action, one entry, however many things went in: a
    dressing's oil, vinegar and mustard arrive together with the one step that
    used them. Repeated items merge into one line; steps append as the next
    numbered instruction. Committed immediately: closing the tab loses
    nothing."""
    try:
        flagged = append_ad_hoc_entry(
            db,
            recipe_id,
            ingredients=[spec.model_dump() for spec in payload.ingredients],
            step_text=payload.step_text,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _state(db, recipe_id, flagged)


@router.post("/{recipe_id}/ad-hoc/finish", response_model=AdHocFinishResponse)
def finish_ad_hoc_route(
    recipe_id: UUID,
    payload: AdHocFinishRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> AdHocFinishResponse:
    """Close out the cook: deduct what was recorded (to zero — shortfalls are
    reported, never refused; ADR 0024 §3), then promote the draft to a real
    recipe or delete it keeping the deductions (§4). Abandoning without
    deducting is the existing ``DELETE /api/recipes/{id}``."""
    try:
        result = finish_ad_hoc_cook(
            db,
            recipe_id,
            servings_made=payload.servings_made,
            save=payload.save,
            name=payload.name,
            location_id=payload.location_id,
            leftover_servings=payload.leftover_servings,
            note=payload.note,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AdHocFinishResponse.model_validate(result)
