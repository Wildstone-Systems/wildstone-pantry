from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import allow_kiosk, deny_agent_keys, require_authenticated_admin, resolve_actor
from app.db import get_db
from app.models import Recipe
from app.services import provider_keys
from modules.automation import document_bytes_to_text
from modules.catalog.services.barcode_providers import ProviderRateLimited
from modules.catalog.services.identity import IdentityConflict
from modules.catalog.services.units import RECIPE_UNIT_VOCAB
from modules.planning.schemas.recipe import (
    ApproveSubstitutionResponse,
    ConfirmIngredientItemRequest,
    ConfirmIngredientItemResponse,
    FlaggedUnit,
    PendingIngredientMatch,
    PendingIngredientMatchesResponse,
    RecipeAvailabilityResponse,
    RecipeBulkCreate,
    RecipeBulkResponse,
    RecipeBulkResult,
    RecipeCookRequest,
    RecipeCookResponse,
    RecipeCreate,
    RecipeImportDetailRead,
    RecipeIngredientAvailabilityRead,
    RecipeIngredientInput,
    RecipeListRead,
    RecipeMutationRead,
    RecipeNutritionRead,
    RecipeRead,
    RecipeSearchResponse,
    RecipeSearchResultRead,
    RecipeSuggestionRead,
    RecipeSuggestionResponse,
    RecipeValidationLine,
    RecipeValidationMerge,
    RecipeValidationRead,
    RecipeWithIngredientsRead,
)
from modules.planning.schemas.recipe_import import (
    RecipeIngestParsedRequest,
    RecipeIngestParsedResponse,
    RecipeParseResponse,
    RecipeParseTextRequest,
)
from modules.planning.schemas.recipe_review import (
    ImportReviewInput,
    ImportReviewResponse,
    ReviewedImportInput,
)
from modules.planning.services import recipe_review
from modules.planning.services.recipe_images import (
    ImageRejected,
    delete_recipe_image,
    get_recipe_image,
    recipes_with_uploads,
    resolve_image_src,
    set_recipe_image,
)
from modules.planning.services.recipe_import import ingest_parsed_recipe, parse_recipe_raw_text
from modules.planning.services.recipe_providers import active_providers, fetch_recipe_detail, search_recipes
from modules.planning.services.recipe_writer import write_recipe as _write_recipe
from modules.planning.services.recipes import (
    RecipeDeleteBusyError,
    confirm_ingredient_item,
    cook_recipe,
    delete_recipe,
    find_recipe_by_external_identity,
    get_recipe_with_ingredients,
    list_recipes_with_counts,
    pending_ingredient_matches,
    preview_ingredient_specs,
    recipe_availability,
    recipe_nutrition,
    resolve_ingredient_specs,
    set_recipe_ingredients,
    suggest_recipes,
    update_recipe,
)
from modules.planning.services.substitutions import approve_substitution

# Kiosk-open like the rest of the everyday surface (ADR 0004): planning a meal
# from the kitchen tablet must not require an admin login.
router = APIRouter(prefix="/recipes", tags=["recipes"], dependencies=[Depends(allow_kiosk)])


def _opt_decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _resolve_ingredients(db: Session, payload: RecipeCreate) -> tuple[list[dict] | None, list[dict]]:
    """Resolve a payload's ingredient specs to writable rows, before any recipe exists.

    Deliberately runs *ahead* of the recipe write: an unresolvable ingredient used to
    raise after ``create_recipe`` had already committed, leaving an orphan recipe with
    no ingredients — visible in bulk import, where the failed entry still appeared in
    the catalog. Failing here means nothing was written at all.

    ``None`` means the payload omitted ``ingredients`` entirely (metadata-only update:
    leave existing ones untouched), which is distinct from ``[]`` (clear them).
    """
    if payload.ingredients is None:
        return None, []
    inputs, _created, flagged = resolve_ingredient_specs(
        db,
        [spec.model_dump() for spec in payload.ingredients],
        create_missing=payload.create_missing_items,
    )
    return inputs, flagged


@router.get("/unit-vocab")
def recipe_unit_vocab_route() -> dict:
    """The closed set of units a recipe line may use (ADR 0017 §3) — the single
    source of truth for the editor's unit picker."""
    return {"units": RECIPE_UNIT_VOCAB}


