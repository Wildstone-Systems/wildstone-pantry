from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from modules.catalog.services.units import to_recipe_unit
from modules.planning.schemas.recipe_source import SourceLine

# "2 avocados", "1.5 lemons", "1/2 onion" — a count the caller left in the name.
# Whitespace after the number is required, so "2% milk" and "7-Up" don't match.
_LEADING_QUANTITY_RE = re.compile(r"^(?P<num>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s+(?P<rest>.+)$")


class RecipeIngredientSpec(BaseModel):
    """An ingredient for create/update — by catalog ``item_id`` OR by ``name``.

    Agent-friendly: pass a name and let the server resolve or create the catalog
    item (``create_missing_items``). Distinct from ``RecipeIngredientInput``
    (the id-only primitive behind ``PUT .../ingredients``).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: UUID | None = None
    name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "The ingredient ALONE — no count, no unit, no preparation. "
            "'avocado', not '2 avocados' / '2 cups flour' / 'onion, diced'. "
            "This string becomes a catalog item and is matched against what you "
            "already own, so anything extra creates an item that can never match. "
            "Put the amount in `quantity` and the unit in `unit`. A leading count "
            "and unit are lifted out automatically as a courtesy, but prose "
            "modifiers are not."
        ),
    )
    # Accept `quantity_required` too: that is the name this same field is *read* back
    # under (RecipeIngredientRead) and the name the id-only PUT primitive takes, so a
    # client that round-trips a GET into a POST used to silently get the 1.0 default.
    quantity: float = Field(
        default=1.0,
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("quantity", "quantity_required"),
        description=(
            "The amount for the ENTIRE recipe as written (matching `servings`), "
            "not per serving. A recipe serving 4 that uses one pound of beef "
            "total sends quantity=1, unit='lb' — not 0.25. Per-serving nutrition "
            "and cook-along scaling both divide by `servings`, so sending "
            "per-serving amounts here is silently wrong by a factor of `servings`."
        ),
    )
    unit: str = Field(default="each", min_length=1, max_length=24)
    raw_line: str | None = None
    source_line_id: str | None = Field(default=None, max_length=64)
    optional: bool = False
    yield_basis: bool | None = Field(
        default=None,
        description=(
            "Mark the line a 'yield_ratio' output is computed from — the meat in a "
            "pulled-pork recipe, so the rub and sauce don't inflate the yield. "
            "Ignored unless the recipe's output_mode is 'yield_ratio'. Omit to "
            "leave an existing line's setting alone (this endpoint upserts)."
        ),
    )
    quantity_mode: Literal["amount", "package"] | None = Field(
        default=None,
        description=(
            "'amount' is the usual thing: `quantity` of `unit`. 'package' means N "
            "WHOLE packages, whatever they weigh — the only honest way to write "
            "'one pork butt roast' when every roast weighs something different. "
            "With 'package', `quantity` is a COUNT of packages and `unit` is "
            "ignored. Omit to leave an existing line's mode alone."
        ),
    )

    @model_validator(mode="after")
    def _need_item_or_name(self) -> RecipeIngredientSpec:
        if self.item_id is None and not (self.name or "").strip():
            raise ValueError("each ingredient needs either item_id or name")
        return self

    @model_validator(mode="after")
    def _lift_leading_quantity(self) -> RecipeIngredientSpec:
        """Pull a count out of the NAME when the caller left it there.

        Agents routinely send ``{"name": "2 avocados"}`` with no quantity. That
        created a catalog item literally called "2 avocados" — junk that can
        never match the "avocado" on your shelf — and recorded the amount as the
        1.0 default, so the recipe silently asked for half what it needed.

        Only fires when the quantity is still the default, so an explicit
        quantity always wins. Requires whitespace after the number, which is
        what keeps real product names intact: "2% milk" (no space) and "7-Up"
        (no space) are untouched.
        """
        if self.item_id is not None or "quantity" in self.model_fields_set:
            return self
        raw = (self.name or "").strip()
        match = _LEADING_QUANTITY_RE.match(raw)
        if not match:
            return self
        remainder = match.group("rest").strip()
        if not remainder:
            return self  # a name that was ONLY a number — leave it to fail loudly
        value = match.group("num")
        try:
            parsed = sum(float(Fraction(part)) for part in value.split())
        except (ValueError, ZeroDivisionError):
            return self
        if parsed <= 0:
            return self

        # "2 cups flour" — the unit usually rides along with the count. Only
        # taken when the caller didn't state a unit, and never if it would
        # leave the name empty ("2 cups" alone stays a name).
        if "unit" not in self.model_fields_set:
            head, _, tail = remainder.partition(" ")
            tail = tail.strip()
            if tail and (folded := to_recipe_unit(head)):
                self.unit = folded
                remainder = tail

        self.raw_line = self.raw_line or raw
        self.quantity = parsed
        self.name = remainder
        return self

    @model_validator(mode="after")
    def _optional_context(self) -> RecipeIngredientSpec:
        if self.item_id is None and self.name:
            cleaned = re.sub(r"(?:,?\s*to taste|[,\s]*\(?optional\)?)\s*$", "", self.name, flags=re.I).strip()
            if cleaned and cleaned != self.name:
                self.raw_line = self.raw_line or self.name
                self.name = cleaned
                self.optional = True
        return self

    @model_validator(mode="after")
    def _storage_quantity(self) -> RecipeIngredientSpec:
        value = Decimal(str(self.quantity))
        if not value.is_finite() or not Decimal("0") < value <= Decimal("999999999.999"):
            raise ValueError("ingredient quantity exceeds positive Numeric(12,3) storage range")
        rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if rounded <= 0:
            raise ValueError("ingredient quantity rounds to zero at storage precision")
        # Normalizing storage precision must not turn an omitted default into
        # evidence that the source explicitly supplied a quantity.
        object.__setattr__(self, "quantity", float(rounded))
        return self

    @field_validator("unit")
    @classmethod
    def _normalise_unit(cls, value: str) -> str:
        # Lenient (agent/import path): fold known aliases to the vocabulary; keep an
        # unrecognised token as-is so it surfaces as "can't tell" and gets flagged.
        return to_recipe_unit(value) or value


class ExternalSource(BaseModel):
    """Where an imported recipe came from upstream, and its id there.

    Supplying this on ``POST /api/recipes`` makes the write an **upsert**: a second
    import of ``{"source": "themealdb", "id": "52802"}`` updates the recipe it
    created the first time instead of adding a duplicate. Both halves are required —
    a source with no id identifies nothing.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=40)
    id: str = Field(min_length=1, max_length=120)
    url: str | None = Field(default=None, max_length=500)


class RecipeCreate(BaseModel):
    # extra="forbid": an unknown field is a 422, not a silent drop — so a wrong
    # payload fails loudly instead of quietly losing data.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    instructions: str | None = Field(
        default=None,
        description=(
            "Plain text, ONE STEP PER LINE.\n"
            "\n"
            "Numbering is recognised with or without a delimiter — '1.', '2)', "
            "'Step 3:' and a bare '4 ' all work — but it must run SEQUENTIALLY "
            "FROM 1. That sequence is how a real step is told apart from a line "
            "that merely starts with a digit, so out-of-order numbers are "
            "treated as text, not as new steps.\n"
            "\n"
            "A line that isn't the next step in sequence is appended to the step "
            "above it, so wrapped/continued lines are safe.\n"
            "\n"
            "DO NOT interleave ingredient lines into the instructions (scraped "
            "imports often do: '2 (1 inch) pieces ginger root' sitting between "
            "steps 2 and 3). They survive the sequence rule but get glued onto "
            "the preceding step. Ingredients belong in `ingredients`.\n"
            "\n"
            "Unnumbered text falls back to blank-line paragraphs, then single "
            "lines; a single unbroken paragraph renders as one step. No HTML."
        ),
    )
    servings: int = Field(
        default=1,
        ge=1,
        description=(
            "How many servings this recipe AS WRITTEN produces — the yield the "
            "ingredient quantities correspond to. This is the divisor for "
            "per-serving nutrition and the basis for cook-along scaling, so an "
            "inaccurate value silently skews both. If the source doesn't state a "
            "yield, estimate from the ingredient amounts rather than defaulting to 1."
        ),
    )
    output_item_id: UUID | None = None
    output_quantity: float | None = Field(default=None, gt=0)
    output_unit: str | None = Field(default=None, max_length=24)
    # How the yield is worked out (ADR 0025 §3). 'fixed' multiplies
    # output_quantity by the servings cooked. 'yield_ratio' derives it from the
    # mass actually consumed by the lines marked `yield_basis`, so the same
    # recipe against a 4.87 lb roast and a 9 lb roast produces two different,
    # correct numbers. A weighed dish (cook's output_weight_grams) beats both.
    # Null means "leave it as it is", which is what an editor that predates
    # these fields sends. Defaulting to "fixed" here would make every unrelated
    # save silently demote a yield_ratio recipe back to a fixed one.
    output_mode: Literal["fixed", "yield_ratio"] | None = None
    output_yield_ratio: float | None = Field(default=None, gt=0, le=1)
    # Optional ingredients set in the same call. None = leave ingredients
    # untouched (metadata-only update); [] = clear them.
    ingredients: list[RecipeIngredientSpec] | None = None
    create_missing_items: bool = True
    external_source: ExternalSource | None = None
    source: str | None = Field(default=None, max_length=32)
    source_filename: str | None = Field(default=None, max_length=256)
    source_text: str | None = None
    image_url: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "URL of a picture of the finished dish, if the source has one. "
            "Stored as-is and rendered directly, so it must be a publicly "
            "reachable image URL, not a page that contains one. A photo "
            "uploaded later via POST /recipes/{id}/image takes precedence."
        ),
    )


