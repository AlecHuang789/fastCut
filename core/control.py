"""Cooperative cancel flag shared by the API and the pipeline."""

from __future__ import annotations

import contextvars
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_stops: set[str] = set()
_current: contextvars.ContextVar[str] = contextvars.ContextVar("fastcut_task", default="")


class Stopped(Exception):
    """The user asked to stop the current generation."""


def request_stop(task_id: str) -> None:
    with _lock:
        _stops.add(task_id)


def clear_stop(task_id: str) -> None:
    with _lock:
        _stops.discard(task_id)


def should_stop(task_id: str) -> bool:
    with _lock:
        return task_id in _stops


def checkpoint(task_id: str) -> None:
    if should_stop(task_id):
        raise Stopped()


def bind_task(task_id: str) -> contextvars.Token:
    return _current.set(task_id)


def reset_task(token: contextvars.Token) -> None:
    _current.reset(token)


def active_task() -> str:
    return _current.get()


@contextmanager
def task_binding(task_id: str) -> Iterator[None]:
    token = bind_task(task_id)
    try:
        yield
    finally:
        reset_task(token)


def kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _drain(pipe, sink: list[str]) -> None:
    try:
        sink.append(pipe.read() if pipe is not None else "")
    except Exception:
        sink.append("")


def run_process(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a command. A stop request kills the process tree and raises Stopped."""
    task_id = active_task()
    if task_id and should_stop(task_id):
        raise Stopped()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout_parts), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr_parts), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout if timeout else None
    stopped = False
    timed_out = False
    try:
        while proc.poll() is None:
            if task_id and should_stop(task_id):
                stopped = True
                kill_process_tree(proc)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                kill_process_tree(proc)
                break
            time.sleep(0.3)
    finally:
        for thread in threads:
            thread.join(timeout=2)
    stdout = stdout_parts[0] if stdout_parts else ""
    stderr = stderr_parts[0] if stderr_parts else ""
    if stopped:
        raise Stopped()
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout or 0, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode or 0, stdout, stderr)