def _to_recipe_read(row: Recipe, *, has_upload: bool | None = None) -> RecipeRead:
    # `has_upload=None` means "look it up" — fine for a single recipe, but the
    # list view passes it explicitly from one batched query so rendering N cards
    # doesn't cost N queries.
    if has_upload is None:
        has_upload = False
    return RecipeRead(
        import_review=row.import_review,
        id=row.id,
        source=row.source, source_filename=row.source_filename, source_text=row.source_text,
        name=row.name,
        instructions=row.instructions,
        servings=row.servings,
        output_item_id=row.output_item_id,
        output_quantity=None if row.output_quantity is None else float(row.output_quantity),
        output_unit=row.output_unit,
        output_mode=row.output_mode or "fixed",
        output_yield_ratio=None if row.output_yield_ratio is None else float(row.output_yield_ratio),
        created_at=row.created_at,
        external_source=row.external_source,
        external_id=row.external_id,
        external_url=row.external_url,
        image_src=resolve_image_src(row, has_upload=has_upload),
        is_draft=row.is_draft,
    )


def _to_mutation_read(row: Recipe, flagged: list[dict], *, created: bool = True) -> RecipeMutationRead:
    return RecipeMutationRead(
        **_to_recipe_read(row).model_dump(),
        flagged_units=[FlaggedUnit(**entry) for entry in flagged],
        created=created,
    )


@router.get("", response_model=list[RecipeListRead])
def list_recipes_route(db: Session = Depends(get_db)) -> list[RecipeListRead]:
    rows = list_recipes_with_counts(db)
    uploaded = recipes_with_uploads(db, [row["id"] for row in rows])
    return [
        RecipeListRead(
            id=row["id"],
            name=row["name"],
            instructions=row["instructions"],
            servings=row["servings"],
            output_item_id=row["output_item_id"],
            output_quantity=None if row["output_quantity"] is None else float(row["output_quantity"]),
            output_unit=row["output_unit"],
            output_mode=row["output_mode"] or "fixed",
            output_yield_ratio=(
                None if row["output_yield_ratio"] is None else float(row["output_yield_ratio"])
            ),
            created_at=row["created_at"],
            external_source=row["external_source"],
            external_id=row["external_id"],
            external_url=row["external_url"],
            image_src=(
                f"/api/recipes/{row['id']}/image" if row["id"] in uploaded else row["image_url"]
            ),
            ingredient_count=row["ingredient_count"],
        )
        for row in rows
    ]