class RecipeRead(BaseModel):
    import_review: dict | None = None
    source: str | None = None
    source_filename: str | None = None
    source_text: str | None = None
    id: UUID
    name: str
    instructions: str | None
    servings: int
    output_item_id: UUID | None = None
    output_quantity: float | None = None
    output_unit: str | None = None
    output_mode: str = "fixed"
    output_yield_ratio: float | None = None
    created_at: datetime
    external_source: str | None = None
    external_id: str | None = None
    external_url: str | None = None
    # Where to render this recipe's picture from, already resolved: an uploaded
    # photo wins over an imported one, and null means there is no picture. The
    # client renders this and never has to know which of the two it got.
    image_src: str | None = None
    # True while this row is an in-progress ad hoc cook (ADR 0024 §1). Drafts
    # only surface via the ad hoc endpoints; on every other read this is False.
    is_draft: bool = False


class RecipeListRead(RecipeRead):
    """Recipe plus its ingredient count, for the list view."""

    ingredient_count: int = 0


class FlaggedUnit(BaseModel):
    """A unit the import path could not resolve to the vocabulary (ADR 0017 §3).

    The line is still stored — the flag path deliberately does not fail the whole
    recipe — but it will read as "can't tell" at cook time until the unit is
    remapped. ``suggestion`` is advisory and never auto-applied.
    """

    index: int
    item_name: str | None = None
    unit: str
    suggestion: str | None = None


class RecipeMutationRead(RecipeRead):
    """A created/updated recipe, plus anything the server could not fully resolve.

    Subclasses ``RecipeRead`` so existing clients see the same fields they always
    did; ``flagged_units`` and ``created`` are additive.
    """

    flagged_units: list[FlaggedUnit] = Field(default_factory=list)
    # False when an `external_source` match turned this POST into an update — the
    # signal an importer needs to tell "added 40 recipes" from "re-ran the same feed".
    created: bool = True


class RecipeBulkCreate(BaseModel):
    """A batch of recipes to import in one call.

    Each entry is validated and written independently: one bad recipe reports its
    own error and the rest still land. That is the point of the endpoint — the
    per-recipe N+1 it replaces had the same property, and a batch that aborts
    wholesale on entry 37 would be strictly worse than the loop it replaces.
    """

    model_config = ConfigDict(extra="forbid")

    recipes: list[RecipeCreate] = Field(min_length=1, max_length=200)


class RecipeBulkResult(BaseModel):
    index: int
    status: str  # created | updated | error
    recipe_id: UUID | None = None
    name: str | None = None
    flagged_units: list[FlaggedUnit] = Field(default_factory=list)
    error: str | None = None


class RecipeBulkResponse(BaseModel):
    created: int
    updated: int
    failed: int
    results: list[RecipeBulkResult]


class RecipeValidationLine(BaseModel):
    quantity_mode: Literal["amount", "package"] = "amount"
    yield_basis: bool = False
    raw_line: str | None = None
    optional: bool = False
    index: int
    name: str | None = None
    item_id: UUID | None = None
    resolution: str  # by_id | matched | would_create | unresolved
    quantity: float
    unit: str
    unit_resolution: str  # ok | flagged
    suggestion: str | None = None


