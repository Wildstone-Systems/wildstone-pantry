"""Real PostgreSQL C2 locks/atomicity smoke. Synthetic data and model fixtures only.

Run only against the disposable migrations-job database, after Alembic head:
    python scripts/check_ranked_identity_postgres.py --confirm-ci-database
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-ci-database", action="store_true")
    args = parser.parse_args()
    from sqlalchemy.engine import make_url

    url = make_url(os.environ.get("DATABASE_URL", "sqlite://"))
    if not (
        args.confirm_ci_database
        and os.environ.get("CI") == "true"
        and url.drivername == "postgresql+psycopg"
        and url.host in {"localhost", "127.0.0.1"}
        and url.username == "pantryos_ci"
        and url.database == "pantryos"
    ):
        parser.error("Requires explicit acknowledgement and the disposable local CI PostgreSQL database")

    from cryptography.fernet import Fernet

    os.environ["CONFIG_SECRET_KEY"] = Fernet.generate_key().decode()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from sqlalchemy import event, select, text

    from app.db.session import SessionLocal, engine
    from app.main import app
    from app.models import Item, RecipeIngredient
    from modules.automation import culinary_service
    from modules.planning.models.ranked_ingredient_review import RankedIngredientReview

    @event.listens_for(engine, "connect")
    def timeouts(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '5000ms'")
            cursor.execute("SET lock_timeout = '1000ms'")

    engine.dispose()
    calls = []

    async def model_fixture(db, request, **kwargs):
        assert not await kwargs["stop_requested"]()
        calls.append(1)
        return {
            key: {
                "type": "choice",
                "choice": "c_1",
                "confidence": 0.95,
                "probabilities": {label: 0.94 if label == "c_1" else 0.02 for label in question.criteria},
            }
            for key, question in request.questions.items()
        }

    culinary_service.evaluate_culinary = model_fixture
    client = TestClient(app)
    suffix = uuid4().hex[:10]
    email = f"ranked-ci-{suffix}@example.com"
    password = "SyntheticRankedCi987!"

    def request(method, path, expected=200, **kwargs):
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == expected, f"{method} {path}: {response.status_code}"
        return response.json()

    bootstrap = client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    assert bootstrap.status_code in (200, 201), "The disposable migrations database must have no existing owner"
    token = request("post", "/api/auth/login", json={"email": email, "password": password})["token"]
    headers = {"Authorization": "Bearer " + token}
    request(
        "put",
        "/api/ai/classifier/config",
        headers=headers,
        json={
            "enabled": True,
            "runtime_review_enabled": True,
            "execution_location": "local",
            "endpoint": "http://localhost:11434",
            "model": "clef",
        },
    )
    location = request(
        "post", "/api/locations", expected=201, headers=headers, json={"name": "Synthetic PG review shelf"}
    )["id"]

    def setup_case(number):
        word = f"pgcase{suffix}{number}"
        wanted = request("post", "/api/items", expected=201, headers=headers, json={"name": f"Chickpeas {word}"})["id"]
        product = request("post", "/api/items", expected=201, headers=headers, json={"name": f"Garbanzo beans {word}"})[
            "id"
        ]
        response = client.post(
            "/api/inventory/add",
            headers=headers,
            json={"item_id": product, "location_id": location, "quantity": 2, "unit": "each"},
        )
        assert response.status_code in (200, 201)
        recipe = request(
            "post", "/api/recipes", expected=200, headers=headers, json={"name": f"Synthetic PG case {number}"}
        )["id"]
        request(
            "put",
            f"/api/recipes/{recipe}/ingredients",
            headers=headers,
            json=[{"item_id": wanted, "quantity_required": 1, "unit": "each"}],
        )
        with SessionLocal() as db:
            ingredient = str(db.scalar(select(RecipeIngredient.id).where(RecipeIngredient.recipe_id == UUID(recipe))))
        return recipe, ingredient, wanted, product

    def evaluate(data):
        return request(
            "post",
            "/api/recipes/ingredient-matches/evaluate",
            headers=headers,
            json={"recipe_id": data[0], "ingredient_id": data[1]},
        )

    def confirm(review, data):
        payload = {key: review[key] for key in ("revision", "evidence_hash", "confirmation_token")}
        return client.post(
            f"/api/recipes/ingredient-matches/reviews/{review['id']}/confirm",
            headers=headers,
            json={**payload, "candidate_item_id": data[3], "confirm_global_alias": True},
        )

    def state(data, review):
        with SessionLocal() as db:
            target, product = db.get(Item, UUID(data[2])), db.get(Item, UUID(data[3]))
            receipt = db.get(RankedIngredientReview, UUID(review["id"]))
            assert db.scalar(text("SELECT 1")) == 1
            return (
                target.canonical_ingredient,
                product.canonical_ingredient,
                product.canonical_revision,
                product.canonical_provenance,
                receipt.status,
                receipt.revision,
            )

    def assert_success(data, review):
        response = confirm(review, data)
        assert response.status_code == 200, f"Confirmation returned {response.status_code}"
        current = state(data, review)
        assert current[0] == current[1] and current[3] == "confirmed" and current[4] == "confirmed"

    data = setup_case(0)
    assert_success(data, evaluate(data))
    completed = 1
    for number, table in enumerate(("inventory", "inventory_events", "items", "item_product_evidence"), 1):
        data = setup_case(number)
        review = evaluate(data)
        before = state(data, review)
        with engine.connect() as blocker:
            transaction = blocker.begin()
            blocker.execute(text(f"LOCK TABLE {table} IN ROW EXCLUSIVE MODE"))
            started = time.monotonic()
            response = confirm(review, data)
            assert response.status_code == 409, f"{table} blocker returned {response.status_code}"
            assert time.monotonic() - started < 3, f"{table} conflict did not fail promptly"
            assert state(data, review) == before, f"{table} conflict changed identity/receipt"
            transaction.rollback()
        assert_success(data, evaluate(data))
        completed += 1

    # A real fixture trigger changes the review revision AFTER the selected
    # product update. Final receipt CAS must fail and roll back both writes.
    data = setup_case(9)
    review = evaluate(data)
    before = state(data, review)
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION ranked_ci_conflict() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                UPDATE ranked_ingredient_reviews SET revision = revision + 1
                  WHERE id = TG_ARGV[0]::uuid;
                RETURN NEW;
            END $$
        """)
        )
        # UUIDs come from generated API fixture records, never external input.
        connection.execute(
            text(f"""
            CREATE TRIGGER ranked_ci_conflict AFTER UPDATE OF canonical_ingredient ON items
            FOR EACH ROW WHEN (NEW.id = '{UUID(data[3])}'::uuid)
            EXECUTE FUNCTION ranked_ci_conflict('{UUID(review['id'])}')
        """)
        )
    try:
        response = confirm(review, data)
        assert response.status_code == 409, f"Final CAS fault returned {response.status_code}"
        assert state(data, review) == before, "Final CAS conflict failed to roll back alias and receipt"
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER IF EXISTS ranked_ci_conflict ON items"))
            connection.execute(text("DROP FUNCTION IF EXISTS ranked_ci_conflict()"))
    assert_success(data, evaluate(data))
    completed += 1
    assert calls, "Model fixtures were not exercised"
    client.close()
    engine.dispose()
    sys.stdout.write(
        f"PostgreSQL ranked identity smoke: {completed}/6 cases passed; model fixtures only, 0 provider requests.\n"
    )


if __name__ == "__main__":
    main()
