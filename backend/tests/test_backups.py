"""Backups + system surface (ADR 0018 §6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings
from app.services import backup as backup_service


def _enable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    email, password = "backup-admin@example.com", "BackupPass9!"
    boot = client.post("/api/auth/bootstrap-admin", json={"email": email, "password": password})
    assert boot.status_code in {200, 409}
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_backup_disabled_by_default(client: httpx.Client) -> None:
    assert backup_service.backup_enabled() is False
    assert backup_service.should_run(datetime.now(timezone.utc)) is False
    headers = _admin_headers(client)
    listing = client.get("/api/system/backups", headers=headers).json()
    assert listing == {"enabled": False, "backups": []}
    assert client.post("/api/system/backups", headers=headers).status_code == 409


def test_run_backup_and_scheduling(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    assert backup_service.should_run(now) is True  # nothing yet → run at boot

    info = backup_service.run_backup(now)
    assert info.db == "sqlite"
    assert info.size_bytes > 0
    assert (tmp_path / f"{info.name}.dump").is_file()

    # Fresh backup → no re-run until ~24h pass.
    assert backup_service.should_run(now + timedelta(hours=1)) is False
    assert backup_service.should_run(now + timedelta(hours=24)) is True


def test_retention_keeps_7_daily_plus_4_weekly(tmp_path, monkeypatch) -> None:
    _enable(monkeypatch, tmp_path)
    start = datetime(2026, 7, 29, 3, 0, tzinfo=timezone.utc)
    # 30 daily backups → newest 7 kept + one per ISO week (up to 4) among the rest.
    for days_ago in range(30):
        backup_service.run_backup(start - timedelta(days=days_ago))
    remaining = backup_service.list_backups()
    assert len(remaining) == backup_service.DAILY_KEEP + backup_service.WEEKLY_KEEP


def test_backup_api_run_list_download(client: httpx.Client, tmp_path, monkeypatch) -> None:
    _enable(monkeypatch, tmp_path)
    headers = _admin_headers(client)

    created = client.post("/api/system/backups", headers=headers)
    assert created.status_code == 200, created.text
    name = created.json()["name"]

    listing = client.get("/api/system/backups", headers=headers).json()
    assert listing["enabled"] is True
    assert listing["backups"][0]["name"] == name

    download = client.get(f"/api/system/backups/{name}/download", headers=headers)
    assert download.status_code == 200
    assert len(download.content) == listing["backups"][0]["size_bytes"]

    # Admin-only + traversal-proof.
    assert client.get("/api/system/backups").status_code == 401
    evil = client.get("/api/system/backups/..%2F..%2Fetc%2Fpasswd/download", headers=headers)
    assert evil.status_code == 404


def test_version_endpoint_open_and_quiet_without_feed(client: httpx.Client) -> None:
    body = client.get("/api/system/version").json()
    assert body["current"] == settings.app_version
    assert body["available"] is None  # no UPDATE_MANIFEST_URL → no calls, no update
