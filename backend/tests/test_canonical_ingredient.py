"""Canonical ingredient proposer (ADR 0015 §1). The load-bearing property is that
it never collapses two *different* things into one key (the flavor problem), while
it does unify the same thing across brands and size noise."""

from __future__ import annotations

from modules.catalog.services.canonical import canonical_ingredient as C
from modules.catalog.services.canonical import subset_suggestion as S


def test_strips_known_brand_and_unifies_across_brands() -> None:
    # Different brands, same ingredient → same key.
    assert C("Lawry's Ground Cumin") == C("McCormick Ground Cumin")
    assert C("Lawry's Ground Cumin") == "cumin ground"


def test_order_independent() -> None:
    assert C("Ground Cumin") == C("Cumin, Ground")


def test_exact_names_match() -> None:
    assert C("Garlic Powder") == C("garlic powder")
    assert C("Milk") == C("milk")


def test_size_and_count_noise_dropped() -> None:
    assert C("Great Value 2% Milk, 1 Gallon") == "2% milk"
    # Singularised (see _singularise): the key must be the same whether a
    # recipe says "tomatoes" or an item is called "tomato", or the two never match.
    assert C("16 oz Crushed Tomatoes") == "crushed tomato"
    assert C("Eggs, 12 ct") == "egg"  # singularised, so a recipe's "egg" matches


def test_flavor_modifiers_are_kept_and_never_collapsed() -> None:
    # The whole point: plain vs strawberry-banana must be DIFFERENT keys.
    plain = C("Chobani Plain Greek Yogurt")
    straw = C("Chobani Strawberry Banana Greek Yogurt")
    assert plain != straw
    assert plain == C("Fage Plain Greek Yogurt")  # brand stripped, flavor kept


def test_negation_is_not_equal_to_the_bare_word() -> None:
    # salt != salt-free (equality must not match; subset/negation is Phase 2).
    assert C("Salt") != C("Salt-Free Garlic & Herb")
    assert C("Salt") == "salt"


def test_brand_field_stripped_when_present_in_name() -> None:
    assert C("Kroger Whole Milk", brand="Kroger") == C("Whole Milk")


def test_all_noise_returns_none() -> None:
    # A name that is entirely brand + size leaves nothing identity-bearing.
    assert C("Great Value 16 oz") is None
    assert C("") is None
    assert C(None) is None


def test_parenthetical_modifiers_kept_and_trademarks_dropped() -> None:
    assert C("Ground Cumin®") == "cumin ground"
    assert C("Cumin (ground)") == "cumin ground"


def test_subset_suggestion_offers_up_to_two_extra_tokens() -> None:
    # cheddar cheese ⊆ cheddar cheese shredded (one extra) → suggest.
    assert S(C("Cheddar Cheese"), C("Shredded Cheddar Cheese")) is True
    # Two extra: the real case this limit was raised for — a recipe's "coconut
    # oil" against the "Organic Virgin Coconut Oil" actually on the shelf.
    assert S(C("coconut oil"), C("Organic Virgin Coconut Oil")) is True
    assert S("cumin", "cumin ground") is False  # single-token recipe → never suggest
    assert S("cumin ground", "cumin") is False  # product must be the more specific one


def test_subset_suggestion_rejects_three_extra_tokens_and_negations() -> None:
    # Three extra descriptive tokens is too loose to offer.
    assert S("greek yogurt", "banana greek strawberry sweetened yogurt") is False
    # butter (single token) vs onion butter blend → not offered. This exclusion
    # is what keeps "pepper" from matching "Mild Banana Pepper Rings".
    assert S("butter", "blend butter onion") is False
    assert S("pepper", "banana mild pepper rings") is False
    # negation guard: chicken broth vs "free" variant rejected even at 1 extra.
    assert S("chicken broth", "broth chicken free") is False


def test_subset_suggestion_not_for_equality() -> None:
    assert S("garlic powder", "garlic powder") is False
