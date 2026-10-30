"""Bounded ranked review receipts, atomic CAS and recipe source reads."""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update

from app.models import Recipe, RecipeIngredient
from modules.planning.models.ranked_ingredient_review import RankedIngredientReview


def context(db, recipe_id, ingredient_id, *, lock=False):
    statement = (
        select(RecipeIngredient, Recipe)
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .where(Recipe.id == recipe_id, RecipeIngredient.id == ingredient_id)
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement.execution_options(populate_existing=True)).one_or_none()
    if row is None:
        raise LookupError("Recipe ingredient unavailable")
    ingredient, recipe = row
    source = {
        "recipe": {column.name: str(getattr(recipe, column.name)) for column in Recipe.__table__.columns},
        "line": {column.name: str(getattr(ingredient, column.name)) for column in RecipeIngredient.__table__.columns},
    }
    return ingredient.item_id, source


def get(db, review_id, *, lock=False):
    if lock and db.get_bind().dialect.name == "sqlite":
        # Setting revision to itself does not invoke an ORM onupdate timestamp.
        db.execute(
            text("UPDATE ranked_ingredient_reviews SET revision = revision WHERE id = :key"), {"key": review_id.hex}
        )
    statement = select(RankedIngredientReview).where(RankedIngredientReview.id == review_id)
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement.execution_options(populate_existing=True)).scalar_one_or_none()


def create(db, **values):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE ranked_ingredient_reviews IN SHARE ROW EXCLUSIVE MODE"))
    else:
        db.execute(text("UPDATE ranked_ingredient_reviews SET revision = revision WHERE 1 = 0"))
    db.execute(
        delete(RankedIngredientReview).where(
            RankedIngredientReview.expires_at < datetime.now(UTC), RankedIngredientReview.status != "confirmed"
        )
    )
    count = db.scalar(
        select(func.count())
        .select_from(RankedIngredientReview)
        .where(RankedIngredientReview.status.in_(["evaluating", "pending", "interrupted"]))
    )
    if count >= 100:
        raise ValueError("Pending review capacity reached")
    row = RankedIngredientReview(**values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def change(db, row, document, status, revision, *, commit=True):
    changed = db.execute(
        update(RankedIngredientReview)
        .where(
            RankedIngredientReview.id == row.id,
            RankedIngredientReview.revision == revision,
            RankedIngredientReview.status.in_(["evaluating", "pending", "interrupted"]),
        )
        .values(document=document, status=status, revision=revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise ValueError("Review changed")
    if commit:
        db.commit()
        db.expire_all()
