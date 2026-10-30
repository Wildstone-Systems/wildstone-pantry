"""Guarded real PostgreSQL receipt draft commit smoke in its own disposable DB."""

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
    name = "pantryos_receipt_ci_" + uuid4().hex
    if not re.fullmatch(r"pantryos_receipt_ci_[a-f0-9]{32}", name):
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
    isolated_cwd = tempfile.TemporaryDirectory(prefix="pantryos-receipt-ci-")
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
        from threading import Barrier
        from unittest.mock import patch

        from app.models import InventoryTransaction, Item, Receipt
        from modules.shopping.services import receipts

        credentials = {"email": "synthetic-receipt-ci@example.com", "password": "SyntheticReceiptCi987!"}
        response = client.post("/api/auth/bootstrap-admin", json=credentials)
        assert response.status_code in (200, 201)
        token = request("post", "/api/auth/login", json=credentials)["token"]
        headers = {"Authorization": "Bearer " + token}
        item = request("post", "/api/items", expected=201, headers=headers, json={"name": "Milk"})["id"]
        location = request(
            "post", "/api/locations", expected=201, headers=headers, json={"name": "Synthetic receipt CI pantry"}
        )["id"]

        def counts():
            with SessionLocal() as db:
                return tuple(db.query(model).count() for model in (Receipt, InventoryTransaction, Item))

        def draft(raw):
            value = request(
                "post", "/api/receipts/draft", headers=headers, json={"location_id": location, "raw_text": raw}
            )
            for line in value["lines"]:
                line["confirmed"] = True
                line["matched_item_id"] = item
            return value

        def commit(value, expected=200):
            return request(
                "post",
                f"/api/receipts/drafts/{value['draft_id']}/commit",
                expected=expected,
                headers=headers,
                json={"token": value["token"], "lines": value["lines"]},
            )

        from core.events import get_event_bus

        published = []

        def observe(event, _db=None):
            # A separate PostgreSQL connection sees only durable outer commits.
            assert counts()[1] > len(published), "Notification escaped before durable stock commit"
            published.append(event)

        event_patch = patch.object(get_event_bus(), "publish", side_effect=observe)
        event_patch.start()

        first = draft("Milk,2,each")
        assert counts() == (0, 0, 1), "Preview must not write receipt/stock/catalog truth"
        receipt = commit(first)
        assert commit(first) == receipt and counts() == (1, 1, 1), "Replay duplicated stock"
        assert len(published) == 1, "Replay repeated stock notification"
        first["lines"][0]["quantity"] = "3"
        commit(first, expected=409)
        assert counts() == (1, 1, 1), "Changed replay must not write"

        failure = draft("Milk,2,each\nMilk,3,each")
        original = receipts.add_inventory
        calls = 0

        def fail_second(db, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("Synthetic CI second-line failure")
            return original(db, **kwargs)

        before = counts()
        with patch.object(receipts, "add_inventory", side_effect=fail_second):
            commit(failure, expected=409)
        assert counts() == before, "Real PostgreSQL outer transaction did not roll back"
        assert len(published) == 1, "Rolled-back stock emitted a notification"
        commit(failure)
        assert counts() == (2, 3, 1), "Failed draft did not recover exactly once"
        assert len(published) == 3

        race = draft("Milk,1,each")
        ready = Barrier(2)

        def contender():
            ready.wait(timeout=5)
            return commit(race)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        assert results[0] == results[1], "Concurrent identical retries returned different receipts"
        assert counts() == (3, 4, 1), "Concurrent retry duplicated receipt or stock"
        assert len(published) == 4

        exclusion = draft("Milk,1,each\nRETURN Milk -2.99\nTOTAL 2.99")
        for line in exclusion["lines"][1:]:
            line["excluded"] = True
        commit(exclusion)
        assert counts() == (4, 5, 1), "Excluded financial/return rows added stock"
        assert len(published) == 5
        sys.stdout.write(
            "PostgreSQL receipt review smoke: preview, replay, changed retry, rollback/recovery, "
            "concurrent commit and exclusions passed; 0 provider requests.\n"
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
                or not re.fullmatch(r"pantryos_receipt_ci_[a-f0-9]{32}", created_name)
            ):
                raise RuntimeError("Refusing to drop an unexpected database")
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{created_name}" WITH (FORCE)'))
        admin_engine.dispose()
        os.chdir(original_cwd)
        isolated_cwd.cleanup()


if __name__ == "__main__":
    main()
