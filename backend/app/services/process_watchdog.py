"""Long-run process watchdog for browser automation.

The cleanup is intentionally narrow:
- only Windows process inspection is enabled for now;
- only Camoufox/Firefox roots using this project's configured profiles dir are
  candidates;
- currently leased profiles in this backend process are protected;
- Node/Vite/Codex processes are ignored unless they are stale Playwright driver
  processes attached to a stale project browser tree.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..config import settings
from .browser_stack import active_profile_paths
from .process_utils import hidden_subprocess_kwargs


TERMINAL_PROCESS_NAMES = {"camoufox.exe", "firefox.exe", "node.exe"}
PLAYWRIGHT_DRIVER_MARKERS = (r"\playwright\driver", r"\ms-playwright\driver")


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    name: str
    command_line: str
    created_at: datetime | None = None

    @property
    def age_seconds(self) -> float:
        if not self.created_at:
            return 0.0
        now = datetime.now(timezone.utc)
        created = self.created_at.astimezone(timezone.utc) if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        return max(0.0, (now - created).total_seconds())


def _norm_path(value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(value))
    except Exception:
        return os.path.normcase(str(value or ""))


def _is_relative_to(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def extract_profile_path(command_line: str) -> str:
    match = re.search(r"(?:^|\s)-profile\s+(?:\"([^\"]+)\"|([^\s]+))", command_line or "", flags=re.IGNORECASE)
    return str(match.group(1) or match.group(2) or "").strip() if match else ""


def _is_playwright_driver_command(command_line: str) -> bool:
    lowered = str(command_line or "").lower()
    return any(marker in lowered for marker in PLAYWRIGHT_DRIVER_MARKERS)


def _parse_process_rows(raw: str) -> list[ProcessInfo]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    rows: list[ProcessInfo] = []
    items = data if isinstance(data, list) else []
    for item in items:
        try:
            created_raw = str(item.get("CreatedAt") or "")
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00")) if created_raw else None
            rows.append(
                ProcessInfo(
                    pid=int(item.get("ProcessId") or 0),
                    parent_pid=int(item.get("ParentProcessId") or 0),
                    name=str(item.get("Name") or "").lower(),
                    command_line=str(item.get("CommandLine") or ""),
                    created_at=created,
                )
            )
        except Exception:
            continue
    return [row for row in rows if row.pid > 0 and row.name in TERMINAL_PROCESS_NAMES]


def _children_by_parent(processes: Iterable[ProcessInfo]) -> dict[int, list[ProcessInfo]]:
    children: dict[int, list[ProcessInfo]] = {}
    for process in processes:
        children.setdefault(process.parent_pid, []).append(process)
    return children


def _descendant_pids(root_pid: int, children: dict[int, list[ProcessInfo]]) -> set[int]:
    out: set[int] = set()
    stack = [root_pid]
    while stack:
        parent = stack.pop()
        for child in children.get(parent, []):
            if child.pid in out:
                continue
            out.add(child.pid)
            stack.append(child.pid)
    return out


def select_stale_project_browser_pids(
    processes: list[ProcessInfo],
    *,
    profiles_root: str,
    active_profiles: set[str] | None = None,
    browser_stale_seconds: float,
    driver_stale_seconds: float,
    current_pid: int | None = None,
) -> set[int]:
    """Select stale automation process trees that belong to this project."""
    root = _norm_path(profiles_root)
    active = {_norm_path(path) for path in (active_profiles or set())}
    children = _children_by_parent(processes)
    by_pid = {process.pid: process for process in processes}
    stale_browser_roots: set[int] = set()

    for process in processes:
        if process.name not in {"camoufox.exe", "firefox.exe"}:
            continue
        profile_path = extract_profile_path(process.command_line)
        if not profile_path:
            continue
        normalized_profile = _norm_path(profile_path)
        if not _is_relative_to(normalized_profile, root):
            continue
        if normalized_profile in active:
            continue
        if process.age_seconds >= browser_stale_seconds:
            stale_browser_roots.add(process.pid)

    selected: set[int] = set()
    for root_pid in stale_browser_roots:
        selected.add(root_pid)
        selected.update(_descendant_pids(root_pid, children))
        parent = by_pid.get(by_pid[root_pid].parent_pid)
        if (
            parent
            and parent.name == "node.exe"
            and _is_playwright_driver_command(parent.command_line)
            and parent.age_seconds >= driver_stale_seconds
        ):
            selected.add(parent.pid)

    # Stale Playwright drivers with no children can be left behind after failed
    # browser launches.  Restrict to this backend as parent when current_pid is
    # available; otherwise skip this extra cleanup to stay conservative.
    if current_pid:
        for process in processes:
            if process.name != "node.exe":
                continue
            if not _is_playwright_driver_command(process.command_line):
                continue
            if process.parent_pid != current_pid:
                continue
            if children.get(process.pid):
                continue
            if process.age_seconds >= driver_stale_seconds:
                selected.add(process.pid)

    return selected


def _powershell_process_snapshot() -> list[ProcessInfo]:
    if sys.platform != "win32":
        return []
    script = r"""
$names = @('node.exe','camoufox.exe','firefox.exe')
Get-CimInstance Win32_Process |
  Where-Object { $names -contains $_.Name } |
  ForEach-Object {
    [pscustomobject]@{
      ProcessId = $_.ProcessId
      ParentProcessId = $_.ParentProcessId
      Name = $_.Name
      CommandLine = $_.CommandLine
      CreatedAt = if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' }
    }
  } | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return []
    return _parse_process_rows(result.stdout)


def _stop_processes(pids: Iterable[int]) -> None:
    unique = sorted({int(pid) for pid in pids if int(pid) > 0})
    if not unique or sys.platform != "win32":
        return
    quoted = ",".join(str(pid) for pid in unique)
    script = f"Stop-Process -Id {quoted} -Force -ErrorAction SilentlyContinue"
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
        **hidden_subprocess_kwargs(),
    )


def cleanup_stale_browser_processes() -> set[int]:
    processes = _powershell_process_snapshot()
    selected = select_stale_project_browser_pids(
        processes,
        profiles_root=settings.profiles_dir,
        active_profiles=active_profile_paths(),
        browser_stale_seconds=max(60, int(settings.browser_process_stale_minutes or 45) * 60),
        driver_stale_seconds=max(60, int(settings.playwright_driver_stale_minutes or 45) * 60),
        current_pid=os.getpid(),
    )
    _stop_processes(selected)
    return selected


class ProcessWatchdog:
    def __init__(self, interval_seconds: int | None = None):
        self.interval_seconds = max(60, int(interval_seconds or settings.process_watchdog_interval_seconds or 300))
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                killed = await asyncio.to_thread(cleanup_stale_browser_processes)
                if killed:
                    from .registrator import emit_log

                    emit_log(f"[watchdog] 已清理陈旧浏览器/Playwright 进程: {sorted(killed)}", flush=True)
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