class RecipeValidationMerge(BaseModel):
    quantity_mode: Literal["amount", "package"] = "amount"
    yield_basis: bool = False
    name: str | None = None
    from_indexes: list[int]
    quantity: float
    unit: str
    optional: bool = False


class RecipeValidationRead(BaseModel):
    source_lines: list[SourceLine] = Field(default_factory=list)
    """What ``POST /api/recipes`` *would* do with this payload — nothing is written.

    ``valid`` is false when the payload would fail outright (an unknown name with
    ``create_missing_items`` off). Flagged units do **not** make a payload invalid:
    the import path stores those lines by design (ADR 0017 §3), so they are a
    warning to act on, not a rejection.
    """

    valid: bool
    would_upsert: bool
    existing_recipe_id: UUID | None = None
    ingredients: list[RecipeValidationLine] = Field(default_factory=list)
    merged_ingredients: list[RecipeValidationMerge] = Field(default_factory=list)
    flagged_units: list[FlaggedUnit] = Field(default_factory=list)
    would_create_items: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RecipeSearchResultRead(BaseModel):
    source: str
    external_id: str
    name: str
    image_url: str | None = None
    source_label: str
    category: str | None = None
    area: str | None = None
    servings: int | None = None
    ready_in_minutes: int | None = None


class RecipeSearchResponse(BaseModel):
    results: list[RecipeSearchResultRead]
    # Which providers actually ran — lets the client explain a thin result set
    # ("only TheMealDB is active; add a Spoonacular key for more") rather than
    # silently looking like a bad search.
    providers_active: list[str]


class RecipeImportDetailRead(BaseModel):
    """A search hit's full detail, already shaped as ``RecipeCreate`` — the
    caller feeds ``recipe`` straight into the existing
    ``POST /api/recipes/validate`` then ``POST /api/recipes``, so importing a
    search result reuses every bit of the existing create/validate path
    (unit folding, item matching, external_source upsert) with nothing new to
    maintain on the write side."""

    recipe: RecipeCreate
    image_url: str | None = None


class RecipeIngredientInput(BaseModel):
    item_id: UUID
    quantity_required: float = Field(gt=0)
    unit: str = Field(default="each", min_length=1, max_length=24)
    optional: bool = False
    # Null means "leave whatever this line already had". The recipe editor
    # rewrites every line on save and predates both fields, so defaulting them
    # here would erase a yield basis and demote a whole-package line to an
    # amount line on any unrelated edit.
    yield_basis: bool | None = None
    quantity_mode: Literal["amount", "package"] | None = None

    @field_validator("unit")
    @classmethod
    def _require_vocab_unit(cls, value: str) -> str:
        # Strict (manual editor path, ADR 0017 §3): the editor's unit picker only
        # offers vocabulary units, so a non-vocabulary token here is a client bug —
        # reject it rather than store something that can never convert.
        canonical = to_recipe_unit(value)
        if canonical is None:
            raise ValueError(f"'{value}' is not a supported recipe unit")
        return canonical


class RecipeIngredientRead(BaseModel):
    ingredient_id: UUID
    item_id: UUID
    item_name: str
    quantity_required: str
    unit: str
    optional: bool = False
    yield_basis: bool = False
    quantity_mode: str = "amount"


class RecipeWithIngredientsRead(BaseModel):
    recipe: RecipeRead
    ingredients: list[RecipeIngredientRead]


class RecipeCookSubstitution(BaseModel):
    original_item_id: UUID
    substitute_item_id: UUID
    ratio: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=24)


class RecipeCookIngredientOverride(BaseModel):
    """One ingredient line's amount for THIS cook only (ADR 0022 §2).

    Never edits the recipe — it records what you actually used this time.
    Keyed by the ingredient ROW id (not item_id): a recipe may legitimately list
    the same item on two lines, and only the row id tells them apart.
    """

    model_config = ConfigDict(extra="forbid")

    ingredient_id: UUID
    # The FINAL amount to use — scaling is NOT applied on top of this. You are
    # editing the already-scaled number shown on screen, so what you type is
    # what gets deducted (ADR 0022 §2).
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=24)
    # Leave this ingredient out entirely: no deduction, and no shortfall error
    # if it isn't in stock.
    omit: bool = False


