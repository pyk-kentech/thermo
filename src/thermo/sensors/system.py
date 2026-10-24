"""프로세스, 성능 카운터, 전원 상태."""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes

import psutil

from ..model import PowerReading, Proc
from . import dotnet

_user32 = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def visible_window_pids() -> set[int]:
    pids: set[int] = set()

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    _user32.EnumWindows(_EnumProc(cb), 0)
    return pids


class Processes:
    """psutil 의 cpu_percent 는 같은 Process 객체로 두 번 불러야 값이 나오므로 객체를 붙잡아 둔다."""

    def __init__(self) -> None:
        self._procs: dict[int, psutil.Process] = {}
        self._ncpu = psutil.cpu_count() or 1

    def top(self, n: int = 8, gpu_use: dict[int, float] | None = None) -> list[Proc]:
        alive = set()
        rows = []
        visible = visible_window_pids()
        for p in psutil.process_iter(["name"]):
            if p.pid == 0:  # System Idle Process
                continue
            alive.add(p.pid)
            q = self._procs.setdefault(p.pid, p)
            try:
                cpu = q.cpu_percent(None) / self._ncpu
            except psutil.Error:
                continue
            gpu = (gpu_use or {}).get(p.pid, 0.0)
            if cpu > 0 or gpu > 0:
                rows.append(Proc(p.info["name"] or "?", p.pid, cpu, gpu, p.pid not in visible))
        for pid in set(self._procs) - alive:
            del self._procs[pid]
        rows.sort(key=lambda r: max(r.cpu, r.gpu), reverse=True)
        return rows[:n]


class PerfLimit:
    """\\Processor Information(_Total)\\% Performance Limit."""

    def __init__(self) -> None:
        dotnet.load()
        from System.Diagnostics import PerformanceCounter
        self._pc = PerformanceCounter("Processor Information", "% Performance Limit", "_Total")
        self._pc.NextValue()

    def read(self) -> float:
        return float(self._pc.NextValue())


class Power:
    SCHEME_EVERY = 30.0  # 전원 계획은 잘 안 바뀌므로 30초마다만

    def __init__(self) -> None:
        self._scheme: str | None = None
        self._scheme_at = 0.0
        self._wmi = None
        try:
            dotnet.load()
            from System.Management import ManagementObjectSearcher
            self._wmi = ManagementObjectSearcher(r"root\wmi", "SELECT PowerOnline, Charging FROM BatteryStatus")
        except Exception:
            pass

    def _read_scheme(self) -> str | None:
        try:
            r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, timeout=5,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return None
        text = r.stdout.decode("mbcs", "replace")
        return text[text.find("(") + 1:text.rfind(")")].strip() or None if "(" in text else None

    def read(self) -> PowerReading:
        now = time.monotonic()
        if now - self._scheme_at > self.SCHEME_EVERY:
            self._scheme, self._scheme_at = self._read_scheme(), now
        ac = charging = None
        if self._wmi is not None:
            try:
                for o in self._wmi.Get():
                    ac, charging = bool(o["PowerOnline"]), bool(o["Charging"])
            except Exception:
                pass
        if ac is None:
            b = psutil.sensors_battery()
            ac = None if b is None else b.power_plugged
        return PowerReading(ac=ac, charging=charging, scheme=self._scheme)
