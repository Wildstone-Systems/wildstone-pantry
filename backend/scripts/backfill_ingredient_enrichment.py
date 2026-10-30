"""Backfill nutrition and conversions for ingredients that arrived bare.

Recipe import used to create catalogue items with nothing but a name. This
walks the ones a recipe actually references and fills what can be looked up,
using the same paths that now run at creation time.

Paced deliberately. USDA rate-limits and has been measured returning 400s under
load; a tight loop over a few hundred items produces mostly failures and teaches
you nothing. One item at a time with a gap is slower and actually works.

    python scripts/backfill_ingredient_enrichment.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.db import SessionLocal  # noqa: E402
from app.models import Item, RecipeIngredient  # noqa: E402

GAP_SECONDS = 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 = every candidate")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        referenced = {row[0] for row in db.execute(select(RecipeIngredient.item_id)).all()}
        candidates = [
            item
            for item in db.execute(
                select(Item).where(Item.is_active.is_(True)).order_by(Item.name)
            ).scalars()
            if item.id in referenced
            and (item.energy_kcal_100g is None or item.density_g_per_ml is None)
        ]
        if args.limit:
            candidates = candidates[: args.limit]

        print(f"{len(candidates)} recipe ingredients missing nutrition and/or a conversion")
        if args.dry_run:
            for item in candidates:
                gaps = []
                if item.energy_kcal_100g is None:
                    gaps.append("nutrition")
                if item.density_g_per_ml is None:
                    gaps.append("density")
                print(f"  {item.name[:40]:40} needs {', '.join(gaps)}")
            return 0

        from modules.catalog.services.enrichment import enrich_item

        filled_nutrition = filled_density = 0
        for index, item in enumerate(candidates, start=1):
            result = enrich_item(db, item)
            if "nutrition" in result:
                filled_nutrition += 1
            if "density" in result:
                filled_density += 1
            if result:
                print(f"  [{index}/{len(candidates)}] {item.name[:34]:34} {result}")
            time.sleep(GAP_SECONDS)

        print(f"\nfilled nutrition for {filled_nutrition}, density for {filled_density}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
