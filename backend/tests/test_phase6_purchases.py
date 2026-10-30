from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.shopping.services.purchases import parse_purchase_csv


def test_phase6_parse_purchase_csv_with_headers() -> None:
    csv_text = "name,quantity,unit,barcode\nMilk,2,each,0123456789\nBread,1,each,\n"
    rows = parse_purchase_csv(csv_text, default_unit="each")
    assert len(rows) == 2
    assert rows[0]["name"] == "Milk"
    assert rows[0]["quantity"] == Decimal("2")
    assert rows[0]["unit"] == "each"
    assert rows[0]["barcode"] == "0123456789"


def test_phase6_parse_purchase_csv_without_headers() -> None:
    csv_text = "Milk,2,each,0123456789\nBread,1,each\n"
    rows = parse_purchase_csv(csv_text, default_unit="each")
    assert len(rows) == 2
    assert rows[1]["name"] == "Bread"
    assert rows[1]["quantity"] == Decimal("1")
    assert rows[1]["unit"] == "each"


def test_phase6_parse_purchase_csv_flexible_headers() -> None:
    csv_text = "Item,Qty,UOM,UPC\nBananas,6,each,4011\n"
    rows = parse_purchase_csv(csv_text, default_unit="each")
    assert len(rows) == 1
    assert rows[0]["name"] == "Bananas"
    assert rows[0]["quantity"] == Decimal("6")
    assert rows[0]["unit"] == "each"
    assert rows[0]["barcode"] == "4011"


def test_phase6_parse_purchase_csv_bad_quantity_defaults_to_one() -> None:
    csv_text = "name,quantity,unit\nMilk,not-a-number,each\n"
    rows = parse_purchase_csv(csv_text, default_unit="each")
    assert len(rows) == 1
    assert rows[0]["quantity"] == Decimal("1")