class RecipeCookRequest(BaseModel):
    # None = cook from the whole household: ingredients are drawn
    # first-expiring-first across every location.
    location_id: UUID | None = None
    # BATCH MULTIPLIER — `2` means "make two whole recipes" and deducts twice
    # every ingredient amount. It is NOT "servings to make"; for that use
    # `servings_made` below (ADR 0022 §1). Kept as-is so existing callers
    # (including the MCP `cook_recipe` agent tool) keep their exact behaviour.
    servings: int = Field(default=1, ge=1)
    # ABSOLUTE servings to produce. Scales ingredients by
    # `servings_made / recipe.servings`, so asking for 2 servings of a recipe
    # that yields 4 deducts half of each amount. When set, `servings` is
    # ignored. Decimal because "1.5 servings" is a real thing.
    servings_made: Decimal | None = Field(default=None, gt=0)
    leftover_servings: int = Field(default=0, ge=0)
    note: str | None = None
    # What the finished dish ACTUALLY weighed. Authoritative when given — it
    # replaces the recipe's declared yield rather than scaling it (ADR 0025 §3),
    # so a 4.87 lb roast and a 9 lb roast no longer produce the same number.
    output_weight_grams: float | None = Field(default=None, gt=0)
    # How the yield was packed (ADR 0025 §4). `output_portions` alone splits it
    # evenly; adding `output_portion_size` makes that many containers of exactly
    # that size and leaves the rest loose (the three tubs in the fridge plus
    # what's still in the serving dish). Opt-in per cook — there is deliberately
    # no recipe-level default, because how a batch gets divided is a property of
    # the evening, not of the recipe.
    output_portions: int | None = Field(default=None, ge=1)
    output_portion_size: Decimal | None = Field(default=None, gt=0)
    output_portion_unit: str | None = Field(default=None, max_length=24)
    # Size the whole batch from what's on hand for ONE line — "use all the pork
    # I have" (ADR 0025 §5). Wins over `servings` and `servings_made`: it is the
    # most specific thing a cook can say about how much they're making.
    scale_to_ingredient_id: UUID | None = None
    # When a line comes up short, take what's on hand instead of refusing the
    # cook (ADR 0025 §7). Pairs with scale_to_ingredient: sizing the batch to a
    # 5 lb roast asks for more rub than the jar holds, and "season it a little
    # lighter" is a real answer. Every trimmed line comes back in
    # `capped_ingredients` — the proportions did change.
    allow_short_ingredients: bool = False
    substitutions: list[RecipeCookSubstitution] = Field(default_factory=list)
    ingredient_overrides: list[RecipeCookIngredientOverride] = Field(default_factory=list)


class CappedIngredient(BaseModel):
    """A line that was trimmed to what was actually on hand (ADR 0025 §7)."""

    ingredient_id: UUID | None = None
    item_name: str
    wanted: Decimal
    took: Decimal
    unit: str


class RecipeCookResponse(BaseModel):
    cook_event_id: UUID
    recipe_id: UUID
    location_id: UUID
    deducted_transaction_ids: list[UUID]
    leftover_servings: int
    produced_item_id: UUID | None = None
    produced_quantity: float | None = None
    produced_unit: str | None = None
    produced_balance: float | None = None
    # Present only when the cook portioned its yield: the containers that were
    # packed (each individually finishable/discardable), and how much stayed loose.
    produced_portions: int | None = None
    produced_portion_size: float | None = None
    produced_portion_unit: str | None = None
    produced_loose: float | None = None
    # The batch multiplier this cook actually ran at — with scale_to_ingredient
    # the pantry chose it, not the caller.
    scale_used: float | None = None
    # Lines trimmed to what was on the shelf (only ever non-empty when
    # allow_short_ingredients was set).
    capped_ingredients: list[CappedIngredient] = Field(default_factory=list)


class RecipeNutrientTotals(BaseModel):
    calories: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None


class RecipeUnresolvedIngredient(BaseModel):
    item_id: UUID
    item_name: str
    reason: str


class RecipeNutritionRead(BaseModel):
    recipe_id: UUID
    servings: int
    total: RecipeNutrientTotals
    per_serving: RecipeNutrientTotals
    unresolved: list[RecipeUnresolvedIngredient]
    complete: bool


