from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

StockMode = Literal["counted", "measured"]


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = None
    barcode: str | None = None
    barcode_type: str = "UPC"
    image_url: str | None = None
    size_description: str | None = None
    size_quantity: float | None = None
    size_unit: str | None = None
    # Omit to let the server infer counted/measured from the package unit.
    stock_mode: StockMode | None = None
    # Catch weight (ADR 0025 §1): every package weighs something different under
    # one UPC. `size_quantity` becomes a nominal and intake asks for the real
    # weight of the package in hand.
    variable_weight: bool = False
    base_unit: str | None = None
    nutrition: dict | None = None
    source: str = "manual"
    category_id: UUID | None = None
    default_expiration_days: int | None = Field(default=None, ge=0)
    restock_threshold: Decimal | None = Field(default=None, ge=0)
    calories_per_unit: Decimal | None = Field(default=None, ge=0)
    energy_kcal_100g: Decimal | None = Field(default=None, ge=0)
    protein_g_100g: Decimal | None = Field(default=None, ge=0)
    fat_g_100g: Decimal | None = Field(default=None, ge=0)
    carbs_g_100g: Decimal | None = Field(default=None, ge=0)
    serving_size_g: Decimal | None = Field(default=None, gt=0)
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    grams_per_each: Decimal | None = Field(default=None, gt=0)
    fiber_g_100g: Decimal | None = Field(default=None, ge=0)
    sugars_g_100g: Decimal | None = Field(default=None, ge=0)
    saturated_fat_g_100g: Decimal | None = Field(default=None, ge=0)
    sodium_g_100g: Decimal | None = Field(default=None, ge=0)
    ingredients_text: str | None = None
    allergens: list[str] | None = None
    nutri_score: str | None = Field(default=None, max_length=32)
    nova_group: int | None = None
    eco_score: str | None = Field(default=None, max_length=32)


class ItemUpdate(BaseModel):
    canonical_ingredient: str | None = Field(default=None, min_length=1, max_length=200)
    confirm_identity_change: bool = False
    expected_canonical_ingredient: str | None = None
    expected_canonical_revision: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand: str | None = None
    barcode: str | None = None
    barcode_type: str | None = None
    image_url: str | None = None
    size_description: str | None = None
    size_quantity: float | None = None
    size_unit: str | None = None
    stock_mode: StockMode | None = None
    variable_weight: bool | None = None
    base_unit: str | None = None
    nutrition: dict | None = None
    source: str | None = None
    category_id: UUID | None = None
    default_expiration_days: int | None = Field(default=None, ge=0)
    restock_threshold: Decimal | None = Field(default=None, ge=0)
    calories_per_unit: Decimal | None = Field(default=None, ge=0)
    energy_kcal_100g: Decimal | None = Field(default=None, ge=0)
    protein_g_100g: Decimal | None = Field(default=None, ge=0)
    fat_g_100g: Decimal | None = Field(default=None, ge=0)
    carbs_g_100g: Decimal | None = Field(default=None, ge=0)
    serving_size_g: Decimal | None = Field(default=None, gt=0)
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    grams_per_each: Decimal | None = Field(default=None, gt=0)
    fiber_g_100g: Decimal | None = Field(default=None, ge=0)
    sugars_g_100g: Decimal | None = Field(default=None, ge=0)
    saturated_fat_g_100g: Decimal | None = Field(default=None, ge=0)
    sodium_g_100g: Decimal | None = Field(default=None, ge=0)
    ingredients_text: str | None = None
    allergens: list[str] | None = None
    nutri_score: str | None = Field(default=None, max_length=32)
    nova_group: int | None = None
    eco_score: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None
    # "This item's best-by is meaningless" (ADR 0023 D4) — suppresses it from the
    # expiring feed. Per item, not per lot: the claim is about the kind of thing,
    # not about one jar.
    ignore_expiry: bool | None = None


