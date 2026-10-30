"""Planning-owned persistence; transaction commits stay in the service."""

from uuid import UUID

from sqlalchemy import select

from app.models.recipe_ingredient import RecipeIngredient
from modules.planning import repository as planning_repository
from modules.planning.models import CookSession
from modules.planning.models.substitution_review import SubstitutionReview


def session(db, session_id, *, lock=False):
    query = select(CookSession).where(CookSession.id == session_id)
    return db.execute(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    ).scalar_one_or_none()


def review(db, review_id, *, lock=False):
    query = select(SubstitutionReview).where(SubstitutionReview.id == review_id)
    return db.execute(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    ).scalar_one_or_none()


def lock_recipe(db, recipe_id):
    if planning_repository.lock_recipe_for_update(db, recipe_id) is None:
        raise LookupError("Recipe not found")
    db.execute(
        select(RecipeIngredient)
        .where(RecipeIngredient.recipe_id == recipe_id)
        .order_by(RecipeIngredient.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()


def list_for(db, session_id, recipe_id):
    rows = (
        db.execute(
            select(SubstitutionReview)
            .where(
                (SubstitutionReview.session_id == session_id)
                | (
                    (SubstitutionReview.recipe_id == recipe_id)
                    & (SubstitutionReview.scope == "recipe")
                    & (SubstitutionReview.status == "approved")
                )
            )
            .order_by(SubstitutionReview.created_at.desc())
            .limit(100)
        )
        .scalars()
        .all()
    )
    current = session(db, session_id)
    attached = (current.plan or {}).get("substitution_approval_ids", []) if current else []
    known = {str(row.id) for row in rows}
    for review_id in attached:
        if str(review_id) not in known:
            row = review(db, UUID(str(review_id)))
            if row is not None:
                rows.append(row)
    return rows


def add(db, row):
    db.add(row)
    db.flush()


def consume(db, ids, now):
    for review_id in ids:
        row = review(db, review_id, lock=True)
        if row and row.scope == "one_use" and row.status == "approved":
            row.status = "consumed"
            row.consumed_at = now
            db.add(row)


def finishing_uses(db, review_id):
    rows = db.execute(select(CookSession).where(CookSession.status == "finishing")).scalars().all()
    return any(str(review_id) in (row.plan or {}).get("substitution_approval_ids", []) for row in rows)
