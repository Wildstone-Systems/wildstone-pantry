from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import JSONB, UUID
from app.models.base import Base


class Item(Base):
    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    barcode_type: Mapped[str] = mapped_column(String(10), nullable=False, default="UPC")  # UPC, EAN13, EAN8, ISBN
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_description: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "1 gallon", "16 oz", "12 pack"
    size_quantity: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    size_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)  # gal, oz, lb, ml, l, ea
    # How on-hand is naturally tracked (ADR 0014): 'counted' (whole units — cans,
    # eggs) or 'measured' (by amount — milk, oil; content tracked per container).
    # A display/behaviour default over the lot/container substrate; user-overridable.
    stock_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="counted", server_default="counted"
    )
    # Catch weight (ADR 0025 §1): every package of this product weighs something
    # different, though they all scan the same UPC — a pork butt roast, a wedge
    # of cheese, a whole fish. `size_quantity` demotes to a NOMINAL used for
    # shopping-list pack math and estimates; the real weight is captured per
    # package at intake and lives on the lot, because that is the only place a
    # per-package fact can honestly live. Deliberately a flag rather than a third
    # `stock_mode`: that field is a two-valued display hint (ADR 0014) that
    # intake, shopping and units.py all branch on, and this composes with both
    # values instead of changing what they mean.
    variable_weight: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Base (stored/consumed) unit — how on-hand is tracked and decremented (ADR
    # 0016): count→'each', mass→'g', volume→'ml'. Defaulted from the size dimension,
    # user-overridable. The purchase unit (a package) is `size_quantity size_unit`;
    # this is the unit those contents are stored in. Null = fall back to the default.
    base_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Canonical ingredient key (ADR 0015): a normalised, brand/size-stripped match
    # key so name-divergent records for the same thing ("cumin" / "Lawry's Ground
    # Cumin") resolve to one ingredient. Proposed on create/scan, user-overridable
    # (the confirmed identity). Null = no identity-bearing key → exact-id only.
    canonical_ingredient: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    canonical_provenance: Mapped[str] = mapped_column(String(20), nullable=False, default="generated", server_default="legacy_unknown")
    canonical_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=2)
    canonical_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    canonical_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nutrition: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # nutrition facts per serving
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")  # open_food_facts, kroger, go_upc, manual
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_expiration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "This item's best-by is meaningless" (ADR 0023 D4) — spices, condiments,
    # staples used long past their printed date. Suppresses the item from the
    # expiring feed entirely; a list that nags about paprika every three days
    # teaches the household to ignore the whole list.
    ignore_expiry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Calories per 1 ``unit`` of stock (step 4b). Nullable = unknown; the plate
    # action still records consumption (calories null). Retained for back-compat
    # with the shipped plate/rollup path; the per-100g fields below are the
    # calculation source of truth when present (ADR 0011 D1).
    calories_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Structured nutrition on a per-100g basis (step 5 / ADR 0011 D1) — matches
    # nutrition labels + OpenFoodFacts, so barcode scans auto-fill it. All
    # nullable; a missing field just makes a rollup partial rather than wrong.
    energy_kcal_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    protein_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    fat_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    carbs_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Optional per-serving reference: grams in one "serving" (label serving size).
    serving_size_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Extended per-100g macros captured from OpenFoodFacts alongside the core
    # four above. Nullable — a missing figure just makes a rollup partial.
    fiber_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    sugars_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    saturated_fat_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    sodium_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Product-quality fields from OpenFoodFacts: full ingredient list, cleaned
    # allergen tags, and the three OFF scoring letters/grades.
    ingredients_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    allergens: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    nutri_score: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nova_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eco_score: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # True when the per-100g macros were NOT read from this product's own label
    # but borrowed from a GENERIC USDA match by name (retailer house-brand UPCs
    # often carry no nutrition anywhere). Surfaced as "Estimated" in the UI so an
    # approximation is never mistaken for label-exact data.
    nutrition_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=func.false()
    )
    # When nutrition is estimated, the USDA food it was matched to (description +
    # FDC id) — so a wrong guess ("sour cream" → "heavy cream") is visible and
    # correctable, not silently imported. Null unless nutrition_estimated is set.
    nutrition_source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Identity of a USDA generic food this item was created from (logging a meal
    # with no barcode and no pantry match — "3 hard boiled eggs"). Unique so
    # re-picking the same food across meals reuses one Item instead of creating
    # a duplicate each time. Null for every other item.
    usda_fdc_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    # Last time this item's data was checked/reconciled against an external
    # provider (e.g. a re-scan). Informational only — no scheduled job reads it.
    off_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Unit-conversion hints (ADR 0011 D2). Per-item override; when null the
    # ingredient_reference table is consulted, else conversion returns None.
    density_g_per_ml: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    grams_per_each: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Min-stock level (step 3): when household on-hand drops below this, the item
    # becomes an auto entry on the shopping list. Null = not tracked for restock.
    restock_threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    # Soft-delete: archived items stay in the DB so recipe ingredients, inventory
    # and history keep resolving; they're hidden from default catalog/picker lists
    # and can be reactivated instead of recreated.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=func.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    category = relationship("Category", back_populates="items")
    # Many child tables use ON DELETE CASCADE. Use passive_deletes so SQLAlchemy doesn't
    # try to NULL out non-nullable FKs during deletion (let Postgres handle cascade).
    transactions = relationship("InventoryTransaction", back_populates="item", passive_deletes=True)
    inventory_rows = relationship("Inventory", back_populates="item", passive_deletes=True)
    barcode_aliases = relationship("BarcodeAlias", back_populates="item", passive_deletes=True)
    receipt_lines = relationship("ReceiptLineItem", back_populates="matched_item")
    purchase_lines = relationship("PurchaseLineItem", back_populates="matched_item")
