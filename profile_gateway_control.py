#!/usr/bin/env python3
"""Persistent lifecycle control for one named Hermes gateway profile.

Designed for the custom Railway image, whose PID 1 stack is
`tini -> entrypoint -> supervisord` rather than upstream's s6-overlay image.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Callable, Sequence


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _read_linux_cmdline(pid: int) -> str:
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(
            b"\0", b" "
        ).decode("utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while _pid_is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_is_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class ProfileGatewayController:
    """Start/stop one explicitly configured profile gateway."""

    def __init__(
        self,
        *,
        profile: str,
        profile_home: Path,
        command: Sequence[str],
        popen_factory: Callable = subprocess.Popen,
        pid_alive: Callable[[int], bool] = _pid_is_alive,
        read_cmdline: Callable[[int], str] = _read_linux_cmdline,
        terminate_pid: Callable[[int], None] = _terminate_process_group,
    ) -> None:
        self.profile = profile
        self.profile_home = Path(profile_home)
        self.command = tuple(command)
        self.state_path = self.profile_home / "gateway_control.json"
        self._popen_factory = popen_factory
        self._pid_alive = pid_alive
        self._read_cmdline = read_cmdline
        self._terminate_pid = terminate_pid
        self._process = None
        self._lock = threading.RLock()

    def _read_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _read_hermes_gateway_pid(self) -> int | None:
        """Read upstream's JSON PID record, with legacy plain-int fallback."""
        try:
            raw = (self.profile_home / "gateway.pid").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        candidate = parsed.get("pid") if isinstance(parsed, dict) else parsed
        try:
            pid = int(candidate)
        except (TypeError, ValueError):
            return None
        return pid if pid > 1 else None

    def _write_state(self, state: dict) -> None:
        self.profile_home.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _pid_belongs_to_profile_gateway(self, pid: object) -> bool:
        if not isinstance(pid, int) or pid <= 1 or not self._pid_alive(pid):
            return False
        try:
            argv = shlex.split(self._read_cmdline(pid))
        except ValueError:
            return False
        has_profile = any(
            argv[index:index + 2] in (["-p", self.profile], ["--profile", self.profile])
            for index in range(max(0, len(argv) - 1))
        ) or f"--profile={self.profile}" in argv
        has_gateway_run = any(
            argv[index:index + 2] == ["gateway", "run"]
            for index in range(max(0, len(argv) - 1))
        )
        return has_profile and has_gateway_run

    @_synchronized
    def status(self) -> dict:
        state = self._read_state()
        child_running = self._process is not None and self._process.poll() is None
        persisted_pid = state.get("pid")
        persisted_running = self._pid_belongs_to_profile_gateway(persisted_pid)
        hermes_pid = self._read_hermes_gateway_pid()
        hermes_running = self._pid_belongs_to_profile_gateway(hermes_pid)
        running = child_running or persisted_running or hermes_running
        pid = (
            self._process.pid
            if child_running
            else persisted_pid
            if persisted_running
            else hermes_pid
            if hermes_running
            else None
        )
        desired = state.get("desired")
        if desired not in {"running", "stopped"}:
            desired = "running" if running else "stopped"
        return {
            "profile": self.profile,
            "desired": desired,
            "running": running,
            "pid": pid,
            "last_error": state.get("last_error"),
        }

    @_synchronized
    def start(self) -> dict:
        current = self.status()
        if current["running"]:
            return current
        if self._process is None or self._process.poll() is not None:
            self._process = self._popen_factory(
                self.command,
                start_new_session=True,
            )
        self._write_state({
            "desired": "running",
            "pid": self._process.pid,
            "last_error": None,
        })
        return self.status()

    @_synchronized
    def stop(self) -> dict:
        current = self.status()
        if current["running"] and current["pid"] is not None:
            self._terminate_pid(current["pid"])
        self._process = None
        self._write_state({
            "desired": "stopped",
            "pid": None,
            "last_error": None,
        })
        return self.status()

    @_synchronized
    def restart(self) -> dict:
        self.stop()
        return self.start()

    @_synchronized
    def reconcile(self) -> dict:
        state = self._read_state()
        current = self.status()
        if state.get("desired") == "running" and not current["running"]:
            return self.start()
        return current
