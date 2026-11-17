"""Per-task workspace and status file."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from config.settings import get_settings


class Task:
    def __init__(self, task_id: str, root: Path):
        self.id = task_id
        self.root = root
        self._state: dict = {"task_id": task_id, "state": "running", "notes": []}

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        log_path = self.root / "pipeline.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def note(self, message: str) -> None:
        notes = list(self._state.get("notes") or [])
        notes.append(message)
        self._state["notes"] = notes
        self.log(message)
        self._write()

    def status(self, **fields: object) -> None:
        from core.control import should_stop

        if should_stop(self.id) and fields.get("state") not in {"review", "failed", "done"}:
            fields = {**fields, "state": "stopping"}
        self._state.update(fields)
        self._state["task_id"] = self.id
        self._state["updated_at"] = time.time()
        self._write()

    def read_status(self) -> dict:
        path = self.root / "status.json"
        if not path.exists():
            return dict(self._state)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self) -> None:
        payload = json.dumps(self._state, ensure_ascii=False, indent=2)
        target = self.root / "status.json"
        temporary = self.root / "status.json.tmp"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)


def create_task(task_id: str | None = None) -> Task:
    settings = get_settings()
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    chosen = task_id or uuid.uuid4().hex[:12]
    root = settings.workspace_dir / chosen
    if root.exists():
        raise FileExistsError(f"任务目录已存在: {chosen}")
    for name in ("audio", "assets", "build", "hf", "input"):
        (root / name).mkdir(parents=True, exist_ok=True)
    task = Task(chosen, root)
    task.status(state="running", step="init", progress=0.02, output=None, error=None)
    task.log(f"任务 {chosen} 已创建")
    return task


def delete_task(task_id: str) -> None:
    base = get_settings().workspace_dir.resolve()
    root = (base / task_id).resolve()
    if root == base or base not in root.parents or not root.is_dir():
        raise FileNotFoundError(task_id)
    shutil.rmtree(root)


def load_task(task_id: str) -> Task:
    root = get_settings().workspace_dir / task_id
    if not root.exists():
        raise FileNotFoundError(task_id)
    task = Task(task_id, root)
    status_path = root / "status.json"
    if status_path.exists():
        task._state = json.loads(status_path.read_text(encoding="utf-8"))
    return task