class NutritionReferenceEvidence(BaseModel):
    """Reference identity evidence, distinct from nutrition measurements."""

    status: Literal["verified", "human_selected", "label_exact", "manual", "legacy_unknown", "unverified", "rejected", "unavailable"] = "legacy_unknown"
    source: str | None = None
    fdc_id: int | None = None
    description: str | None = None
    why: str = ""


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    canonical_ingredient: str | None = None
    canonical_provenance: Literal["generated", "confirmed", "legacy_unknown"] = "legacy_unknown"
    canonical_version: int | None = None
    canonical_revision: int = 0
    canonical_confirmed_at: datetime | None = None
    name: str
    brand: str | None
    barcode: str | None
    barcode_type: str
    image_url: str | None
    size_description: str | None
    size_quantity: float | None
    size_unit: str | None
    stock_mode: str
    variable_weight: bool = False
    base_unit: str | None = None
    nutrition: dict | None
    source: str
    category_id: UUID | None
    default_expiration_days: int | None
    restock_threshold: float | None
    calories_per_unit: float | None
    energy_kcal_100g: float | None
    protein_g_100g: float | None
    fat_g_100g: float | None
    carbs_g_100g: float | None
    serving_size_g: float | None
    density_g_per_ml: float | None
    grams_per_each: float | None
    fiber_g_100g: float | None
    sugars_g_100g: float | None
    saturated_fat_g_100g: float | None
    sodium_g_100g: float | None
    ingredients_text: str | None
    allergens: list[str] | None
    nutri_score: str | None
    nova_group: int | None
    eco_score: str | None
    ignore_expiry: bool = False
    nutrition_estimated: bool = False
    nutrition_reference: NutritionReferenceEvidence = Field(default_factory=NutritionReferenceEvidence)

    @model_validator(mode="after")
    def read_reference_evidence(self):
        evidence = (self.nutrition or {}).get("_reference_verification")
        if isinstance(evidence, dict):
            try:
                self.nutrition_reference = NutritionReferenceEvidence.model_validate(evidence)
            except ValidationError:
                self.nutrition_reference = NutritionReferenceEvidence(source=self.nutrition_source)
        elif self.nutrition_source:
            self.nutrition_reference = NutritionReferenceEvidence(source=self.nutrition_source)
        return self

    # The USDA food an estimate was matched to (description + FDC id), for auditing.
    nutrition_source: str | None = None
    # Set only for a lightweight item auto-created from a USDA generic food
    # search (logging a meal with no barcode and no pantry match).
    usda_fdc_id: int | None = None
    off_last_checked_at: datetime | None
    is_active: bool
    created_at: datetime


class ItemRescanResult(BaseModel):
    """Outcome of re-querying an item against the external provider."""

    item_id: UUID
    # updated | no_change | not_found | no_barcode | rate_limited
    status: str
    filled: list[str]
    # True when the filled nutrition came from a generic USDA name match rather
    # than the product's own label (ADR: shown as an estimate, e.g. no-UPC eggs).
    estimated: bool = False
    # The matched USDA food (description + FDC id) when estimated — so a wrong
    # match is visible right in the rescan result.
    estimated_source: str | None = None
    reference_review: NutritionReferenceEvidence | None = None
    item: ItemRead | None = None


class NutritionCandidate(BaseModel):
    """One USDA food offered in the nutrition picker, with a macro preview."""

    source: str = "usda_fdc"
    verification_status: Literal["unverified"] = "unverified"
    fdc_id: int | None = None
    description: str | None = None
    data_type: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None


class NutritionCandidatesResponse(BaseModel):
    item_id: UUID
    candidates: list[NutritionCandidate]
    # True when USDA rate-limited the search — the UI says "try again", not "none".
    throttled: bool = False


class ApplyUsdaNutritionRequest(BaseModel):
    fdc_id: int
    # The search term the candidate was picked from (defaults to the item name),
    # so apply reproduces the exact list and matches the chosen food.
    q: str | None = None


class LoggableUnitRead(BaseModel):
    """One unit this item can be logged in, and what ONE of it is worth."""

    unit: str
    grams: Decimal
    calories: Decimal | None = None
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbs_g: Decimal | None = None


class UnresolvableUnitRead(BaseModel):
    unit: str
    reason: str


class LoggableUnitsResponse(BaseModel):
    item_id: UUID
    item_name: str
    units: list[LoggableUnitRead]
    unresolvable: list[UnresolvableUnitRead]
    # The unit a human would most naturally use for this item, or null when
    # nothing converts at all.
    recommended_unit: str | None = None
    # False when no unit yields calories — the item has no per-100g nutrition,
    # so logging it will record the amount but contribute nothing to a total.
    has_nutrition: bool


class FoodSearchResponse(BaseModel):
    """USDA generic foods matching a free-text query — the picker for something
    eaten that's neither in the pantry catalog nor has a barcode."""

    candidates: list[NutritionCandidate]
    # True when USDA rate-limited the search — the UI says "try again", not "none".
    throttled: bool = False
    # The term that actually produced these results. Differs from `q` when an
    # over-specific query was broadened (USDA keyword search is conjunctive, so
    # more words = fewer hits); the UI surfaces it rather than silently
    # answering a different question.
    searched_term: str = ""


