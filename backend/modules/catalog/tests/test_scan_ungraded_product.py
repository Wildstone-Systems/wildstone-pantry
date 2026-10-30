"""Scanning a product OFF cannot grade must not 500 (GH regression).

Open Food Facts returns ``nutri_score`` / ``eco_score`` grades of ``a``-``e``
for products it can rate, but ``unknown`` or ``not-applicable`` for the many it
cannot. 0026 sized both columns at String(1), so autocreating such an item on
scan raised ``value too long for type character varying(1)`` and the scan
500'd — which is most real products (e.g. an energy drink). 0027 widens them.
"""

from __future__ import annotations

import httpx
import pytest

from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import ProductResult


def _ungraded(barcode: str) -> ProductResult:
    # Shape mirrors the Celsius scan from the field report: real macros, but a
    # product OFF declines to grade.
    return ProductResult(
        barcode=barcode,
        name="Celsius",
        brand="Celsius Live Fit",
        category="Beverages",
        size_description="16 fl oz",
        nutrition={"energy-kcal_100g": 2, "carbohydrates_100g": 0},
        source="open_food_facts",
        nutri_score="unknown",
        eco_score="not-applicable",
        nova_group=4,
    )


@pytest.mark.parametrize("nutri,eco", [("unknown", "not-applicable"), ("a", "e"), ("unknown", "unknown")])
def test_scan_of_an_ungraded_product_autocreates_without_error(
    client: httpx.Client, monkeypatch, nutri: str, eco: str
) -> None:
    result = _ungraded("0852480007112")
    result.nutri_score = nutri
    result.eco_score = eco
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: result)

    resp = client.post("/api/barcodes/scan", json={"barcode": "852480007112", "auto_create": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_name"] == "Celsius"

    item_id = resp.json()["item_id"]
    item = client.get(f"/api/items/{item_id}").json()
    assert item["nutri_score"] == nutri
    assert item["eco_score"] == eco
