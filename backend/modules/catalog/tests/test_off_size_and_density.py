"""OFF package-size parsing, energy-derived density, and rescan backfill."""

from __future__ import annotations

import pytest

from modules.catalog.services.barcode_providers import (
    _derive_density_from_energy,
    _estimate_liquid_density,
    _parse_quantity,
    _parse_serving_grams,
)


@pytest.mark.parametrize(
    ("text", "qty", "unit"),
    [
        ("1 gallon", 1.0, "gal"),
        ("1 gal (3.78 L)", 1.0, "gal"),
        ("half gallon", 0.5, "gal"),
        ("gallon", 1.0, "gal"),
        ("1 quart", 1.0, "qt"),
        ("quart", 1.0, "qt"),
        ("1 pint", 1.0, "pint"),
        ("16 fl oz", 16.0, "oz"),
        ("2 L", 2.0, "l"),
        ("500 g", 500.0, "g"),
        ("33 cl", 330.0, "ml"),  # metric sub-unit → ml
        ("5 dl", 500.0, "ml"),
    ],
)
def test_package_size_parses_human_units(text: str, qty: float, unit: str) -> None:
    got_qty, got_unit, desc = _parse_quantity(text)
    assert got_qty == qty
    assert got_unit == unit
    assert desc == text


def test_unknown_unit_is_left_null_not_mislabelled_as_count() -> None:
    qty, unit, desc = _parse_quantity("3 sachets")
    assert qty == 3.0
    assert unit is None  # not silently "ea"
    assert desc == "3 sachets"


def test_density_is_derived_from_per_serving_vs_per_100g_calories() -> None:
    # 2% milk: ~50 kcal/100 g, a 240 ml serving carries ~124 kcal.
    # grams/serving = 124/50*100 = 248 g; density = 248/240 = 1.033 g/ml.
    nutriments = {"energy-kcal_serving": 124, "energy-kcal_100g": 50}
    density = _derive_density_from_energy(nutriments, "1 cup (240 ml)")
    assert density == pytest.approx(1.033, abs=0.01)


def test_density_derivation_rejects_implausible_values() -> None:
    assert _derive_density_from_energy({"energy-kcal_serving": 900, "energy-kcal_100g": 50}, "10 ml") is None
    assert _derive_density_from_energy({}, "240 ml") is None


def test_liquid_density_falls_back_to_a_category_estimate() -> None:
    assert _estimate_liquid_density(["en:milks", "en:dairies"], "gal") == 1.03
    assert _estimate_liquid_density(["en:olive-oils", "en:oils"], "l") == 0.92
    assert _estimate_liquid_density(["en:sparkling-waters"], "l") == 1.00  # generic liquid default
    # A solid (weight unit) gets no liquid density.
    assert _estimate_liquid_density(["en:snacks"], "g") is None


def test_serving_grams_handles_ml_via_density() -> None:
    # No grams in the label; 240 ml at 1.03 g/ml → ~247 g.
    grams = _parse_serving_grams("1 cup (240 ml)", density=1.03)
    assert grams == pytest.approx(247.2, abs=0.5)


def test_serving_grams_prefers_explicit_grams() -> None:
    assert _parse_serving_grams("30 g (about 12 chips)") == 30.0
