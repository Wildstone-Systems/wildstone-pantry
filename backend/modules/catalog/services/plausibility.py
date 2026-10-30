"""Deterministic sanity checks on nutrition values.

No model, no lookup, no network — just arithmetic and physics. That is the
point: these run on every write, cost nothing, and cannot themselves be wrong
in the way a probabilistic source can. They catch a different class of error
from the match gate. The gate asks "is this the right food?"; this asks "could
these numbers be true of any food at all?"

Two severities, and the distinction is load-bearing:

``IMPOSSIBLE``
    Violates arithmetic or physics — 400 g of carbohydrate in 100 g of garlic,
    a negative gram figure, more energy than pure fat could provide. There is
    no food this describes, so it is refused on arrival rather than stored and
    flagged. A stored impossibility propagates: it is summed into daily
    calories, scaled by recipes, and charted, and by then nobody can tell which
    row poisoned the total.

``SUSPECT``
    Internally inconsistent but not impossible — the stated energy disagrees
    with what the macros imply, or there is energy with no macros to explain
    it. These are *reported*, never refused. Fibre, sugar alcohols and rounding
    all shift the Atwater sum legitimately, and refusing on that basis would
    throw away correct label data.

Thresholds are measured, not chosen. Across 241 items carrying real label data,
the Atwater cross-check has a median relative error of 4.2% and a 90th
percentile of 28.7% — so ordinary, correct labels routinely disagree by a
quarter, and the flag has to sit well above that to mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

IMPOSSIBLE = "impossible"
SUSPECT = "suspect"

# Nothing edible exceeds these per 100 g. Pure fat is 900 kcal/100 g, which is
# the physical ceiling for energy; anything above it is a unit error.
MAX_KCAL_PER_100G = Decimal("900")
MAX_GRAMS_PER_100G = Decimal("100")

# Protein + fat + carbohydrate cannot exceed the 100 g they are measured in.
# A little slack for rounding on labels that state each to the nearest gram.
MAX_MACRO_SUM = Decimal("105")

# Atwater factors: kcal ≈ 4·protein + 4·carbs + 9·fat.
KCAL_PER_G_PROTEIN = Decimal("4")
KCAL_PER_G_CARB = Decimal("4")
KCAL_PER_G_FAT = Decimal("9")

# Measured p90 of correct label data is 0.287, so 0.5 flags the tail without
# drowning the signal in ordinary fibre/rounding disagreement.
ATWATER_TOLERANCE = Decimal("0.5")

# Below this, the relative check is meaningless — a 1 kcal sparkling water is
# "100% off" in relative terms and perfectly correct.
ATWATER_MIN_KCAL = Decimal("40")

# Energy this high with every macro at zero means the macros were never filled,
# not that the food is energy-free.
UNEXPLAINED_ENERGY_KCAL = Decimal("50")


@dataclass(frozen=True)
class Issue:
    severity: str
    field: str
    message: str

    @property
    def blocking(self) -> bool:
        return self.severity == IMPOSSIBLE


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def check_nutrition(values: dict) -> list[Issue]:
    """Every problem visible in these numbers alone, worst class first.

    ``values`` uses the item column names (``energy_kcal_100g``,
    ``protein_g_100g``, ...); missing keys are simply not checked, since absent
    is a legitimate state and says nothing either way.
    """
    issues: list[Issue] = []

    energy = _dec(values.get("energy_kcal_100g"))
    protein = _dec(values.get("protein_g_100g"))
    fat = _dec(values.get("fat_g_100g"))
    carbs = _dec(values.get("carbs_g_100g"))

    gram_fields = {
        "protein_g_100g": protein,
        "fat_g_100g": fat,
        "carbs_g_100g": carbs,
        "fiber_g_100g": _dec(values.get("fiber_g_100g")),
        "sugars_g_100g": _dec(values.get("sugars_g_100g")),
        "saturated_fat_g_100g": _dec(values.get("saturated_fat_g_100g")),
        "sodium_g_100g": _dec(values.get("sodium_g_100g")),
    }

    for field, amount in gram_fields.items():
        if amount is None:
            continue
        if amount < 0:
            issues.append(Issue(IMPOSSIBLE, field, f"{amount} g is negative."))
        elif amount > MAX_GRAMS_PER_100G:
            issues.append(
                Issue(IMPOSSIBLE, field, f"{amount} g per 100 g — more than the 100 g it is measured in.")
            )

    if energy is not None:
        if energy < 0:
            issues.append(Issue(IMPOSSIBLE, "energy_kcal_100g", f"{energy} kcal is negative."))
        elif energy > MAX_KCAL_PER_100G:
            issues.append(
                Issue(
                    IMPOSSIBLE,
                    "energy_kcal_100g",
                    f"{energy} kcal per 100 g exceeds pure fat ({MAX_KCAL_PER_100G}).",
                )
            )

    macro_sum = sum((m for m in (protein, fat, carbs) if m is not None), Decimal("0"))
    if protein is not None and fat is not None and carbs is not None and macro_sum > MAX_MACRO_SUM:
        issues.append(
            Issue(
                IMPOSSIBLE,
                "macros",
                f"protein + fat + carbs = {macro_sum} g in 100 g.",
            )
        )

    # --- consistency, reported but never refused ---------------------------

    if energy is not None and energy >= UNEXPLAINED_ENERGY_KCAL:
        if all(m is not None and m == 0 for m in (protein, fat, carbs)):
            issues.append(
                Issue(
                    SUSPECT,
                    "energy_kcal_100g",
                    f"{energy} kcal with no protein, fat or carbohydrate to account for it.",
                )
            )

    if (
        energy is not None
        and energy >= ATWATER_MIN_KCAL
        and protein is not None
        and fat is not None
        and carbs is not None
        and not any(i.blocking for i in issues)
    ):
        implied = protein * KCAL_PER_G_PROTEIN + carbs * KCAL_PER_G_CARB + fat * KCAL_PER_G_FAT
        relative = abs(implied - energy) / energy
        if relative > ATWATER_TOLERANCE:
            issues.append(
                Issue(
                    SUSPECT,
                    "energy_kcal_100g",
                    f"stated {energy} kcal but the macros imply about "
                    f"{implied.quantize(Decimal('1'))} kcal ({relative:.0%} apart).",
                )
            )

    issues.sort(key=lambda i: 0 if i.blocking else 1)
    return issues


def blocking_issues(values: dict) -> list[Issue]:
    """Only the problems that must stop a write."""
    return [issue for issue in check_nutrition(values) if issue.blocking]


def audit_items(db, *, include_ok: bool = False) -> list[dict]:
    """Every stored item whose own numbers disagree with themselves.

    The counterpart to the conversion review queue: that one lists what is
    *missing*, this lists what is *present and doubtful*. Both are needed — the
    twenty bad nutrition estimates were all present, all populated, and all
    invisible, because nothing ever looked at a value once it had been written.

    Pure SQL-and-arithmetic; no provider is contacted, so this is cheap enough
    to run on page load.
    """
    from sqlalchemy import select

    from app.models import Item

    columns = (
        "energy_kcal_100g", "protein_g_100g", "fat_g_100g", "carbs_g_100g",
        "fiber_g_100g", "sugars_g_100g", "saturated_fat_g_100g", "sodium_g_100g",
    )

    rows: list[dict] = []
    for item in db.execute(select(Item).where(Item.is_active.is_(True)).order_by(Item.name)).scalars():
        values = {column: getattr(item, column, None) for column in columns}
        if all(value is None for value in values.values()):
            continue
        issues = check_nutrition(values)
        if not issues and not include_ok:
            continue
        rows.append(
            {
                "item_id": item.id,
                "name": item.name,
                "brand": item.brand,
                # Provenance travels WITH the finding. "This value is doubtful"
                # is only actionable next to where the value came from.
                "estimated": bool(item.nutrition_estimated),
                "source": item.nutrition_source,
                "energy_kcal_100g": float(item.energy_kcal_100g) if item.energy_kcal_100g is not None else None,
                "protein_g_100g": float(item.protein_g_100g) if item.protein_g_100g is not None else None,
                "fat_g_100g": float(item.fat_g_100g) if item.fat_g_100g is not None else None,
                "carbs_g_100g": float(item.carbs_g_100g) if item.carbs_g_100g is not None else None,
                "issues": [
                    {"severity": i.severity, "field": i.field, "message": i.message} for i in issues
                ],
            }
        )
    # Worst first: an impossible value is actionable now, an inconsistent one
    # is a question for later.
    rows.sort(key=lambda row: (0 if any(i["severity"] == IMPOSSIBLE for i in row["issues"]) else 1, row["name"].lower()))
    return rows


# How many items must share one macro fingerprint before it reads as a
# placeholder rather than a coincidence. Two foods genuinely having identical
# protein, fat, carbs AND energy to the decimal is already unlikely; four is
# not a coincidence, it is a default someone typed once.
DUPLICATE_FINGERPRINT_THRESHOLD = 4


def audit_duplicate_macros(db) -> list[dict]:
    """Groups of items sharing byte-identical nutrition — i.e. placeholders.

    A class of error no within-item check can see. Twenty-three items in this
    catalogue carried exactly 50 kcal / 2 g protein / 1 g fat / 8 g carbs —
    butter, heavy cream, cashews, limes and carrots alike — written by a bulk
    import that filled the fields because they were required rather than
    because it knew the answer. Every one of them passes the arithmetic checks:
    4x2 + 4x8 + 9x1 = 49 against a stated 50 is internally consistent to within
    2%.

    What gives it away is not any single row, it is the repetition. Real foods
    do not agree to the decimal place.
    """
    from collections import defaultdict

    from sqlalchemy import select

    from app.models import Item

    fingerprints: dict[tuple, list[Item]] = defaultdict(list)
    for item in db.execute(select(Item).where(Item.is_active.is_(True))).scalars():
        key = (
            item.energy_kcal_100g,
            item.protein_g_100g,
            item.fat_g_100g,
            item.carbs_g_100g,
        )
        # An all-null item simply has no nutrition; that is not a placeholder.
        if all(value is None for value in key):
            continue
        # Nor is all-zero. Diet drinks, black coffee and most seasonings are
        # genuinely 0/0/0/0, and fourteen of them agreeing is a fact about
        # zero-calorie food, not evidence of a default. Flagging it would put a
        # standing false alarm at the top of the queue, which is how review
        # pages stop being read.
        if all(value is not None and value == 0 for value in key):
            continue
        fingerprints[key].append(item)

    groups = []
    for key, members in fingerprints.items():
        if len(members) < DUPLICATE_FINGERPRINT_THRESHOLD:
            continue
        energy, protein, fat, carbs = key
        groups.append(
            {
                "energy_kcal_100g": float(energy) if energy is not None else None,
                "protein_g_100g": float(protein) if protein is not None else None,
                "fat_g_100g": float(fat) if fat is not None else None,
                "carbs_g_100g": float(carbs) if carbs is not None else None,
                "count": len(members),
                "item_ids": [item.id for item in members],
                "names": sorted(item.name for item in members),
            }
        )
    groups.sort(key=lambda group: -group["count"])
    return groups
