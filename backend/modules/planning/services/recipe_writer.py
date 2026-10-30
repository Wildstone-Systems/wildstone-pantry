"""Shared recipe writer for ordinary and reviewed import routes."""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Recipe
from modules.planning.schemas.recipe import RecipeCreate
from modules.planning.services.recipes import (
    create_recipe,
    find_recipe_by_external_identity,
    resolve_ingredient_specs,
    set_recipe_ingredients,
    update_recipe,
)


def _opt_decimal(value):
    return None if value is None else Decimal(str(value))


def _resolve_ingredients(db, payload, *, expected_resolution=None):
    if payload.ingredients is None:
        return None, []
    inputs, _, flagged = resolve_ingredient_specs(
        db,
        [spec.model_dump() for spec in payload.ingredients],
        create_missing=payload.create_missing_items,
        **({"expected_resolution": expected_resolution} if expected_resolution is not None else {}),
    )
    return inputs, flagged


def write_recipe(db: Session, payload: RecipeCreate, *, expected_resolution=None) -> tuple[Recipe, list[dict], bool]:
    """Create the recipe, or update the one already imported under the same external
    identity. Returns the row, its flagged units, and whether it was newly created."""
    # Resolve ingredients first so a bad one fails before any recipe row is committed.
    inputs, flagged = _resolve_ingredients(db, payload, expected_resolution=expected_resolution)

    external = payload.external_source
    existing = find_recipe_by_external_identity(
        db,
        external.source if external else None,
        external.id if external else None,
    )
    source_text = payload.source_text
    if source_text is None and existing is None:
        source_text = "\n".join(spec.raw_line for spec in (payload.ingredients or []) if spec.raw_line) or None
    fields = dict(
        name=payload.name,
        instructions=payload.instructions,
        servings=payload.servings,
        output_item_id=payload.output_item_id,
        output_quantity=_opt_decimal(payload.output_quantity),
        output_unit=payload.output_unit,
        output_mode=payload.output_mode,
        output_yield_ratio=_opt_decimal(payload.output_yield_ratio),
        external_source=external.source if external else None,
        external_id=external.id if external else None,
        external_url=external.url if external else None,
        image_url=payload.image_url,
        source=payload.source,
        source_filename=payload.source_filename,
        source_text=source_text,
    )
    if existing is not None:
        row = update_recipe(db, existing.id, **fields)
        created = False
    else:
        row = create_recipe(db, **fields)
        created = True

    if inputs is not None:
        set_recipe_ingredients(db, row.id, inputs)
    return row, flagged, created
