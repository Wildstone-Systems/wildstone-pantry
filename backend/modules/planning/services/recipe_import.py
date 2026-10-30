from __future__ import annotations

from sqlalchemy.orm import Session

from modules.automation import parse_recipe_text
from modules.planning.services.recipes import (
    create_recipe,
    preview_ingredient_specs,
    resolve_ingredient_specs,
    set_recipe_ingredients,
)


def parse_recipe_raw_text(raw_text: str, db: Session | None = None) -> dict:
    parsed = parse_recipe_text(raw_text)
    from modules.planning.schemas.recipe_review import ImportSource
    from modules.planning.services.recipe_review import annotate_source

    source = ImportSource(kind="text", raw_text=raw_text)
    parsed["source_lines"] = annotate_source(source, parsed["ingredients"], title=parsed["title"])
    if db is not None:
        preview = preview_ingredient_specs(db, parsed["ingredients"], create_missing=True)
        for resolved in preview["ingredients"]:
            row = parsed["ingredients"][resolved["index"]]
            row.update({key: resolved[key] for key in (
                "item_id", "resolution", "unit_resolution", "suggestion",
            )})
    return parsed


def ingest_parsed_recipe(
    db: Session, *, title: str, instructions: str | None, ingredients: list[dict],
    source: str, source_filename: str | None, source_text: str | None,
    create_missing_items: bool,
) -> dict:
    # One public resolver for structured and parsed imports, before recipe writes.
    inputs, created_item_ids, flagged_units = resolve_ingredient_specs(
        db, ingredients, create_missing=create_missing_items,
    )
    recipe = create_recipe(
        db, name=title, instructions=instructions,
        source=source or "text", source_filename=source_filename,
        source_text=source_text if source_text is not None else "\n".join(
            row.get("raw_line") or row.get("name") or "" for row in ingredients
        ),
    )
    set_recipe_ingredients(db, recipe.id, inputs)
    return {"recipe_id": recipe.id, "created_item_ids": created_item_ids, "flagged_units": flagged_units}
