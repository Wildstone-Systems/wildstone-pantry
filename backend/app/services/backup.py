"""Database backups (ADR 0018 §6): backup ships before any auto-update code.

Nightly ``pg_dump -Fc`` (or a file copy for SQLite dev/test databases) into
``BACKUP_DIR``, with retention of the newest 7 plus one per ISO week up to 4
weekly keepers. Every dump gets a sidecar ``.json`` manifest recording the
app version and schema source so restores can refuse a dump from a newer
schema than the running code.

Disabled unless ``BACKUP_DIR`` is set — the product compose mounts a named
volume at ``/backups``; dev instances opt in explicitly.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import settings

logger = logging.getLogger(__name__)

DAILY_KEEP = 7
WEEKLY_KEEP = 4
_DUMP_SUFFIX = ".dump"
_META_SUFFIX = ".json"
# Run when the newest backup is older than this (a little under 24h so a
# slightly-late hourly tick doesn't skip a day).
_STALE_AFTER = timedelta(hours=23, minutes=30)


@dataclass(frozen=True)
class BackupInfo:
    name: str
    created_at: str
    app_version: str
    db: str
    size_bytes: int


def backup_enabled() -> bool:
    return bool(settings.backup_dir.strip())


def _backup_dir() -> Path:
    path = Path(settings.backup_dir.strip())
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_backups() -> list[BackupInfo]:
    if not backup_enabled():
        return []
    infos: list[BackupInfo] = []
    for meta_path in _backup_dir().glob(f"pantryos-*{_META_SUFFIX}"):
        try:
            data = json.loads(meta_path.read_text())
            infos.append(BackupInfo(**data))
        except Exception:  # noqa: BLE001 - a corrupt meta never breaks listing
            logger.warning("unreadable backup manifest: %s", meta_path)
    infos.sort(key=lambda b: b.created_at, reverse=True)
    return infos


def latest_backup_at() -> datetime | None:
    backups = list_backups()
    if not backups:
        return None
    return datetime.fromisoformat(backups[0].created_at)


def should_run(now: datetime) -> bool:
    if not backup_enabled():
        return False
    latest = latest_backup_at()
    return latest is None or (now - latest) >= _STALE_AFTER


def run_backup(now: datetime | None = None) -> BackupInfo:
    """Create one backup and prune to retention. Raises on failure."""
    if not backup_enabled():
        raise RuntimeError("Backups are disabled (BACKUP_DIR is not set).")
    now = now or datetime.now(timezone.utc)
    name = f"pantryos-{now.strftime('%Y%m%d-%H%M%S')}"
    dest = _backup_dir() / f"{name}{_DUMP_SUFFIX}"

    url = settings.database_url
    if url.startswith("sqlite"):
        db_kind = "sqlite"
        src = url.split("///", 1)[1]
        shutil.copyfile(src, dest)
    else:
        db_kind = "postgresql"
        # pg_dump accepts a connection URL, minus SQLAlchemy's driver suffix.
        pg_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(dest), "--dbname", pg_url],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump failed: {result.stderr.strip()[:500]}")

    info = BackupInfo(
        name=name,
        created_at=now.isoformat(),
        app_version=settings.app_version,
        db=db_kind,
        size_bytes=dest.stat().st_size,
    )
    (_backup_dir() / f"{name}{_META_SUFFIX}").write_text(json.dumps(asdict(info)))
    prune()
    return info


def prune() -> int:
    """Keep the newest DAILY_KEEP dumps + up to WEEKLY_KEEP one-per-ISO-week
    older keepers; delete the rest. Returns how many were deleted."""
    backups = list_backups()  # newest first
    keep = {b.name for b in backups[:DAILY_KEEP]}
    weekly_seen: set[tuple[int, int]] = set()
    for b in backups[DAILY_KEEP:]:
        created = datetime.fromisoformat(b.created_at)
        week = created.isocalendar()[:2]
        if week not in weekly_seen and len(weekly_seen) < WEEKLY_KEEP:
            weekly_seen.add(week)
            keep.add(b.name)

    deleted = 0
    for b in backups:
        if b.name in keep:
            continue
        (_backup_dir() / f"{b.name}{_DUMP_SUFFIX}").unlink(missing_ok=True)
        (_backup_dir() / f"{b.name}{_META_SUFFIX}").unlink(missing_ok=True)
        deleted += 1
    return deleted


def backup_file(name: str) -> Path | None:
    """Resolve a backup name to its dump path. Names are validated strictly —
    this feeds a download endpoint and must never traverse paths."""
    if not backup_enabled():
        return None
    if not name.startswith("pantryos-") or not name.replace("pantryos-", "").replace("-", "").isdigit():
        return None
    path = _backup_dir() / f"{name}{_DUMP_SUFFIX}"
    return path if path.is_file() else None
