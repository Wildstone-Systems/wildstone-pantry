"""System surface (ADR 0018 §6): version + backups.

``/version`` is open like ``/health`` — the SPA shows it and the launcher
polls it. Backup management is real-admin-only (``require_authenticated_admin``),
and the download endpoint resolves names through the strictly-validated
``backup_file`` helper so it can never serve an arbitrary path.
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core import settings
from app.core.security import require_authenticated_admin
from app.services import backup as backup_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# Update-manifest cache: (fetched_at_monotonic, parsed | None)
_MANIFEST_TTL_SECONDS = 6 * 3600
_manifest_cache: tuple[float, dict | None] = (0.0, None)


class AvailableUpdate(BaseModel):
    version: str
    urgency: str = "normal"
    notes_url: str | None = None


class VersionInfo(BaseModel):
    current: str
    available: AvailableUpdate | None = None


class BackupList(BaseModel):
    enabled: bool
    backups: list[dict]


def _fetch_available() -> AvailableUpdate | None:
    """Best-effort check of the update manifest feed. Never raises; a free-mode
    instance with no feed configured makes no network calls at all."""
    global _manifest_cache
    url = settings.update_manifest_url.strip()
    if not url:
        return None
    fetched_at, cached = _manifest_cache
    if time.monotonic() - fetched_at < _MANIFEST_TTL_SECONDS:
        data = cached
    else:
        data = None
        try:
            resp = httpx.get(url, timeout=5, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()
        except Exception:  # noqa: BLE001 - update checks must never break the app
            logger.info("update manifest fetch failed", exc_info=True)
        _manifest_cache = (time.monotonic(), data)

    if not isinstance(data, dict):
        return None
    version = str(data.get("version", "")).strip()
    # Tags carry a leading v (v0.2.0) while the manifest strips it — normalize
    # both sides so an install never reports itself as its own update.
    if not version or version.removeprefix("v") == settings.app_version.removeprefix("v"):
        return None
    return AvailableUpdate(
        version=version,
        urgency=str(data.get("urgency", "normal")),
        notes_url=data.get("notes_url"),
    )


@router.get("/version", response_model=VersionInfo)
def version_route() -> VersionInfo:
    return VersionInfo(current=settings.app_version, available=_fetch_available())


@router.get("/backups", response_model=BackupList, dependencies=[Depends(require_authenticated_admin)])
def list_backups_route() -> BackupList:
    return BackupList(
        enabled=backup_service.backup_enabled(),
        backups=[b.__dict__ for b in backup_service.list_backups()],
    )


@router.post("/backups", dependencies=[Depends(require_authenticated_admin)])
def run_backup_route() -> dict:
    if not backup_service.backup_enabled():
        raise HTTPException(status_code=409, detail="Backups are disabled — set BACKUP_DIR (product installs mount /backups).")
    try:
        info = backup_service.run_backup()
    except Exception as exc:  # noqa: BLE001 - surface the reason to the admin
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}") from exc
    return info.__dict__


@router.get("/backups/{name}/download", dependencies=[Depends(require_authenticated_admin)])
def download_backup_route(name: str) -> FileResponse:
    path = backup_service.backup_file(name)
    if not path:
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
