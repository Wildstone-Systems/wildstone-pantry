"""Canonical ingredient key — a *proposer*, not an oracle (ADR 0015).

Normalises a product name to a matching key so that different catalog records for
the same thing ("cumin", "Lawry's Ground Cumin") can be recognised as one
ingredient. The guiding principle (ADR 0015 §0): canonicalisation only *proposes*
candidates for equality/suggestion — it must never make a confident *wrong* match.

So the rules are deliberately conservative:

- **Strip only known brands** (a curated list) plus ®/™ and possessives. An
  *unknown* brand simply stays in the key: a miss degrades to a suggestion the
  user can confirm, never to a wrong auto-match.
- **Keep every descriptive modifier** (plain, ground, whole, strawberry-banana,
  low-sodium). "Plain Greek Yogurt" and "Strawberry Banana Greek Yogurt" must
  produce *different* keys — the flavor problem (ADR 0015 §Context) means no
  algorithm can tell which modifiers are identity-critical, so we keep them all.

Tokens are lowercased, de-duplicated and sorted, so the key is order-independent
("ground cumin" == "cumin ground") and equality is a robust identity test.

This module has **zero app imports on purpose**: it is pure text processing so the
backfill migration can import and call it without pulling in the ORM/app graph.
"""

from __future__ import annotations

import re

CANONICAL_VERSION = 2

# Curated brands to strip. Lowercased; multi-word entries are removed as phrases
# before tokenising. Unknown brands are intentionally NOT here — they stay in the
# key and degrade to a confirmable suggestion rather than a wrong match.
_BRAND_PHRASES: tuple[str, ...] = (
    "great value",
    "smart way",
    "member's mark",
    "members mark",
    "simple truth",
    "private selection",
    "good & gather",
    "good and gather",
    "market pantry",
    "del monte",
    "old el paso",
    "365 everyday value",
)
_BRAND_WORDS: frozenset[str] = frozenset(
    {
        "chobani",
        "fage",
        "daisy",
        "lawrys",  # Lawry's — after possessive strip
        "lawry",
        "mccormick",
        "kroger",
        "heinz",
        "hunts",  # Hunt's
        "hunt",
        "kraft",
        "hellmanns",  # Hellmann's
        "hellmann",
        "oikos",
        "yoplait",
        "danone",
        "philadelphia",
        "tillamook",
        "landolakes",
        "breakstone",
        "breakstones",
        "sargento",
        "goya",
        "badia",
        "morton",
        "domino",
        "swanson",
        "progresso",
        "barilla",
        "ronzoni",
        "bertolli",
        "prego",
        "ragu",
        "classico",
        "cento",
    }
)

# Size / packaging tokens to drop (bare unit words and count words). Numbers and
# glued number+unit tokens ("16oz") are handled by regex separately.
_SIZE_WORDS: frozenset[str] = frozenset(
    {
        "g", "gram", "grams", "kg", "kilogram", "kilograms", "mg",
        "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds",
        "ml", "milliliter", "milliliters", "l", "liter", "liters", "litre", "litres",
        "tsp", "teaspoon", "teaspoons", "tbsp", "tablespoon", "tablespoons",
        "floz", "cup", "cups", "pint", "pints", "qt", "quart", "quarts",
        "gal", "gallon", "gallons",
        "pack", "packs", "pk", "ct", "count", "ea", "each", "piece", "pieces",
        "box", "boxes", "bag", "bags", "can", "cans", "jar", "jars", "bottle",
        "bottles", "container", "containers", "pouch", "pouches", "case",
    }
)

# Generic stopwords that carry no identity.
_STOPWORDS: frozenset[str] = frozenset({"of", "the", "with", "a", "an", "and", "for", "in"})

_TRADEMARK_RE = re.compile(r"[®™©]")
_POSSESSIVE_RE = re.compile(r"['’]s\b")
_NUM_UNIT_RE = re.compile(r"^\d+(?:[.,]\d+)?(?:[a-z]+)?$")
_NONWORD_RE = re.compile(r"[^a-z0-9%\s-]")


def _is_size_or_number(token: str) -> bool:
    if token in _SIZE_WORDS:
        return True
    # Pure number or a number glued to a unit ("16oz", "12pk").
    if _NUM_UNIT_RE.match(token):
        return True
    return False


# Words that merely END in "s" — stripping it would maim them ("hummus" ->
# "hummu"). Anything ending -ss/-us/-is is covered by rule; these are the
# irregulars that aren't.
_NEVER_SINGULARISE: frozenset[str] = frozenset(
    {
        "molasses", "asparagus", "couscous", "hummus", "greens", "oats", "grits",
        "cheese",  # already singular; guard against future -es rules
    }
)


