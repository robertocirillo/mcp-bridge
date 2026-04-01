from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


def remove_dir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def file_is_stale(*, path: Path, cutoff: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime) <= cutoff
    except OSError:
        return False


def stale_cutoff(*, ttl_seconds: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now()) - timedelta(seconds=ttl_seconds)
