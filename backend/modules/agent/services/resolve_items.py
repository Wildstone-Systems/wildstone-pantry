"""Resolve free-text product lines (e.g. from a receipt an agent read) to
catalog items, so the agent can call ``add_inventory`` with real item ids.

Matching and creation go through the catalog's ``item_refs`` service. Unmatched
lines are created only when ``create_missing`` is set — the default is a dry
run that writes nothing. Never touches stock; that stays in ``add_inventory``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from modules.automation import ExpirationEstimator
from modules.catalog.services.item_refs import MATCH_CREATED, create_item_ref, find_item_ref

MATCH_NONE = "not_found"
__all__ = ["MATCH_CREATED", "MATCH_NONE", "resolve_items"]


def _expiry_days(estimator: ExpirationEstimator, ref: dict | None, name: str) -> int:
    """The item's own configured shelf life wins; otherwise the category estimate."""
    if ref is not None and ref["default_expiration_days"] is not None:
        return ref["default_expiration_days"]
    return estimator.estimate(ref["item_name"] if ref else name, ref["category"] if ref else None)


def resolve_items(db: Session, *, lines: list[dict], create_missing: bool = False) -> list[dict]:
    """One result per input line, in the same order.

    Lines repeated in one call (same name/barcode) resolve to the same item, so
    a real run never creates a product twice."""
    estimator = ExpirationEstimator()
    created: dict[str, dict] = {}
    results: list[dict] = []
    for line in lines:
        name = (line.get("name") or "").strip()
        barcode = (line.get("barcode") or "").strip() or None
        key = (barcode or "") + "|" + name.lower()

        ref = find_item_ref(db, name=name, barcode=barcode) or created.get(key)
        if ref is None and create_missing and name:
            ref = create_item_ref(db, name=name, barcode=barcode, brand=line.get("brand"))
            created[key] = ref

        results.append(
            {
                "name": name,
                "barcode": barcode,
                "match": ref["match"] if ref else MATCH_NONE,
                "item_id": ref["item_id"] if ref else None,
                "item_name": ref["item_name"] if ref else None,
                "suggested_expiry_days": _expiry_days(estimator, ref, name),
            }
        )
    return results
