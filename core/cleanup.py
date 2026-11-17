"""Remove task folders and download cache older than the retention window."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from config.settings import get_settings


def cleanup_expired(hours: float | None = None, root: Path | None = None) -> list[str]:
    settings = get_settings()
    limit = settings.cleanup_hours if hours is None else hours
    base = root or settings.workspace_dir
    if not base.exists():
        return []
    cutoff = time.time() - limit * 3600
    removed: list[str] = []
    for child in base.iterdir():
        if child.name == "_cache":
            for file in child.rglob("*"):
                if file.is_file() and file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)
                    removed.append(str(file))
            continue
        if not child.is_dir():
            continue
        status_path = child / "status.json"
        state = None
        stamp = child.stat().st_mtime
        if status_path.exists():
            stamp = max(stamp, status_path.stat().st_mtime)
            try:
                state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
            except json.JSONDecodeError:
                state = None
        if state in {"running", "review"}:
            continue
        if stamp < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed
