"""Guarded real PostgreSQL CSV draft commit smoke in its own disposable DB."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


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
    name = "pantryos_csv_ci_" + uuid4().hex
    if not re.fullmatch(r"pantryos_csv_ci_[a-f0-9]{32}", name):
        raise RuntimeError("Invalid generated CI database name")
    created_name = None
    admin_engine = create_engine(
        base_url,
        isolation_level="AUTOCOMMIT",
        connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=5000"},
    )
    app_engine = None
    client = None
    event_patch = None
    original_cwd = os.getcwd()
    isolated_cwd = tempfile.TemporaryDirectory(prefix="pantryos-csv-ci-")
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

        from concurrent.futures import ThreadPoolExecutor
        from datetime import UTC, date, datetime
        from threading import Barrier
        from unittest.mock import patch
        from uuid import UUID

        from alembic import command
        from alembic.config import Config

        from app.models import InventoryTransaction, Item, Purchase, PurchaseLineItem
        from core.events import get_event_bus
        from modules.shopping.purchase_review_models import PurchaseCsvClaim
        from modules.shopping.services import purchase_review
        from modules.shopping.services.purchases import compute_purchase_fingerprint

        credentials = {"email": "synthetic-csv-ci@example.com", "password": "SyntheticCsvCi987!"}
        response = client.post("/api/auth/bootstrap-admin", json=credentials)
        assert response.status_code in (200, 201)
        token = request("post", "/api/auth/login", json=credentials)["token"]
        headers = {"Authorization": "Bearer " + token}
        item = request("post", "/api/items", expected=201, headers=headers, json={"name": "Milk"})["id"]
        location = request(
            "post", "/api/locations", expected=201, headers=headers, json={"name": "Synthetic CSV CI pantry"}
        )["id"]

        def payload(csv, merchant="Synthetic CI"):
            return {
                "location_id": location,
                "csv_text": csv,
                "merchant": merchant,
                "purchased_on": "2026-10-28",
                "create_missing_items": False,
                "apply_to_inventory": True,
                "default_unit": "each",
            }

        def counts():
            with SessionLocal() as db:
                return tuple(
                    db.query(model).count()
                    for model in (Purchase, PurchaseLineItem, InventoryTransaction, Item, PurchaseCsvClaim)
                )

        # Exercise actual65 backfill with pre-existing duplicate history,
        # leaving both original purchases intact and choosing the oldest.
        historical = payload("name,quantity,unit\nMilk,9,each", merchant="Historical CI")
        old_fingerprint = compute_purchase_fingerprint(
            csv_text=historical["csv_text"],
            merchant=historical["merchant"],
            purchased_on=date(2026, 10, 28),
            location_id=UUID(location),
        )
        migration_config = Config(str(backend / "alembic.ini"))
        migration_config.set_main_option("script_location", str(backend / "migrations"))
        engine.dispose()
        command.downgrade(migration_config, "0064_receipt_review_drafts")
        oldest, newer = uuid4(), uuid4()
        with SessionLocal() as db:
            db.add_all(
                [
                    Purchase(
                        id=oldest,
                        location_id=UUID(location),
                        source="csv",
                        status="processed",
                        source_fingerprint=old_fingerprint,
                        merchant="Historical CI",
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ),
                    Purchase(
                        id=newer,
                        location_id=UUID(location),
                        source="csv",
                        status="processed",
                        source_fingerprint=old_fingerprint,
                        merchant="Historical CI",
                        created_at=datetime(2026, 1, 2, tzinfo=UTC),
                    ),
                ]
            )
            db.commit()
        engine.dispose()
        command.upgrade(migration_config, "head")
        with SessionLocal() as db:
            claim = db.get(PurchaseCsvClaim, (UUID(location), old_fingerprint))
            assert claim is not None and claim.purchase_id == oldest
            assert db.get(Purchase, oldest) and db.get(Purchase, newer)
        assert counts() == (2, 0, 0, 1, 1), "Backfill altered historical purchase rows"
        historical_preview = request("post", "/api/purchases/preview-csv", headers=headers, json=historical)
        assert historical_preview["duplicate_of_purchase_id"] == str(oldest)

        def draft(csv, merchant="Synthetic CI"):
            value = request("post", "/api/purchases/draft-csv", headers=headers, json=payload(csv, merchant))
            for line in value["lines"]:
                line["confirmed"] = True
                line["matched_item_id"] = item
            return value

        def commit_raw(value):
            return client.post(
                f"/api/purchases/drafts/{value['draft_id']}/commit",
                headers=headers,
                json={"token": value["token"], "fingerprint": value["fingerprint"], "lines": value["lines"]},
            )

        def commit(value, expected=200):
            response = commit_raw(value)
            assert response.status_code == expected, f"Reviewed commit: {response.status_code}"
            return response.json()

        old_draft = draft(historical["csv_text"], merchant="Historical CI")
        assert old_draft["duplicate_of_purchase_id"] == str(oldest)
        commit(old_draft, expected=409)
        assert counts() == (2, 0, 0, 1, 1)

        published = []

        def observe(event, _db=None):
            assert counts()[2] > len(published), "Notification escaped before durable CSV stock commit"
            published.append(event)

        event_patch = patch.object(get_event_bus(), "publish", side_effect=observe)
        event_patch.start()

        first = draft("name,quantity,unit\nMilk,2,each")
        assert counts() == (2, 0, 0, 1, 1), "Preview wrote purchase/stock/catalog truth"
        purchase = commit(first)
        assert commit(first) == purchase and counts() == (3, 1, 1, 1, 2)
        assert len(published) == 1, "Exact replay repeated a notification"
        first["lines"][0]["quantity"] = "3"
        commit(first, expected=409)
        assert counts() == (3, 1, 1, 1, 2)

        conflict = request(
            "post",
            "/api/purchases/draft-csv",
            headers=headers,
            json={
                **payload("name,quantity,unit,barcode\nCI New drink,1,each,987654321098\nMilk,1,each,987654321098"),
                "create_missing_items": True,
            },
        )
        conflict["lines"][0].update(confirmed=True, create_new=True, matched_item_id=None)
        conflict["lines"][1].update(confirmed=True, create_new=False, matched_item_id=item)
        before = counts()
        commit(conflict, expected=409)
        assert counts() == before and len(published) == 1, "Same-batch barcode conflict leaked writes or events"

        failure = draft("name,quantity,unit\nMilk,2,each\nMilk,3,each")
        original = purchase_review.add_inventory
        calls = 0

        def fail_second(db, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("Synthetic CI second CSV line failure")
            return original(db, **kwargs)

        before = counts()
        with patch.object(purchase_review, "add_inventory", side_effect=fail_second):
            commit(failure, expected=409)
        assert counts() == before and len(published) == 1, "Failed import leaked rows, claim or notification"
        commit(failure)
        assert counts() == (4, 3, 3, 1, 3) and len(published) == 3

        def race(callables):
            ready = Barrier(len(callables))

            def contender(operation):
                ready.wait(timeout=5)
                response = operation()
                return response.status_code, response.json()

            with ThreadPoolExecutor(max_workers=len(callables)) as pool:
                futures = [pool.submit(contender, operation) for operation in callables]
                return [future.result(timeout=20) for future in futures]

        same_source = "name,quantity,unit\nMilk,4,each"
        left, right = draft(same_source), draft(same_source)
        responses = race([lambda: commit_raw(left), lambda: commit_raw(right)])
        assert sorted(status for status, _ in responses) == [200, 409], "Different drafts both imported one source"
        assert counts() == (5, 4, 4, 1, 4) and len(published) == 4

        legacy_source = "name,quantity,unit\nMilk,5,each"
        reviewed = draft(legacy_source, merchant="Legacy race CI")
        responses = race(
            [
                lambda: commit_raw(reviewed),
                lambda: client.post(
                    "/api/purchases/import-csv", headers=headers, json=payload(legacy_source, merchant="Legacy race CI")
                ),
            ]
        )
        assert sorted(status for status, _ in responses) == [
            200,
            409,
        ], "Legacy and reviewed imports bypassed shared claim"
        assert counts() == (6, 5, 5, 1, 5) and len(published) == 5

        same_draft = draft("name,quantity,unit\nMilk,6,each")
        responses = race([lambda: commit_raw(same_draft), lambda: commit_raw(same_draft)])
        assert [status for status, _ in responses] == [200, 200] and responses[0][1] == responses[1][1]
        assert counts() == (7, 6, 6, 1, 6) and len(published) == 6

        excluded = draft("name,quantity,unit\nMilk,1,each\nRefund,-1,each\n,3,each")
        for line in excluded["lines"][1:]:
            line.update(excluded=True, name="")
        commit(excluded)
        assert counts() == (8, 7, 7, 1, 7) and len(published) == 7

        def new_item_draft(csv, merchant):
            value = request(
                "post",
                "/api/purchases/draft-csv",
                headers=headers,
                json={**payload(csv, merchant), "create_missing_items": True},
            )
            for line in value["lines"]:
                line.update(confirmed=True, role="stock", matched_item_id=None, create_new=True)
            return value

        def item_creation_race(drafts, expected_new_items):
            before = counts()
            before_events = len(published)
            responses = race([lambda value=value: commit_raw(value) for value in drafts])
            assert all(status == 200 for status, _ in responses), "Catalog creation race did not complete"
            item_sets = [{line["matched_item_id"] for line in result["lines"]} for _, result in responses]
            assert item_sets[0] == item_sets[1], "Equivalent creation choices produced different catalog items"
            rows = sum(len(value["lines"]) for value in drafts)
            after = counts()
            assert after == (
                before[0] + len(drafts),
                before[1] + rows,
                before[2] + rows,
                before[3] + expected_new_items,
                before[4] + len(drafts),
            )
            assert len(published) == before_events + rows

        # Distinct sources have distinct source claims; catalog identity locking
        # must still unify the same explicit new item with and without a code.
        item_creation_race(
            [
                new_item_draft("name,quantity,unit,barcode\nCI New spice,1,each,012345678905", "Mixed code A"),
                new_item_draft("name,quantity,unit,barcode\nCI New spice,1,each,", "Mixed code B"),
            ],
            expected_new_items=1,
        )
        # Equivalent UPC/EAN forms must share a lock even when names differ.
        item_creation_race(
            [
                new_item_draft("name,quantity,unit,barcode\nCI Variant spice,1,each,036000291452", "GTIN A"),
                new_item_draft("name,quantity,unit,barcode\nCI Variant seasoning,1,each,0036000291452", "GTIN B"),
            ],
            expected_new_items=1,
        )
        # Reversed source order must not reverse the catalog lock acquisition.
        item_creation_race(
            [
                new_item_draft("name,quantity,unit\nCI Parsley seed,1,each\nCI Thyme leaf,1,each", "Reversed A"),
                new_item_draft("name,quantity,unit\nCI Thyme leaf,1,each\nCI Parsley seed,1,each", "Reversed B"),
            ],
            expected_new_items=2,
        )
        sys.stdout.write(
            "PostgreSQL CSV review smoke: nondestructive backfill, source claims, replay, "
            "rollback/recovery, same-batch barcode conflict, cross-draft and legacy races, same-draft race, exclusions, "
            "mixed/GTIN/reversed-batch catalog creation "
            "passed; 0 provider requests.\n"
        )

    finally:
        if event_patch is not None:
            event_patch.stop()
        if client is not None:
            client.close()
        if app_engine is not None:
            app_engine.dispose()
        if created_name is not None:
            if (
                created_name != name
                or created_name == "pantryos"
                or not re.fullmatch(r"pantryos_csv_ci_[a-f0-9]{32}", created_name)
            ):
                raise RuntimeError("Refusing to drop an unexpected database")
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{created_name}" WITH (FORCE)'))
        admin_engine.dispose()
        os.chdir(original_cwd)
        isolated_cwd.cleanup()


if __name__ == "__main__":
    main()
