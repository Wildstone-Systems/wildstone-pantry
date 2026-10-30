"""Observable identity preservation and reviewed changes, without provider calls."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Item
from modules.catalog.services.canonical import canonical_ingredient as C


@pytest.fixture(autouse=True)
def personal_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


@pytest.mark.parametrize("left,right", [
    ("Yogurt (strawberry)", "Yogurt (plain)"),
    ("70% Chocolate", "90% Chocolate"), ("2% Milk", "Milk"),
    ("Milk (0.5%)", "Milk (2%)"),
])
def test_meaningful_composition_and_parentheses_distinct(left, right):
    assert C(left) != C(right)


def test_packaging_normalizes_without_losing_composition():
    assert C("Great Value Milk (2 %) (1 gallon)") == C("2% Milk")
    assert C("Chobani Yogurt (strawberry) 16oz") == C("Strawberry Yogurt")


def create(client, name="Organic Virgin Coconut Oil"):
    response = client.post("/api/items", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def change(client, item, key):
    return client.put(f"/api/items/{item['id']}", json={
        "canonical_ingredient": key, "confirm_identity_change": True,
        "expected_canonical_ingredient": item["canonical_ingredient"],
        "expected_canonical_revision": item["canonical_revision"],
    })


def test_confirmed_alias_survives_rename_and_deliberate_change(client):
    item = create(client)
    assert item["canonical_provenance"] == "generated"
    assert item["canonical_version"] == 2
    confirmed = change(client, item, "coconut oil").json()
    assert confirmed["canonical_provenance"] == "confirmed"
    assert confirmed["canonical_confirmed_at"]
    renamed = client.put(f"/api/items/{item['id']}", json={"name": "New Brand Coconut", "brand": "New Brand"}).json()
    assert renamed["canonical_ingredient"] == "coconut oil"
    assert renamed["canonical_revision"] == confirmed["canonical_revision"]
    assert change(client, renamed, "coconut oil virgin").status_code == 200


def test_stale_or_unconfirmed_identity_change_writes_nothing(client):
    item = create(client)
    response = client.put(f"/api/items/{item['id']}", json={"name": "Must not persist", "canonical_ingredient": "oil"})
    assert response.status_code == 400
    assert change(client, item, "coconut oil").status_code == 200
    assert change(client, item, "wrong stale key").status_code == 409
    stored = client.get(f"/api/items/{item['id']}").json()
    assert stored["name"] == item["name"]
    assert stored["canonical_ingredient"] == "coconut oil"


def test_confirmed_null_disables_equivalence_and_survives_rename(client):
    item = create(client)
    assert change(client, item, None).status_code == 200
    renamed = client.put(f"/api/items/{item['id']}", json={"name": "Coconut Oil"}).json()
    assert renamed["canonical_ingredient"] is None
    assert renamed["canonical_provenance"] == "confirmed"


def test_generated_rename_reproposes_but_legacy_unknown_does_not(client):
    item = create(client, "Milk")
    renamed = client.put(f"/api/items/{item['id']}", json={"name": "2% Milk", "energy_kcal_100g": 46}).json()
    assert renamed["name"] == "2% Milk"
    assert renamed["energy_kcal_100g"] == 46
    assert renamed["canonical_ingredient"] == "2% milk"
    with SessionLocal() as db:
        row = db.get(Item, item["id"])
        row.canonical_provenance = "legacy_unknown"
        row.canonical_version = None
        db.commit()
    legacy = client.put(f"/api/items/{item['id']}", json={"name": "90% Chocolate"}).json()
    assert legacy["canonical_ingredient"] == "2% milk"
    assert legacy["canonical_provenance"] == "legacy_unknown"


@pytest.mark.parametrize("confirmed_key", ["reviewed alias", None])
def test_barcode_generation_and_stale_reconcile_cannot_overwrite_confirmation(client, confirmed_key):
    from modules.catalog.services.barcode_providers import ProductResult
    from modules.catalog.services.barcodes import _create_item_from_result, _reconcile_item_from_result
    from modules.catalog.services.identity import IdentityConflict, confirm_identity

    with SessionLocal() as db:
        product = _create_item_from_result(db, ProductResult(barcode="123456789012", name="2% Milk"))
        assert product.canonical_version == 2
        assert product.canonical_provenance == "generated"
        product.canonical_ingredient = None
        db.commit()
        product_id = product.id
    with SessionLocal() as stale, SessionLocal() as reviewer:
        stale_item = stale.get(Item, product_id)
        confirm_identity(reviewer, item_id=product_id, key=confirmed_key, version=None)
        reviewer.commit()
        stale_item.name = "Must roll back"
        stale_item.energy_kcal_100g = 999
        with pytest.raises(IdentityConflict):
            _reconcile_item_from_result(stale, stale_item, ProductResult(barcode="123456789012", name="2% Milk", brand="Must roll back"))
        stale.rollback()
    with SessionLocal() as db:
        stored = db.get(Item, product_id)
        assert stored.canonical_ingredient == confirmed_key
        assert stored.canonical_provenance == "confirmed"
        assert stored.canonical_revision == 1
        assert stored.name == "2% Milk"
        assert stored.energy_kcal_100g is None
        assert stored.brand is None


@pytest.mark.parametrize("legacy_source", [False, True])
def test_ingredient_confirmation_records_alias_and_rejects_conflict(client, legacy_source):
    ingredient = create(client, "Coconut Oil")
    if legacy_source:
        with SessionLocal() as db:
            source = db.get(Item, ingredient["id"])
            source.canonical_version = None
            source.canonical_provenance = "legacy_unknown"
            db.commit()
        ingredient["canonical_version"] = None
    product = create(client)
    recipe = client.post("/api/recipes", json={"name": "Oil dish"}).json()
    response = client.put(f"/api/recipes/{recipe['id']}/ingredients", json=[{
        "item_id": ingredient["id"], "quantity_required": 1, "unit": "each",
    }])
    assert response.status_code == 200, response.text
    ingredient_id = client.get(f"/api/recipes/{recipe['id']}/availability").json()["ingredients"][0]["ingredient_id"]
    url = f"/api/recipes/ingredients/{ingredient_id}/confirm-item"
    result = client.post(url, json={"item_id": product["id"]})
    assert result.status_code == 200, result.text
    confirmed = client.get(f"/api/items/{product['id']}").json()
    assert confirmed["canonical_provenance"] == "confirmed"
    assert confirmed["canonical_version"] == ingredient["canonical_version"]
    changed = change(client, confirmed, "another identity").json()
    assert client.post(url, json={"item_id": product["id"]}).status_code == 409
    assert client.post(url, json={"item_id": product["id"], "replace_confirmed": True,
        "expected_canonical_ingredient": changed["canonical_ingredient"],
        "expected_canonical_revision": changed["canonical_revision"],
        "expected_ingredient_key": "stale source"}).status_code == 409
    result = client.post(url, json={"item_id": product["id"], "replace_confirmed": True,
        "expected_canonical_ingredient": changed["canonical_ingredient"],
        "expected_canonical_revision": changed["canonical_revision"],
        "expected_ingredient_key": ingredient["canonical_ingredient"]})
    assert result.status_code == 200, result.text


def test_migration_preserves_keys_and_downgrade(monkeypatch):
    path = Path(__file__).parents[1] / "migrations/versions/0058_canonical_provenance.py"
    spec = importlib.util.spec_from_file_location("identity_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE items (id TEXT PRIMARY KEY, canonical_ingredient TEXT)"))
        connection.execute(sa.text("INSERT INTO items VALUES ('historic', 'old confirmed alias'), ('milk', 'milk'), ('unknown', NULL)"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        rows = connection.execute(sa.text("SELECT canonical_ingredient, canonical_provenance, canonical_version, canonical_confirmed_at FROM items ORDER BY id")).all()
        assert rows == [("old confirmed alias", "legacy_unknown", None, None), ("milk", "legacy_unknown", None, None), (None, "legacy_unknown", None, None)]
        migration.downgrade()
        assert connection.execute(sa.text("SELECT canonical_ingredient FROM items ORDER BY id")).scalars().all() == ["old confirmed alias", "milk", None]
        migration.upgrade()
        assert connection.execute(sa.text("SELECT canonical_provenance FROM items")).scalars().all() == ["legacy_unknown"] * 3