class CreateItemFromFoodSearchRequest(BaseModel):
    fdc_id: int
    # The search term the candidate was picked from — re-run to resolve the
    # chosen food (mirrors ApplyUsdaNutritionRequest.q; no item name to fall
    # back to here, so it's required).
    q: str = Field(min_length=1)


class ItemListResponse(BaseModel):
    items: list[ItemRead]


class ItemUsageRead(BaseModel):
    """What exists downstream of an item — i.e. what purging it would destroy."""

    item_id: UUID
    name: str
    is_active: bool
    ledger_events: int = Field(description="Append-only inventory events referencing this item.")
    lots: int
    on_hand: float
    recipe_ingredients: int = Field(description="Recipe lines that would lose their ingredient.")


class ItemPurgeResult(BaseModel):
    """Confirmation of a permanent delete, reporting what actually went."""

    purged: bool
    item_id: UUID
    name: str
    ledger_events: int
    lots: int
    on_hand: float
    recipe_ingredients: int


class DensityPortionRead(BaseModel):
    """One USDA portion the density was derived from — "1 tsp = 2.3 g".

    Shown instead of the raw g/ml because a cook can sanity-check a spoonful
    and cannot sanity-check 0.4666 g/ml.
    """

    measure: str
    amount: float
    grams: float


class DensitySuggestionRead(BaseModel):
    item_id: UUID
    item_name: str
    density_g_per_ml: Decimal
    source_description: str = Field(description="The USDA food this came from.")
    fdc_id: int
    # True = a model confirmed the USDA food is the same food as this item;
    # False = it rejected every candidate; null = no model was consulted, so a
    # human is still the only gate. These mean different things on review.
    verified: bool | None = None
    verified_why: str = ""
    portions: list[DensityPortionRead] = Field(default_factory=list)
    # What the recipe units actually work out to, precomputed for display.
    grams_per_tsp: Decimal | None = None
    grams_per_tbsp: Decimal | None = None
    grams_per_cup: Decimal | None = None


class ApplyDensityRequest(BaseModel):
    density_g_per_ml: Decimal = Field(gt=0)


class ConversionCandidateRead(BaseModel):
    """An item whose volume↔weight conversion is still unknown."""

    item_id: UUID
    name: str
    brand: str | None = None
    base_unit: str | None = None
    stock_mode: str | None = None
    size_quantity: float | None = None
    size_unit: str | None = None
    # Which volume units recipes ask for this in — the reason it's in the queue.
    recipe_units: list[str] = Field(default_factory=list)


class ConversionReviewResponse(BaseModel):
    items: list[ConversionCandidateRead]
    # Whether a model is configured to vet USDA matches. When false, every
    # suggestion comes back unverified and the human is the only gate — worth
    # saying on the page rather than leaving to be inferred.
    judge_available: bool


class DataQualityIssueRead(BaseModel):
    severity: str = Field(description="impossible = refused on new writes; suspect = reported only.")
    field: str
    message: str


class DataQualityRowRead(BaseModel):
    """One item whose stored numbers disagree with themselves."""

    item_id: UUID
    name: str
    brand: str | None = None
    # Provenance travels with the finding: "doubtful" is only actionable when
    # you can see where the value came from.
    estimated: bool = False
    source: str | None = None
    energy_kcal_100g: float | None = None
    protein_g_100g: float | None = None
    fat_g_100g: float | None = None
    carbs_g_100g: float | None = None
    issues: list[DataQualityIssueRead] = Field(default_factory=list)


class DuplicateMacroGroupRead(BaseModel):
    """Items sharing byte-identical nutrition — a placeholder, not a measurement."""

    energy_kcal_100g: float | None = None
    protein_g_100g: float | None = None
    fat_g_100g: float | None = None
    carbs_g_100g: float | None = None
    count: int
    item_ids: list[UUID] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)


class DataQualityResponse(BaseModel):
    items: list[DataQualityRowRead]
    impossible: int = Field(description="How many carry a value that cannot be true of any food.")
    suspect: int
    # Found by comparing items to EACH OTHER, which no per-item check can do:
    # real foods don't agree to the decimal place.
    duplicate_macros: list[DuplicateMacroGroupRead] = Field(default_factory=list)