def _singularise(token: str) -> str:
    """Fold a plural token to its singular so a recipe's "2 avocados" matches the
    "avocado" on your shelf.

    Plural and singular produced DIFFERENT canonical keys, so "eggs" never
    matched "egg" and every plural ingredient minted a duplicate item that
    could never resolve against inventory.

    Deliberately conservative — a wrong fold silently merges two real
    ingredients, which is worse than a missed match:
      * leave anything under 4 chars alone (gas, oats)
      * leave -ss / -us / -is alone (grass, hummus, basis)
      * -ies -> -y  (berries -> berry)
      * -oes/-shes/-ches/-xes/-zes/-ses -> drop "es"  (tomatoes -> tomato)
      * otherwise drop a trailing "s"
    """
    if len(token) < 4 or token in _NEVER_SINGULARISE:
        return token
    if token.endswith(("ss", "us", "is")):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("oes", "shes", "ches", "xes", "zes", "ses")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token


def canonical_ingredient(name: str | None, brand: str | None = None) -> str | None:
    """Propose a canonical matching key for a product name.

    Returns a normalised, order-independent key (lowercased, brand/size/stopword
    stripped, descriptive modifiers kept, tokens sorted & de-duplicated), or
    ``None`` when nothing identity-bearing remains (e.g. a name that was all brand
    and size). A ``None`` key never matches — the item falls back to exact-id
    behaviour, which is the safe degradation.
    """
    if not name:
        return None

    text = name.lower()
    # Parentheses may contain flavor/composition, not just packaging. Their
    # contents pass through the same conservative token rules as the name.
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1%", text)
    percentages: dict[str, str] = {}
    def preserve_percent(match: re.Match[str]) -> str:
        token = "composition" + chr(97 + len(percentages))
        percentages[token] = match.group(0)
        return token
    text = re.sub(r"\d+(?:\.\d+)?%", preserve_percent, text)
    text = _TRADEMARK_RE.sub(" ", text)
    text = _POSSESSIVE_RE.sub("", text)  # lawry's -> lawrys, hunt's -> hunts

    # Remove multi-word brand phrases before tokenising.
    for phrase in _BRAND_PHRASES:
        text = text.replace(phrase, " ")

    # Also strip the item's own brand field (a reliable brand signal) as a phrase.
    if brand:
        btext = _POSSESSIVE_RE.sub("", _TRADEMARK_RE.sub(" ", brand.lower()))
        btext = btext.strip()
        if btext:
            text = text.replace(btext, " ")

    text = _NONWORD_RE.sub(" ", text)  # punctuation -> space (keep %, -)
    text = text.replace("-", " ")

    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip("-")
        if not token:
            continue
        if token in _BRAND_WORDS:
            continue
        if token in _STOPWORDS:
            continue
        if _is_size_or_number(token):
            continue
        tokens.append(percentages.get(token, _singularise(token)))

    if not tokens:
        return None

    # Order-independent, de-duplicated: "ground cumin" == "cumin ground".
    return " ".join(sorted(set(tokens)))


# Tokens that flip meaning — a candidate that adds one of these is NOT the same
# ingredient (salt vs salt-free), so it's rejected even when it otherwise fits the
# subset shape.
_NEGATION_TOKENS: frozenset[str] = frozenset({"free", "no", "non", "without", "zero", "less"})

# How many descriptive tokens a stocked product may add over the recipe's key
# and still be OFFERED as a match (never auto-linked). See subset_suggestion.
_MAX_EXTRA_TOKENS = 2


def _key_tokens(key: str | None) -> frozenset[str]:
    return frozenset(key.split()) if key else frozenset()


def subset_suggestion(recipe_key: str | None, product_key: str | None) -> bool:
    """Is ``product_key`` a *tight-subset* candidate for a recipe's ``recipe_key``
    (ADR 0015 §2) — i.e. worth SUGGESTING (never auto-matching)?

    True when the recipe's tokens are a subset of the product's and the product
    adds at most ``_MAX_EXTRA_TOKENS`` descriptive tokens: ``cheddar cheese`` ⊆
    ``cheddar cheese shredded`` (suggest), and ``coconut oil`` ⊆ ``organic
    virgin coconut oil`` (suggest) — but never ``butter`` vs ``onion butter
    blend``, because a single-token recipe key inside a longer product name is
    the top false positive. Equality is handled elsewhere; a token that negates
    the ingredient (salt vs salt-free) is rejected outright.

    The limit was 1. Measured against a real 162-ingredient / 42-stocked-item
    corpus, raising it to 2 gained exactly one correct suggestion (coconut oil)
    and zero false positives; 3+ gained nothing further. Kept at 2 rather than
    higher because each extra token is slack that only shows its cost on data
    we haven't seen yet.
    """
    r = _key_tokens(recipe_key)
    p = _key_tokens(product_key)
    if len(r) < 2:  # single common word inside a longer name = top false positive
        return False
    if r == p:  # equality is an auto-match, not a suggestion
        return False
    if not r <= p:  # recipe must be fully contained in the product
        return False
    if len(p) > len(r) + _MAX_EXTRA_TOKENS:
        return False
    if (p - r) & _NEGATION_TOKENS:  # the extra token must not negate the ingredient
        return False
    return True