class RecipeSuggestionSubstitution(BaseModel):
    missing_item: str
    substitute_item: str
    outcome: str


class RecipeSuggestionRead(BaseModel):
    recipe_id: UUID
    recipe_name: str
    available_score: float
    missing_items: list[str]
    substitutions_used: list[RecipeSuggestionSubstitution] = Field(default_factory=list)


class RecipeSuggestionResponse(BaseModel):
    # None = scored against stock pooled across every location.
    location_id: UUID | None = None
    suggestions: list[RecipeSuggestionRead]


class IngredientMatchSuggestion(BaseModel):
    """An on-hand product proposed as a tight-subset match for an ingredient
    (ADR 0015 §2) — confirm to alias it, never auto-counted."""

    item_id: UUID
    item_name: str
    canonical_ingredient: str | None = None
    canonical_provenance: str = "legacy_unknown"
    canonical_version: int | None = None
    canonical_revision: int = 0
    on_hand: str


class SubstituteAvailable(BaseModel):
    """An approved substitute that is on hand and satisfies the ingredient
    (ADR 0015 §4) — shown transparently ("using plain greek yogurt")."""

    to_label: str
    to_canonical: str | None = None
    ratio: str
    outcome: str
    on_hand: str


class SubstitutionSuggestion(BaseModel):
    """A seeded/unapproved substitution whose ``to`` is on hand — offered to
    approve (ADR 0015 §5). Approving flips the ingredient missing → available."""

    substitution_id: UUID
    to_label: str
    to_canonical: str | None = None
    ratio: str
    outcome: str
    on_hand: str


class RecipeIngredientAvailabilityRead(BaseModel):
    ingredient_id: UUID
    item_id: UUID
    item_name: str
    canonical_ingredient: str | None = None
    canonical_provenance: str = "legacy_unknown"
    canonical_version: int | None = None
    canonical_revision: int = 0
    quantity_required: str
    unit: str
    # 'package' lines are COUNTED, not measured: `unit` reads "package" and
    # on_hand is how many whole packages are on the shelf (ADR 0025 §5).
    quantity_mode: str = "amount"
    on_hand: str
    # ADR 0017: the item's base unit and on-hand rendered back in the recipe's own
    # unit for display ("have ~0.8 cup"). null when there's no bridge to convert.
    base_unit: str | None = None
    on_hand_recipe_unit: str | None = None
    status: str  # have | substitute | suggested | missing | cant_convert | optional
    available_via: SubstituteAvailable | None = None
    suggestions: list[IngredientMatchSuggestion] = Field(default_factory=list)
    substitution_suggestions: list[SubstitutionSuggestion] = Field(default_factory=list)


class PendingMatchCandidate(BaseModel):
    item_id: UUID
    item_name: str
    canonical_ingredient: str | None = None
    canonical_provenance: str = "legacy_unknown"
    canonical_version: int | None = None
    canonical_revision: int = 0


class PendingIngredientMatch(BaseModel):
    """A recipe ingredient that is probably already on the shelf under another
    name. Confirming one rewrites the PRODUCT's identity key, so it resolves
    every recipe using that ingredient at once."""

    ingredient_id: UUID
    recipe_id: UUID
    item_id: UUID
    item_name: str
    canonical_ingredient: str | None = None
    canonical_provenance: str = "legacy_unknown"
    canonical_version: int | None = None
    canonical_revision: int = 0
    recipe_names: list[str]
    candidates: list[PendingMatchCandidate]


class PendingIngredientMatchesResponse(BaseModel):
    matches: list[PendingIngredientMatch]


class RecipeAvailabilityResponse(BaseModel):
    recipe_id: UUID
    ingredients: list[RecipeIngredientAvailabilityRead]


class ConfirmIngredientItemRequest(BaseModel):
    item_id: UUID
    expected_canonical_ingredient: str | None = None
    expected_canonical_revision: int | None = Field(default=None, ge=0)
    expected_ingredient_key: str | None = None
    replace_confirmed: bool = False


class ConfirmIngredientItemResponse(BaseModel):
    item_id: UUID
    canonical_ingredient: str | None = None
    canonical_provenance: str = "legacy_unknown"
    canonical_version: int | None = None
    canonical_revision: int = 0


class ApproveSubstitutionResponse(BaseModel):
    substitution_id: UUID
    approved: bool
