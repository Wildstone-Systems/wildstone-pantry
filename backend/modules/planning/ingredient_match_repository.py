"""Planning-owned review/scope persistence, bounded pending data and atomic CAS."""

from datetime import UTC, datetime

from sqlalchemy import case, delete, select, text, update

from app.models import Recipe, RecipeIngredient
from modules.planning.models.ingredient_match_review import IngredientMatchReview, IngredientMatchScope


def context(db, recipe_id, ingredient_id, *, lock=False):
    statement = (
        select(RecipeIngredient, Recipe)
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .where(Recipe.id == recipe_id, RecipeIngredient.id == ingredient_id)
    )
    if lock:
        statement = statement.with_for_update()
    result = db.execute(statement.execution_options(populate_existing=True)).one_or_none()
    if result is None:
        raise LookupError("Recipe ingredient unavailable")
    return result


def lock_scope(db, ingredient_id):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE ingredient_match_scopes IN EXCLUSIVE MODE"))
    else:
        db.execute(
            text("UPDATE ingredient_match_scopes SET revision = revision WHERE ingredient_id = :key"),
            {"key": ingredient_id.hex},
        )
    db.expire_all()


def scope(db, ingredient_id):
    return db.get(IngredientMatchScope, ingredient_id)


def ensure_scope(db, ingredient_id):
    lock_scope(db, ingredient_id)
    row = scope(db, ingredient_id)
    if row is None:
        row = IngredientMatchScope(ingredient_id=ingredient_id, revision=0)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get(db, review_id):
    return db.get(IngredientMatchReview, review_id)


def create(db, **kwargs):
    lock_scope(db, kwargs["ingredient_id"])
    db.execute(
        delete(IngredientMatchReview).where(
            IngredientMatchReview.status.in_(["pending", "evaluating", "rejected", "interrupted"]),
            IngredientMatchReview.expires_at < datetime.now(UTC),
        )
    )
    count = len(
        db.scalars(
            select(IngredientMatchReview.id).where(
                IngredientMatchReview.status.in_(["pending", "evaluating", "interrupted"])
            )
        ).all()
    )
    if count >= 100:
        db.rollback()
        raise ValueError("Pending review limit reached; wait for pending reviews to expire")
    head = scope(db, kwargs["ingredient_id"])
    if head is None or head.revision != kwargs["document"]["scope_revision"]:
        db.rollback()
        raise ValueError("Current chosen item changed; refresh")
    row = IngredientMatchReview(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def change(db, row, document, *, status, expected_revision):
    changed = db.execute(
        update(IngredientMatchReview)
        .where(
            IngredientMatchReview.id == row.id,
            IngredientMatchReview.revision == expected_revision,
        )
        .values(document=document, status=status, revision=expected_revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("Review changed; refresh")
    db.commit()
    db.expire_all()


def confirm(db, row, document, *, expected_revision):
    # Caller holds scope lock and freshly checked evidence under item/recipe locks.
    changed = db.execute(
        update(IngredientMatchScope)
        .where(
            IngredientMatchScope.ingredient_id == row.ingredient_id,
            IngredientMatchScope.revision == document["scope_revision"],
        )
        .values(current_review_id=row.id, revision=document["scope_revision"] + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("Another human choice superseded this pending review")
    changed = db.execute(
        update(IngredientMatchReview)
        .where(
            IngredientMatchReview.id == row.id,
            IngredientMatchReview.revision == expected_revision,
            IngredientMatchReview.status.in_(["pending", "interrupted"]),
        )
        .values(document=document, status="confirmed", revision=expected_revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("Review changed; refresh")
    db.commit()
    db.expire_all()


def history(db, recipe_id, ingredient_id, actor_id):
    # Keep the current confirmed association first regardless of its age. The
    # remaining bounded history is newest first, with an ID tie-breaker.
    current_first = case(
        (
            (IngredientMatchReview.id == IngredientMatchScope.current_review_id)
            & (IngredientMatchReview.status == "confirmed"),
            0,
        ),
        else_=1,
    )
    return db.scalars(
        select(IngredientMatchReview)
        .outerjoin(IngredientMatchScope, IngredientMatchScope.ingredient_id == IngredientMatchReview.ingredient_id)
        .where(
            IngredientMatchReview.recipe_id == recipe_id,
            IngredientMatchReview.ingredient_id == ingredient_id,
            (IngredientMatchReview.status == "confirmed") | (IngredientMatchReview.actor_id == actor_id),
        )
        .order_by(current_first, IngredientMatchReview.created_at.desc(), IngredientMatchReview.id.desc())
        .limit(20)
    ).all()
