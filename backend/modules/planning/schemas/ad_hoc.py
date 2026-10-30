"""Ad hoc cooking request/response shapes (ADR 0024).

The session is a draft recipe row, so reads reuse the recipe shapes
(``RecipeRead`` / ``RecipeIngredientRead``); only the entry/finish inputs and
the finish report are new.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from modules.planning.schemas.recipe import (
    FlaggedUnit,
    RecipeIngredientRead,
    RecipeIngredientSpec,
    RecipeRead,
)


class AdHocEntry(BaseModel):
    """One cooking action: ingredient use(s), a step, or both at once —
    "diced the onion" is a single event producing both, and "whisk the flour,
    sugar and salt together" is a single event using THREE ingredients
    (ADR 0024 §2). One action, one entry, however many things went in.
    """

    model_config = ConfigDict(extra="forbid")

    # The same spec as recipe create/import — name-or-id, with the leading
    # "2 avocados" lifting and unit folding. Quantities are the amount that
    # went into the pan, absolute; there is no scaling in ad hoc.
    ingredients: list[RecipeIngredientSpec] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Everything this action used — a dressing's oil, vinegar and mustard "
            "go in one entry, not three. Empty for a step-only entry."
        ),
    )
    # Convenience for the one-thing case: `ingredient` folds into `ingredients`.
    ingredient: RecipeIngredientSpec | None = None
    step_text: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "What you did, as one step ('Whisk the dry ingredients together'). "
            "Appended to the draft's instructions as the next numbered line; inner "
            "line breaks collapse to spaces."
        ),
    )

    @model_validator(mode="after")
    def _fold_singular_and_require_something(self) -> "AdHocEntry":
        if self.ingredient is not None:
            self.ingredients = [*self.ingredients, self.ingredient]
            self.ingredient = None
        if not self.ingredients and not (self.step_text or "").strip():
            raise ValueError("an entry needs an ingredient, a step, or both")
        return self


class AdHocStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        max_length=200,
        description="Optional working title; naming can wait until Finish.",
    )
    # Seed entries let an entry point pre-load the cook — e.g. "use these up"
    # from the expiring list (ADR 0024 §7).
    entries: list[AdHocEntry] = Field(default_factory=list)


class AdHocStateRead(BaseModel):
    """The draft as it stands: the recipe row (steps live in
    ``recipe.instructions`` as numbered lines) plus its merged ingredients."""

    recipe: RecipeRead
    ingredients: list[RecipeIngredientRead]
    flagged_units: list[FlaggedUnit] = Field(default_factory=list)


class AdHocFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servings_made: Decimal = Field(
        default=Decimal(1),
        gt=0,
        description=(
            "How many servings this cook actually produced — becomes the saved "
            "recipe's base yield (known, not guessed; ADR 0024 §2) and the cook "
            "event's servings_made."
        ),
    )
    save: bool = Field(
        default=True,
        description=(
            "True: promote the draft to a real recipe (a name is required) and log "
            "a cook event. False: keep the deductions, delete the draft — the "
            "'just take it off the shelf' outcome (ADR 0024 §4)."
        ),
    )
    name: str | None = Field(default=None, max_length=200)
    # None = deduct pooled first-expiring-first across every location, like a
    # recipe cook with no location pinned.
    location_id: UUID | None = None
    leftover_servings: int = Field(default=0, ge=0)
    note: str | None = None


class AdHocDeduction(BaseModel):
    item_id: UUID
    item_name: str
    quantity: float
    unit: str


class AdHocShortfall(BaseModel):
    """A line the ledger couldn't fully cover. The stock that WAS there has
    been deducted to zero; ``short`` is the remainder — surfaced drift, not an
    error (ADR 0024 §3)."""

    item_name: str
    short: float
    unit: str


class AdHocUnconvertible(BaseModel):
    """A line with no unit bridge — nothing was deducted for it rather than
    guessing a conversion (ADR 0017)."""

    item_name: str
    reason: str


class AdHocFinishResponse(BaseModel):
    recipe_id: UUID
    saved: bool
    # False when nothing could be deducted and no location was given — a cook
    # event needs a location to be stamped with, so it is skipped and said so.
    cook_logged: bool
    cook_event_id: UUID | None = None
    location_id: UUID | None = None
    deducted_transaction_ids: list[UUID] = Field(default_factory=list)
    deducted: list[AdHocDeduction] = Field(default_factory=list)
    shortfalls: list[AdHocShortfall] = Field(default_factory=list)
    unconvertible: list[AdHocUnconvertible] = Field(default_factory=list)
