"""Scanning the same pack twice must not 500 (GTIN width mismatch).

A scanner reads the 12 digits printed on a US pack (UPC-A); Open Food Facts
returns the 13-digit EAN with a leading zero. Storing the provider's spelling
while looking up the scanner's made the item invisible to the scanner that
created it, so every re-scan re-fetched externally and tried to insert the same
barcode again — a unique violation surfacing as a 500, forever.
"""

from __future__ import annotations

import httpx
import pytest

from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcodes import barcode_variants

UPC_A = "681131328845"
EAN_13 = "0681131328845"


def _seed_item(client: httpx.Client, *, name: str, barcode: str) -> str:
    resp = client.post("/api/items", json={"name": name, "barcode": barcode})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_variants_bridge_upc_and_ean_widths() -> None:
    assert EAN_13 in barcode_variants(UPC_A)
    assert UPC_A in barcode_variants(EAN_13)


def test_short_internal_codes_are_not_zero_padded_into_collisions() -> None:
    assert barcode_variants("12345") == ["12345"]


@pytest.mark.parametrize(
    ("stored", "scanned"),
    [(EAN_13, UPC_A), (UPC_A, EAN_13), (EAN_13, EAN_13), (UPC_A, UPC_A)],
)
def test_lookup_matches_across_gtin_widths(client: httpx.Client, stored: str, scanned: str) -> None:
    _seed_item(client, name="Broccoli Florets", barcode=stored)

    body = client.get(f"/api/barcodes/lookup/{scanned}").json()
    assert body["found"] is True
    # Resolved locally: no external provider call, so no duplicate insert.
    assert body["source"] == "item.barcode"
    assert body["item_name"] == "Broccoli Florets"


def test_rescanning_a_known_pack_is_idempotent(client: httpx.Client, monkeypatch) -> None:
    """The exact failure: item stored as EAN-13, scanner sends UPC-A, repeatedly."""

    # This item has no nutrition data, so a re-scan will try to reconcile it
    # against the external provider (GH follow-up) — keep that offline/deterministic.
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: None)

    _seed_item(client, name="Broccoli Florets", barcode=EAN_13)

    for _ in range(3):
        resp = client.post("/api/barcodes/scan", json={"barcode": UPC_A, "auto_create": True})
        assert resp.status_code == 200, resp.text
        assert resp.json()["created"] is False
        assert resp.json()["item_name"] == "Broccoli Florets"

    items = client.get("/api/items").json()["items"]
    assert len([row for row in items if row["name"] == "Broccoli Florets"]) == 1
