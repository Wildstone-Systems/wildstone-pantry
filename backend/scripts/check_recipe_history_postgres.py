"""Guarded real PostgreSQL recipe-edit/history smoke in its own disposable DB."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def check_import_review(client, headers, session_factory):
    """Exercise the nullable review receipt and savepoint writes on real PostgreSQL."""
    from copy import deepcopy
    from unittest.mock import patch

    from sqlalchemy import func, select

    from app.models import InventoryTransaction, Item, Recipe, RecipeIngredient
    from modules.planning.services import recipe_review

    def request(method, path, expected=200, **kwargs):
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == expected, f"{method} {path}: {response.status_code}"
        return response.json()

    def counts():
        with session_factory() as db:
            return tuple(db.scalar(select(func.count()).select_from(model)) for model in (
                Item, Recipe, RecipeIngredient, InventoryTransaction,
            ))

    legacy = request("post", "/api/recipes", json={"name": "Unreviewed synthetic legacy recipe"})
    assert request("get", f"/api/recipes/{legacy['id']}")["recipe"]["import_review"] is None

    raw = "\U0001f963 Original source\r\nIngredients:\r\n1 1/2 cup smoked paprika\r\n1 1/2 cup smoked paprika\r\nMix well\r\n"
    recipe = {
        "name": "Synthetic reviewed PostgreSQL recipe",
        "instructions": "Mix well",
        "servings": 2,
        "external_source": {"source": "synthetic-review", "id": uuid4().hex},
        "image_url": "https://example.invalid/synthetic-recipe.png",
        "create_missing_items": True,
        "ingredients": [{"name": "Paprika", "quantity": 1.5, "unit": "cup",
                         "raw_line": "1 1/2 cup smoked paprika", "source_line_id": "line:2"}],
    }
    payload = {"recipe": recipe, "source": {"kind": "text", "raw_text": raw}}
    before = counts()
    with patch.object(recipe_review, "run_evaluation", side_effect=AssertionError("Unexpected provider request")):
        # Ordinary structured validation must accept a retained document-line
        # reference without inventing a document span or changing create input.
        validation = request("post", "/api/recipes/validate", json=recipe)
        assert validation["source_lines"][-1]["field_path"] == "ingredients[0]"
        assert validation["source_lines"][-1]["start"] is None
        preview = request("post", "/api/recipes/import-preview", json=payload)
        assert preview["provider_requests"] == 0
        source = {line["source_line_id"]: line for line in preview["lines"]}
        assert source["line:2"]["start"] != source["line:3"]["start"]
        for key in ("line:2", "line:3"):
            line = source[key]
            assert raw[line["start"]:line["end"]] == "1 1/2 cup smoked paprika"
        assert any("smoked" in warning for warning in source["line:2"]["warnings"])
        request("post", "/api/recipes/import-reviewed", expected=422, json=payload)
        edited = deepcopy(payload)
        edited["recipe"]["ingredients"][0]["name"] = "Smoked paprika"
        request("post", "/api/recipes/import-reviewed", expected=409,
                json={**edited, "confirmed": True, "reviewed_input_hash": preview["binding"]})
        assert counts() == before
        fresh = request("post", "/api/recipes/import-preview", json=edited)

        # Receipt attachment is the last stage, after catalog/recipe/ingredient
        # savepoints have committed. None may escape a failed outer transaction.
        with patch.object(recipe_review.repository, "attach", side_effect=ValueError("Injected receipt failure")):
            request("post", "/api/recipes/import-reviewed", expected=409,
                    json={**edited, "confirmed": True, "reviewed_input_hash": fresh["binding"]})
        assert counts() == before
        saved = request("post", "/api/recipes/import-reviewed",
                        json={**edited, "confirmed": True, "reviewed_input_hash": fresh["binding"]})
        detail = request("get", f"/api/recipes/{saved['id']}")
        receipt = detail["recipe"]["import_review"]
        assert receipt["confirmed"] and receipt["stale"] is False
        assert receipt["actor_id"] and receipt["source_hash"] and receipt["material_hash"]
        assert all("raw_line" not in line for line in receipt["lines"])
        assert detail["recipe"]["source_text"] == raw
        assert detail["recipe"]["external_id"] == recipe["external_source"]["id"]
        assert len(detail["ingredients"]) == 1
        assert float(detail["ingredients"][0]["quantity_required"]) == 1.5
        assert detail["ingredients"][0]["item_name"] == "Smoked paprika"
        after = counts()
        assert after == (before[0] + 1, before[1] + 1, before[2] + 1, before[3])
        request("put", f"/api/recipes/{saved['id']}", json={"name": "Edited after review"})
        changed = request("get", f"/api/recipes/{saved['id']}")["recipe"]["import_review"]
        assert changed["reviewed_input_hash"] == receipt["reviewed_input_hash"] and changed["stale"] is True
        assert counts() == after

        # A different connection may add the formerly absent name after the
        # confirmation check. The importer must not silently select that item.
        from modules.catalog.schemas import ItemCreate
        from modules.catalog.services.items import create_item

        race_name = "Synthetic arriving beans " + uuid4().hex
        race_payload = {
            "recipe": {"name": "Synthetic reviewed catalog race", "create_missing_items": True,
                       "ingredients": [{"name": race_name, "quantity": 1, "unit": "tsp",
                                        "raw_line": "1 tsp " + race_name, "source_line_id": "line:2"}]},
            "source": {"kind": "text", "raw_text": "Catalog race\nIngredients\n1 tsp " + race_name},
        }
        race_preview = request("post", "/api/recipes/import-preview", json=race_payload)
        before_race = counts()
        original_worker = recipe_review.repository.worker
        arrived = False

        def arrive_before_worker(db):
            nonlocal arrived
            if not arrived:
                arrived = True
                with session_factory() as concurrent:
                    create_item(concurrent, ItemCreate(name=race_name, brand="Unreviewed arriving brand"))
            return original_worker(db)

        with patch.object(recipe_review.repository, "worker", side_effect=arrive_before_worker):
            request("post", "/api/recipes/import-reviewed", expected=409,
                    json={**race_payload, "confirmed": True, "reviewed_input_hash": race_preview["binding"]})
        assert arrived
        assert counts() == (before_race[0] + 1, *before_race[1:])
    sys.stdout.write(
        "PostgreSQL import review smoke: nullable legacy receipt, exact Unicode/duplicate source spans, "
        "retained document IDs in structured validation, no preview writes, stale acknowledgement, "
        "savepoint rollback/retry, deterministic quantity, "
        "saved source/receipt, later-edit staleness and newly-arrived catalog rejection passed; 0 provider requests.\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-ci-database", action="store_true")
    args = parser.parse_args()
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    base_url = make_url(os.environ.get("DATABASE_URL", "sqlite://"))
    if not (
        args.confirm_ci_database
        and os.environ.get("CI") == "true"
        and base_url.drivername == "postgresql+psycopg"
        and base_url.host in {"localhost", "127.0.0.1"}
        and base_url.username == "pantryos_ci"
        and base_url.database == "pantryos"
    ):
        parser.error("Requires acknowledgement and the disposable local CI PostgreSQL database")
    name = "pantryos_history_ci_" + uuid4().hex
    if not re.fullmatch(r"pantryos_history_ci_[a-f0-9]{32}", name):
        raise RuntimeError("Invalid generated CI database name")
    created_name = None
    admin_engine = create_engine(
        base_url,
        isolation_level="AUTOCOMMIT",
        connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=5000"},
    )
    app_engine = None
    client = None
    original_cwd = os.getcwd()
    isolated_cwd = tempfile.TemporaryDirectory(prefix="pantryos-history-ci-")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created_name = name
        os.environ["DATABASE_URL"] = base_url.set(database=name).render_as_string(hide_password=False)
        from cryptography.fernet import Fernet

        os.environ["CONFIG_SECRET_KEY"] = Fernet.generate_key().decode()
        backend = Path(__file__).resolve().parents[1]
        migration = (
            "import sys; from alembic import command; from alembic.config import Config; "
            "sys.path.insert(0, sys.argv[1]); c=Config(sys.argv[2]); "
            "c.set_main_option('script_location', sys.argv[3]); command.upgrade(c, 'head')"
        )
        subprocess.run(
            [sys.executable, "-c", migration, str(backend), str(backend / "alembic.ini"), str(backend / "migrations")],
            cwd=isolated_cwd.name,
            check=True,
            timeout=120,
        )
        # Empty cwd prevents accidental loading of any workspace .env file.
        os.chdir(isolated_cwd.name)
        sys.path.insert(0, str(backend))
        # No app/settings import may precede the isolated DATABASE_URL above.
        from fastapi.testclient import TestClient
        from sqlalchemy import event

        from app.db.session import SessionLocal, engine
        from app.main import app
        from modules.planning.models.ingredient_match_review import IngredientMatchReview, IngredientMatchScope

        app_engine = engine

        @event.listens_for(engine, "connect")
        def timeouts(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '5000ms'")
                cursor.execute("SET lock_timeout = '1000ms'")

        engine.dispose()
        client = TestClient(app)

        def request(method, path, expected=200, **kwargs):
            response = getattr(client, method)(path, **kwargs)
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json()

        credentials = {"email": "synthetic-history-ci@example.com", "password": "SyntheticHistoryCi987!"}
        response = client.post("/api/auth/bootstrap-admin", json=credentials)
        assert response.status_code in (200, 201)
        token = request("post", "/api/auth/login", json=credentials)["token"]
        headers = {"Authorization": "Bearer " + token}
        target = request("post", "/api/items", expected=201, headers=headers, json={"name": "Chipotle powder"})["id"]
        product = request("post", "/api/items", expected=201, headers=headers, json={"name": "Smoked chipotle spice"})[
            "id"
        ]
        location = request(
            "post", "/api/locations", expected=201, headers=headers, json={"name": "Synthetic CI pantry"}
        )["id"]
        response = client.post(
            "/api/inventory/add",
            headers=headers,
            json={"item_id": product, "location_id": location, "quantity": 2, "unit": "each"},
        )
        assert response.status_code in (200, 201)
        recipe = request(
            "post",
            "/api/recipes",
            headers=headers,
            json={"name": "Synthetic CI sauce", "instructions": "Season the sauce"},
        )["id"]
        ingredient_url = f"/api/recipes/{recipe}/ingredients"
        initial = request(
            "put",
            ingredient_url,
            headers=headers,
            json=[
                {
                    "item_id": target,
                    "quantity_required": 1,
                    "unit": "each",
                    "yield_basis": True,
                    "quantity_mode": "package",
                }
            ],
        )
        ingredient = initial["ingredients"][0]["ingredient_id"]
        review_url = f"/api/recipes/{recipe}/ingredients/{ingredient}/match-reviews"

        def create_review():
            review = request(
                "post",
                review_url,
                headers=headers,
                json={"candidate_item_id": product, "facts": {"form": "powder", "preparation": "dried"}},
            )
            assert review["provider_requests"] == 0
            return review

        def binding(review):
            return {key: review[key] for key in ("revision", "evidence_hash", "confirmation_token")}

        def confirm(review, expected=200):
            return request(
                "post",
                f"/api/ingredient-match-reviews/{review['id']}/confirm",
                expected=expected,
                headers=headers,
                json={**binding(review), "confirm": True},
            )

        def snapshot():
            return request("get", f"/api/recipes/{recipe}", headers=headers)

        def payload(current):
            return {
                "name": current["recipe"]["name"],
                "instructions": current["recipe"]["instructions"],
                "servings": current["recipe"]["servings"],
                "ingredients": [
                    {
                        "item_id": line["item_id"],
                        "quantity": float(line["quantity_required"]),
                        "unit": line["unit"],
                        "optional": line["optional"],
                        "yield_basis": line["yield_basis"],
                        "quantity_mode": line["quantity_mode"],
                    }
                    for line in current["ingredients"]
                ],
            }

        receipt = create_review()
        confirm(receipt)
        current = snapshot()
        request("put", f"/api/recipes/{recipe}", headers=headers, json=payload(current))
        assert snapshot()["ingredients"] == current["ingredients"]
        history = request("get", review_url, headers=headers)["reviews"]
        assert any(row["id"] == receipt["id"] and row["current_choice"] for row in history)

        pending = create_review()
        changed = payload(snapshot())
        changed["instructions"] = "Different recipe context"
        changed["ingredients"][0]["quantity"] = 3
        request("put", f"/api/recipes/{recipe}", headers=headers, json=changed)
        assert snapshot()["ingredients"][0]["ingredient_id"] == ingredient
        confirm(pending, expected=409)
        assert {row["id"] for row in request("get", review_url, headers=headers)["reviews"]} == {
            receipt["id"],
            pending["id"],
        }

        direct = request(
            "put", ingredient_url, headers=headers, json=[{"item_id": target, "quantity_required": 4, "unit": "each"}]
        )
        line = direct["ingredients"][0]
        assert line["ingredient_id"] == ingredient and line["yield_basis"] and line["quantity_mode"] == "package"
        request("put", ingredient_url, headers=headers, json=[])
        from uuid import UUID

        with SessionLocal() as db:
            assert db.get(IngredientMatchReview, UUID(receipt["id"])) is None
            assert db.get(IngredientMatchScope, UUID(ingredient)) is None
        check_import_review(client, headers, SessionLocal)
        sys.stdout.write(
            "PostgreSQL recipe history smoke: no-op, changed, dedicated PUT and removal passed; 0 provider requests.\n"
        )
    finally:
        if client is not None:
            client.close()
        if app_engine is not None:
            app_engine.dispose()
        if created_name is not None:
            if (
                created_name != name
                or created_name == "pantryos"
                or not re.fullmatch(r"pantryos_history_ci_[a-f0-9]{32}", created_name)
            ):
                raise RuntimeError("Refusing to drop an unexpected database")
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{created_name}" WITH (FORCE)'))
        admin_engine.dispose()
        os.chdir(original_cwd)
        isolated_cwd.cleanup()


if __name__ == "__main__":
    main()