@router.post("", response_model=RecipeMutationRead)
def create_recipe_route(payload: RecipeCreate, db: Session = Depends(get_db)) -> RecipeMutationRead:
    """Create a recipe — or update it in place when ``external_source`` names one
    already imported, so re-running a feed converges instead of duplicating."""
    try:
        row, flagged, created = _write_recipe(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_mutation_read(row, flagged, created=created)


@router.post("/bulk", response_model=RecipeBulkResponse)
def bulk_create_recipes_route(payload: RecipeBulkCreate, db: Session = Depends(get_db)) -> RecipeBulkResponse:
    """Import many recipes in one call, reporting per-recipe outcomes.

    Replaces the N+1 an importer otherwise runs. Each recipe is written
    independently — a failure is recorded against its own index and the batch
    continues, so one malformed entry cannot cost the other 199.
    """
    results: list[RecipeBulkResult] = []
    for index, spec in enumerate(payload.recipes):
        try:
            row, flagged, created = _write_recipe(db, spec)
        except (LookupError, IntegrityError, ValueError) as exc:
            db.rollback()
            results.append(RecipeBulkResult(index=index, status="error", name=spec.name, error=str(exc)))
            continue
        results.append(
            RecipeBulkResult(
                index=index,
                status="created" if created else "updated",
                recipe_id=row.id,
                name=row.name,
                flagged_units=[FlaggedUnit(**entry) for entry in flagged],
            )
        )

    return RecipeBulkResponse(
        created=sum(1 for r in results if r.status == "created"),
        updated=sum(1 for r in results if r.status == "updated"),
        failed=sum(1 for r in results if r.status == "error"),
        results=results,
    )


@router.post("/validate", response_model=RecipeValidationRead)
def validate_recipe_route(payload: RecipeCreate, db: Session = Depends(get_db)) -> RecipeValidationRead:
    """Preview what ``POST /api/recipes`` would do with this payload. Writes nothing.

    Lets an importer see the normalisation — which units fold, which get flagged,
    which items would be created, which duplicate lines merge, and whether this
    would upsert an existing recipe — before committing any of it.
    """
    external = payload.external_source
    existing = find_recipe_by_external_identity(
        db,
        external.source if external else None,
        external.id if external else None,
    )
    preview = preview_ingredient_specs(
        db,
        [spec.model_dump() for spec in (payload.ingredients or [])],
        create_missing=payload.create_missing_items,
    )
    return RecipeValidationRead(
        source_lines=recipe_review.structured_validation_lines(payload),
        valid=not preview["errors"],
        would_upsert=existing is not None,
        existing_recipe_id=existing.id if existing else None,
        ingredients=[RecipeValidationLine(**line) for line in preview["ingredients"]],
        merged_ingredients=[RecipeValidationMerge(**row) for row in preview["merged_ingredients"]],
        flagged_units=[FlaggedUnit(**entry) for entry in preview["flagged_units"]],
        would_create_items=preview["would_create_items"],
        errors=preview["errors"],
    )


@router.post("/import-preview", response_model=ImportReviewResponse)
def import_preview_route(payload: ImportReviewInput, db: Session = Depends(get_db)):
    try:
        return recipe_review.preview_import(db, payload)
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/import-review", response_model=ImportReviewResponse, dependencies=[Depends(deny_agent_keys)])
def import_review_route(payload: ImportReviewInput, db: Session = Depends(get_db), actor=Depends(require_authenticated_admin)):
    try:
        return recipe_review.review_import(db, payload)
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/import-reviewed", response_model=RecipeMutationRead, dependencies=[Depends(deny_agent_keys)])
def import_reviewed_route(payload: ReviewedImportInput, db: Session = Depends(get_db), actor=Depends(allow_kiosk)):
    try:
        row, flagged, created = recipe_review.save_reviewed_import(db, payload, actor.id)
        return _to_mutation_read(row, flagged, created=created)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/search-external", response_model=RecipeSearchResponse)
def search_external_recipes_route(
    query: str = Query(min_length=2, max_length=100), db: Session = Depends(get_db)
) -> RecipeSearchResponse:
    """Search external recipe providers by name — "find a chili recipe" — for
    the import flow. TheMealDB is always active (free, no key); Spoonacular
    joins in once its API key is set on the Integrations page (bigger
    catalog)."""
    key = provider_keys.spoonacular_api_key(db)
    hits = search_recipes(query, spoonacular_api_key=key)
    return RecipeSearchResponse(
        results=[
            RecipeSearchResultRead(
                source=h.source,
                external_id=h.external_id,
                name=h.name,
                image_url=h.image_url,
                source_label=h.source_label,
                category=h.category,
                area=h.area,
                servings=h.servings,
                ready_in_minutes=h.ready_in_minutes,
            )
            for h in hits
        ],
        providers_active=active_providers(spoonacular_api_key=key),
    )


@router.get("/search-external/{source}/{external_id}", response_model=RecipeImportDetailRead)
def search_external_recipe_detail_route(
    source: str, external_id: str, db: Session = Depends(get_db)
) -> RecipeImportDetailRead:
    """Full detail for one search hit, already shaped as ``RecipeCreate`` —
    feed ``recipe`` into ``/api/recipes/validate`` to preview, then
    ``POST /api/recipes`` to actually import (its own duplicate-import
    detection via ``external_source`` applies exactly as it does for any
    other importer)."""
    try:
        recipe, image_url = fetch_recipe_detail(
            source, external_id, spoonacular_api_key=provider_keys.spoonacular_api_key(db)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderRateLimited as exc:
        raise HTTPException(status_code=503, detail=f"{source} is rate-limited — try again shortly") from exc
    return RecipeImportDetailRead(recipe=recipe, image_url=image_url)


@router.post("/parse-text", response_model=RecipeParseResponse)
def parse_recipe_text_route(payload: RecipeParseTextRequest, db: Session = Depends(get_db)) -> RecipeParseResponse:
    try:
        parsed = parse_recipe_raw_text(payload.raw_text, db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return RecipeParseResponse.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Parsed ingredient quantities or names need correction; use a positive amount within 3-decimal storage precision and range.") from exc


@router.post("/parse-image", response_model=RecipeParseResponse)
async def parse_recipe_image_route(file: UploadFile = File(...), db: Session = Depends(get_db)) -> RecipeParseResponse:
    """Parse an uploaded recipe. Accepts an image or a PDF (path kept for compatibility)."""

    payload = await file.read()
    try:
        text = document_bytes_to_text(
            payload,
            filename=file.filename,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        parsed = parse_recipe_raw_text(text, db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return RecipeParseResponse.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Parsed ingredient quantities or names need correction; use a positive amount within 3-decimal storage precision and range.") from exc


@router.post("/ingest-parsed", response_model=RecipeIngestParsedResponse)
def ingest_parsed_recipe_route(payload: RecipeIngestParsedRequest, db: Session = Depends(get_db)) -> RecipeIngestParsedResponse:
    try:
        result = ingest_parsed_recipe(
            db,
            title=payload.title,
            instructions=payload.instructions,
            ingredients=[row.model_dump() for row in payload.ingredients],
            source=payload.source,
            source_filename=payload.source_filename,
            source_text=payload.source_text,
            create_missing_items=payload.create_missing_items,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecipeIngestParsedResponse.model_validate(result)


@router.get("/suggestions", response_model=RecipeSuggestionResponse)
def recipe_suggestions_all_locations_route(db: Session = Depends(get_db)) -> RecipeSuggestionResponse:
    """Suggestions scored against stock pooled across every location."""

    rows = suggest_recipes(db, location_id=None)
    return RecipeSuggestionResponse(
        location_id=None,
        suggestions=[RecipeSuggestionRead.model_validate(row) for row in rows],
    )


@router.get("/ingredient-matches", response_model=PendingIngredientMatchesResponse)
def pending_ingredient_matches_route(db: Session = Depends(get_db)) -> PendingIngredientMatchesResponse:
    """Household-wide reconciliation queue: ingredients that are probably already
    in stock under a different product name. Per-recipe availability shows the
    same thing one recipe at a time; this answers each once."""
    return PendingIngredientMatchesResponse(
        matches=[PendingIngredientMatch.model_validate(m) for m in pending_ingredient_matches(db)]
    )


@router.get("/{recipe_id}", response_model=RecipeWithIngredientsRead)
def get_recipe_route(recipe_id: UUID, db: Session = Depends(get_db)) -> RecipeWithIngredientsRead:
    try:
        recipe, ingredients = get_recipe_with_ingredients(db, recipe_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RecipeWithIngredientsRead(
        recipe=_to_recipe_read(recipe, has_upload=get_recipe_image(db, recipe_id) is not None).model_copy(update={"import_review": recipe_review.receipt_read(db, recipe)}),
        ingredients=ingredients,
    )


@router.get("/{recipe_id}/nutrition", response_model=RecipeNutritionRead)
def recipe_nutrition_route(recipe_id: UUID, db: Session = Depends(get_db)) -> RecipeNutritionRead:
    try:
        result = recipe_nutrition(db, recipe_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecipeNutritionRead.model_validate(result)


@router.put("/{recipe_id}", response_model=RecipeMutationRead)
def update_recipe_route(recipe_id: UUID, payload: RecipeCreate, db: Session = Depends(get_db)) -> RecipeMutationRead:
    external = payload.external_source
    try:
        inputs, flagged = _resolve_ingredients(db, payload)
        recipe = update_recipe(
            db,
            recipe_id,
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
            source=payload.source, source_filename=payload.source_filename, source_text=payload.source_text,
        )
        if inputs is not None:
            set_recipe_ingredients(db, recipe_id, inputs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _to_mutation_read(recipe, flagged, created=False)


@router.put("/{recipe_id}/ingredients", response_model=RecipeWithIngredientsRead)
def set_ingredients_route(
    recipe_id: UUID,
    payload: list[RecipeIngredientInput],
    db: Session = Depends(get_db),
) -> RecipeWithIngredientsRead:
    try:
        set_recipe_ingredients(db, recipe_id, [entry.model_dump() for entry in payload])
        recipe, ingredients = get_recipe_with_ingredients(db, recipe_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RecipeWithIngredientsRead(
        recipe=_to_recipe_read(recipe, has_upload=get_recipe_image(db, recipe_id) is not None).model_copy(update={"import_review": recipe_review.receipt_read(db, recipe)}),
        ingredients=ingredients,
    )


@router.delete("/{recipe_id}", status_code=204)
def delete_recipe_route(recipe_id: UUID, db: Session = Depends(get_db)) -> Response:
    try:
        delete_recipe(db, recipe_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecipeDeleteBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/{recipe_id}/cook", response_model=RecipeCookResponse)
def cook_recipe_route(
    recipe_id: UUID,
    payload: RecipeCookRequest,
    db: Session = Depends(get_db),
    actor: str | None = Depends(resolve_actor),
) -> RecipeCookResponse:
    try:
        result = cook_recipe(
            db,
            recipe_id=recipe_id,
            location_id=payload.location_id,
            servings=payload.servings,
            leftover_servings=payload.leftover_servings,
            note=payload.note,
            actor=actor,
            output_weight_grams=_opt_decimal(payload.output_weight_grams),
            substitutions=[s.model_dump() for s in payload.substitutions],
            servings_made=payload.servings_made,
            ingredient_overrides=[o.model_dump() for o in payload.ingredient_overrides],
            output_portions=payload.output_portions,
            output_portion_size=payload.output_portion_size,
            output_portion_unit=payload.output_portion_unit,
            scale_to_ingredient_id=payload.scale_to_ingredient_id,
            allow_short_ingredients=payload.allow_short_ingredients,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RecipeCookResponse.model_validate(result)


@router.get("/{recipe_id}/availability", response_model=RecipeAvailabilityResponse)
def recipe_availability_route(
    recipe_id: UUID,
    location_id: UUID | None = None,
    servings_made: Decimal | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
) -> RecipeAvailabilityResponse:
    """Per-ingredient have/suggested/missing for the recipe view (ADR 0015 Phase 2).
    ``location_id`` omitted = pooled across every location. ``servings_made``
    scales the need to an absolute serving count (ADR 0022 §7); omitted = the
    recipe as written."""
    try:
        result = recipe_availability(
            db, recipe_id, location_id=location_id, servings_made=servings_made
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecipeAvailabilityResponse(
        recipe_id=result["recipe_id"],
        ingredients=[RecipeIngredientAvailabilityRead.model_validate(row) for row in result["ingredients"]],
    )


@router.post(
    "/ingredients/{ingredient_id}/confirm-item",
    response_model=ConfirmIngredientItemResponse,
)
def confirm_ingredient_item_route(
    ingredient_id: UUID, payload: ConfirmIngredientItemRequest, db: Session = Depends(get_db)
) -> ConfirmIngredientItemResponse:
    """Confirm an on-hand product IS this ingredient — learns the alias so it
    identity-matches everywhere (ADR 0015 §5)."""
    try:
        product = confirm_ingredient_item(db, ingredient_id=ingredient_id, item_id=payload.item_id,
            expected_key=payload.expected_canonical_ingredient,
            expected_revision=payload.expected_canonical_revision,
            check_key="expected_canonical_ingredient" in payload.model_fields_set,
            expected_ingredient_key=payload.expected_ingredient_key,
            check_ingredient_key="expected_ingredient_key" in payload.model_fields_set,
            replace_confirmed=payload.replace_confirmed)
    except IdentityConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ConfirmIngredientItemResponse(
        item_id=product.id, canonical_ingredient=product.canonical_ingredient,
        canonical_provenance=product.canonical_provenance, canonical_version=product.canonical_version,
        canonical_revision=product.canonical_revision
    )


@router.post("/substitutions/{substitution_id}/approve", response_model=ApproveSubstitutionResponse)
def approve_substitution_route(
    substitution_id: UUID, db: Session = Depends(get_db)
) -> ApproveSubstitutionResponse:
    """Approve a suggested substitution (ADR 0015 §5) — it then counts toward
    availability and is offered automatically from now on."""
    try:
        sub = approve_substitution(db, substitution_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApproveSubstitutionResponse(substitution_id=sub.id, approved=sub.approved)


@router.get("/suggestions/{location_id}", response_model=RecipeSuggestionResponse)
def recipe_suggestions_route(location_id: UUID, db: Session = Depends(get_db)) -> RecipeSuggestionResponse:
    try:
        rows = suggest_recipes(db, location_id=location_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RecipeSuggestionResponse(
        location_id=location_id,
        suggestions=[RecipeSuggestionRead.model_validate(row) for row in rows],
    )


@router.post("/{recipe_id}/image", response_model=RecipeRead)
async def upload_recipe_image_route(
    recipe_id: UUID, file: UploadFile = File(...), db: Session = Depends(get_db)
) -> RecipeRead:
    """Attach a photo to a recipe, replacing any previous one.

    The file is re-encoded before storage — shrunk to fit, stripped of EXIF (so
    no GPS coordinates ride along) and rotated upright.
    """
    raw = await file.read()
    try:
        set_recipe_image(db, recipe_id, raw)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImageRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recipe = db.get(Recipe, recipe_id)
    return _to_recipe_read(recipe, has_upload=True)


@router.get("/{recipe_id}/image")
def get_recipe_image_route(recipe_id: UUID, db: Session = Depends(get_db)) -> Response:
    row = get_recipe_image(db, recipe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="This recipe has no uploaded picture")
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            # The URL is stable per recipe, so a long cache would serve a stale
            # photo after a re-upload. Revalidating keeps replacement instant
            # while still avoiding a re-download on every render.
            "Cache-Control": "no-cache",
            "Content-Length": str(len(row.data)),
        },
    )


@router.delete("/{recipe_id}/image", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipe_image_route(recipe_id: UUID, db: Session = Depends(get_db)) -> None:
    if not delete_recipe_image(db, recipe_id):
        raise HTTPException(status_code=404, detail="This recipe has no uploaded picture")
