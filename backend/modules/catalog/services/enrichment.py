"""Give a newly created ingredient something to work with.

Recipe import creates catalogue items from bare names — "ground coriander",
"cilantro" — whenever nothing existing matches. Until now those arrived with
nothing but a name: no macros, no way to convert a teaspoon into the grams the
pantry counts. Measured on the live catalogue, 45 of 168 recipe-referenced items
had no nutrition at all and 49 had no usable conversion, which is exactly the
population that later shows up in the conversion review queue.

Enriching at creation is the cheapest possible moment, and the recipe case is
the *easy* one for a by-name lookup: the name is already the plain food, with no
brand or packaging noise to strip. It is barcoded products where the name fights
you, not these.

Everything here is best-effort. A provider outage, a rate limit or a refusal by
the match gate leaves the item exactly as it would have been — the import never
fails because an enrichment did.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Item
from modules.catalog.services import reference_budget

logger = logging.getLogger(__name__)

# How many freshly created items one request will enrich before giving up and
# leaving the rest to the review queue.
#
# A single recipe usually creates a handful; the shared budget below prevents
# slow nutrition/density transports and retries multiplying across the batch.
# Large imports leave unattempted rows for later review. The
# remainder is not lost: it lands in the conversion review queue, which is built
# for exactly this and paces itself.
MAX_INLINE_ENRICHMENTS = 8
INLINE_REFERENCE_BUDGET_SECONDS = 8.0


def enrich_item(db: Session, item: Item, *, with_density: bool = True) -> dict:
    """Fill in what can be looked up for ``item``. Returns what was filled."""
    filled: dict = {}

    if item.energy_kcal_100g is None and not item.nutrition_estimated:
        try:
            from modules.catalog.services.barcodes import _apply_estimated_nutrition

            source = _apply_estimated_nutrition(db, item)
            if source:
                filled["nutrition"] = source
        except Exception as exc:  # noqa: BLE001 — enrichment must never fail an import
            logger.info("nutrition enrichment skipped for %s: %s", item.name, type(exc).__name__)

    if with_density and item.density_g_per_ml is None and not reference_budget.expired():
        try:
            from modules.catalog.services.conversions import suggest_density

            original_name = item.name
            suggestion = suggest_density(db, item.id)
            # Only a *verified* match is written unattended. An unverified one is
            # a suggestion for a human to look at, and this path has no human in
            # it — so it is left for the review queue rather than committed.
            if suggestion is not None and suggestion.verified:
                result = db.execute(
                    update(Item).where(Item.id == item.id, Item.name == original_name,
                                       Item.density_g_per_ml.is_(None))
                    .values(density_g_per_ml=Decimal(str(suggestion.density_g_per_ml))),
                    execution_options={"synchronize_session": False},
                )
                db.commit()
                db.refresh(item)
                if result.rowcount:
                    filled["density"] = suggestion.source_description
        except Exception as exc:  # noqa: BLE001
            logger.info("density enrichment skipped for %s: %s", item.name, type(exc).__name__)

    return filled


def enrich_new_items(db: Session, items: list[Item], *, with_density: bool = True) -> dict:
    """Enrich up to ``MAX_INLINE_ENRICHMENTS`` of a batch; report what happened.

    The cap is reported rather than applied silently — "we enriched 8 of 30" is
    something a caller can act on, "we enriched some" is not.
    """
    enriched: dict[str, dict] = {}
    attempted = 0
    with reference_budget.budget(INLINE_REFERENCE_BUDGET_SECONDS):
        for item in items[:MAX_INLINE_ENRICHMENTS]:
            if reference_budget.expired():
                break
            attempted += 1
            result = enrich_item(db, item, with_density=with_density)
            if result:
                enriched[item.name] = result
    return {
        "enriched": enriched,
        "skipped": max(0, len(items) - attempted),
    }
