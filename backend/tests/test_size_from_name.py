"""Parsing a package size out of a product name (barcode-provider fallback).

Providers (esp. USDA) often carry the size only in the name — "... 1 Gallon" —
with no structured size field. We pull it out, but must not grab the "2%".
"""

from __future__ import annotations

from modules.catalog.services.barcode_providers import _size_from_name


def test_extracts_trailing_gallon_not_the_percent() -> None:
    qty, unit, _desc = _size_from_name("Prairie Farms Reduced Fat 2% Milk 1 Gallon")
    assert (qty, unit) == (1.0, "gal")


def test_extracts_oz_size() -> None:
    qty, unit, _desc = _size_from_name("Great Value 2% Milk 16 oz")
    assert (qty, unit) == (16.0, "oz")


def test_ignores_count_units() -> None:
    # "12 ct" is a count, not a package size.
    assert _size_from_name("Yogurt Cups 12 ct") == (None, None, None)


def test_no_size_in_name() -> None:
    assert _size_from_name("Just Plain Milk") == (None, None, None)
    assert _size_from_name("") == (None, None, None)
