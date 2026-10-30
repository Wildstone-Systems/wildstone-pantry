"""Reading a conversion straight off the product's own label.

Nutrition labels routinely state a serving as a volume AND its weight —
"1 tsp (5 g)". That pair IS a density, product-specific, with no food to match
and nothing for a model to verify. The data was already being fetched from Open
Food Facts and the volume half discarded, so this costs no extra request.

Counted servings ("4 COOKIES (33 g)") give the neighbouring figure,
grams-per-each, which fixes a different unconvertible class: goods stocked as
`each` that a recipe asks for by weight.
"""

from __future__ import annotations

import pytest

from modules.catalog.services.barcode_providers import (
    _derive_density_from_energy,
    _serving_ml,
    grams_per_each_from_serving,
)


@pytest.mark.parametrize(
    "label,expected_ml",
    [
        ("1 tsp (5 g)", 4.92892),
        ("1 tbsp (14 g)", 14.7868),
        ("2 tbsp (30 g)", 29.5736),
        ("1/4 tsp (0.6 g)", 1.23223),
        ("0.25 tsp (1.5 g)", 1.23223),
        ("¼ tsp (0.6 g)", 1.23223),   # unicode fraction — common on spice jars
        ("½ cup (120 g)", 118.294),
        ("1 1/2 tsp (7 g)", 7.39338),      # mixed number
    ],
)
def test_reads_a_spoon_or_cup_serving(label, expected_ml):
    assert _serving_ml(label) == pytest.approx(expected_ml, rel=1e-4)


def test_an_explicit_millilitre_figure_wins():
    """'1 tbsp. (15 ml)' states the volume outright — no conversion needed, and
    the stated figure is what the manufacturer means."""
    assert _serving_ml("1 tbsp. (15 ml)") == 15.0
    assert _serving_ml("1 cup (240 ml)") == 240.0


def test_a_volume_mentioned_later_is_not_the_serving():
    """Regression: '0.5 cube, MAKES 1 CUP PREPARED' read as a 237 ml serving of
    bouillon powder. The serving is always stated first, so the match anchors."""
    assert _serving_ml("0.5 cube, MAKES 1 CUP PREPARED") is None


def test_a_weight_only_serving_yields_no_volume():
    for label in ("6 g", "33g", "1 serving (5 g)", ""):
        assert _serving_ml(label) is None


def test_the_density_falls_out_of_the_label(monkeypatch):
    """The whole point: grams-per-serving over ml-per-serving, both from the
    product's own label."""
    # 5 kcal/serving at 100 kcal/100g => 5 g per serving; 1 tsp = 4.92892 ml.
    nutriments = {"energy-kcal_serving": 5.0, "energy-kcal_100g": 100.0}
    density = _derive_density_from_energy(nutriments, "1 tsp (5 g)")
    assert density == pytest.approx(1.0145, rel=1e-3)


def test_an_implausible_density_is_refused_not_stored():
    nutriments = {"energy-kcal_serving": 500.0, "energy-kcal_100g": 100.0}
    assert _derive_density_from_energy(nutriments, "1 tsp (500 g)") is None


@pytest.mark.parametrize(
    "label,grams,expected",
    [
        ("4 COOKIES (33 g)", 33.0, 8.25),
        ("0.5 cube, MAKES 1 CUP PREPARED", 6.0, 12.0),
        ("2 bars (60 g)", 60.0, 30.0),
        ("1 slice (28 g)", 28.0, 28.0),
    ],
)
def test_a_counted_serving_gives_the_weight_of_one(label, grams, expected):
    assert grams_per_each_from_serving(label, grams) == pytest.approx(expected)


def test_a_measured_serving_is_not_a_count():
    """'1 tsp (5 g)' must not be read as one countable unit weighing 5 g."""
    assert grams_per_each_from_serving("1 tsp (5 g)", 5.0) is None
    assert grams_per_each_from_serving("30 g", 30.0) is None


def test_an_absurd_per_each_weight_is_refused():
    assert grams_per_each_from_serving("1 piece (5000 g)", 5000.0) is None
    assert grams_per_each_from_serving("1 piece (0.001 g)", 0.001) is None
