"""Public DTO interface used by reviewed recipe matching."""

import hashlib
import json
import re

from modules.catalog import match_evidence_repository as repository
from modules.catalog.schemas.match_evidence import MatchCandidate, MatchItemEvidence
from modules.catalog.services.items import list_items
from modules.inventory.services.inventory import list_item_stock_fefo

DESCRIPTORS = {
    "powder",
    "ground",
    "whole",
    "fresh",
    "frozen",
    "raw",
    "cooked",
    "dried",
    "smoked",
    "seasoning",
    "spice",
    "organic",
    "single",
    "ingredient",
    "plain",
    "unsweetened",
    "pure",
}


def item_identity(db, item_id):
    """Public identity DTO; archived items remain visible to legacy mapping reads."""
    item = repository.identity(db, item_id)
    if item is None:
        raise LookupError("Substitution item not found")
    return {"item_id": str(item.id), "canonical_ingredient": item.canonical_ingredient}


def item_base_evidence(db, item_id, *, lock=False):
    item = repository.get(db, item_id, lock=lock)
    if item is None:
        raise LookupError("Active item unavailable")
    nutrients = {
        key: str(getattr(item, key)) if getattr(item, key) is not None else None
        for key in (
            "energy_kcal_100g",
            "protein_g_100g",
            "fat_g_100g",
            "carbs_g_100g",
            "fiber_g_100g",
            "sugars_g_100g",
            "saturated_fat_g_100g",
            "sodium_g_100g",
            "serving_size_g",
        )
    }
    full_source = {
        "name": item.name,
        "brand": item.brand,
        "ingredients_text": item.ingredients_text,
        "source": item.source,
        "canonical": item.canonical_ingredient,
        "revision": item.canonical_revision,
        "canonical_provenance": item.canonical_provenance,
        "canonical_version": item.canonical_version,
        "nutrition_estimated": item.nutrition_estimated,
        "nutrition_source": item.nutrition_source,
        "nutrition_values": nutrients,
    }
    return MatchItemEvidence(
        item_id=item.id,
        name=item.name[:200],
        brand=item.brand,
        ingredients_text=(item.ingredients_text or "")[:2000] or None,
        source=item.source,
        canonical_ingredient=item.canonical_ingredient,
        canonical_provenance=item.canonical_provenance,
        canonical_version=item.canonical_version,
        canonical_revision=item.canonical_revision,
        nutrition_basis="estimated"
        if item.nutrition_estimated
        else "label_or_manual"
        if item.energy_kcal_100g is not None
        else "unknown",
        nutrition_source=item.nutrition_source,
        nutrition_estimated=item.nutrition_estimated,
        nutrition_values=nutrients,
        identity_evidence_truncated=len(item.name) > 200 or len(item.ingredients_text or "") > 2000,
        source_fingerprint=hashlib.sha256(json.dumps(full_source, sort_keys=True).encode()).hexdigest(),
    ).model_dump(mode="json")


def item_evidence(db, item_id, *, lock=False):
    from modules.catalog.services.product_evidence import describe

    base = item_base_evidence(db, item_id, lock=lock)
    base_hash = base["source_fingerprint"]
    structured = describe(db, item_id, base_hash)
    base.update(
        base_source_fingerprint=base_hash,
        structured_evidence=structured,
        source_fingerprint=hashlib.sha256(
            json.dumps({"base": base_hash, "structured": structured}, sort_keys=True).encode()
        ).hexdigest(),
    )
    return base


def unit_evidence(db, item_id, *, lock=False):
    """Published deterministic unit facts used to bind human substitution ratios."""
    from modules.catalog.services.ingredient_reference import resolve_hints

    item = repository.get(db, item_id, lock=lock)
    if item is None:
        raise LookupError("Active item unavailable")
    density, grams_each = resolve_hints(db, item)
    facts = {
        key: getattr(item, key)
        for key in (
            "base_unit",
            "stock_mode",
            "size_quantity",
            "size_unit",
            "density_g_per_ml",
            "grams_per_each",
        )
    }
    facts.update(effective_density_g_per_ml=density, effective_grams_per_each=grams_each)
    return {key: None if value is None else str(value) for key, value in facts.items()}


def stocked(db, item_id):
    return bool(list_item_stock_fefo(db, item_id))


def candidates(db, requested_id, *, query=""):
    target = item_evidence(db, requested_id)
    tokens = set(re.findall(r"[a-z0-9]+", target["name"].lower()))
    meaningful = sorted(tokens - DESCRIPTORS, key=lambda value: (-len(value), value))
    # Default discovery combines a bounded set of identity-bearing words. A
    # modifier such as "unsweetened" must not hide a stocked flavored yogurt.
    # An explicit manual query remains one literal bounded search.
    terms = [query.strip()] if query.strip() else (meaningful[:3] or [target["name"]])
    if not query.strip() and target["canonical_ingredient"] not in (None, "", *terms):
        terms.append(target["canonical_ingredient"])
    rows, seen = [], set()
    for term in terms:
        for row in list_items(db, search=term, in_stock_only=True, limit=50):
            if row.id not in seen:
                rows.append(row)
                seen.add(row.id)
    # Preserve explicit current/confirmed identity before ordinary text ranking.
    if stocked(db, requested_id) and all(row.id != requested_id for row in rows):
        from modules.catalog.services.items import get_item

        rows.insert(0, get_item(db, requested_id))
    ranked = []
    for row in rows:
        item = item_evidence(db, row.id)
        overlap = len(tokens & set(re.findall(r"[a-z0-9]+", item["name"].lower())))
        if row.id == requested_id:
            reason, rank = "same_item", 0
        elif (
            target["canonical_provenance"] == item["canonical_provenance"] == "confirmed"
            and target["canonical_ingredient"]
            and target["canonical_ingredient"] == item["canonical_ingredient"]
        ):
            reason, rank = "confirmed_identity", 1
        elif item["name"].casefold() == target["name"].casefold():
            reason, rank = "exact_name", 2
        else:
            reason, rank = "token_overlap", 3
        ranked.append(
            (
                rank,
                -overlap,
                item["name"].casefold(),
                MatchCandidate(item=item, retrieval_reason=reason).model_dump(mode="json"),
            )
        )
    return [row[3] for row in sorted(ranked, key=lambda row: row[:3])[:5]]
