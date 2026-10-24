"""센서를 모아 1초마다 Snapshot 을 만들고 최근 10초를 창으로 둔다 (DESIGN 5장).

최적화(2026-10-24, tools/overhead.py 로 잼): CPU 온도·전력은 MSR 고리(fastloop)에서 직접 읽고 LHM 은 쓰지 않는다.
ASUS WMI(팬·EC 온도, 호출 3번에 약 30ms)는 2초마다 읽는다 — 팬은 0→3700rpm 에 약 40초가 걸려 2초면 충분하다.
"""

from __future__ import annotations

import time
from collections import deque

import psutil

from .model import FanReading, Snapshot, WindowStats, summarize
from .sensors import is_admin

WINDOW = 10
ASUS_EVERY = 2


class Collector:
    def __init__(self, window: int = WINDOW) -> None:
        self.notes: list[str] = []
        self.admin = is_admin()
        if not self.admin:
            self.notes.append("관리자 권한이 없어 CPU 코어 온도·팬 속도를 읽지 못합니다.")
        self.lhm = None          # 쓰지 않음 (위 설명)
        self.fast = self._open("msr", "CPU 기록(MSR)을 읽지 못해 CPU 온도·전력·스로틀을 알 수 없습니다 "
                                      "(PawnIO 설치를 확인하세요)") if self.admin else None
        if self.fast is not None:
            self.fast.start()
        self.asus = self._open("asus", "ASUS 팬·온도를 읽지 못했습니다") if self.admin else None
        self.gpu = self._open("gpu", "NVIDIA GPU 를 읽지 못했습니다")
        self.perf = self._open("perf", "CPU 클럭 카운터를 읽지 못했습니다")
        self.power = self._open("power", "전원 상태를 읽지 못했습니다")
        from .sensors.system import Processes
        self.procs = Processes()
        self.window: deque[Snapshot] = deque(maxlen=window)
        # 판단에서 뺄 pid: thermo 자신과 기준 측정이 띄운 부하 프로세스. 사용자의 python 프로그램은 범인으로 보여야 하므로
        # 이름(python.exe)으로 빼지 않는다(M10).
        import os
        self.exclude: set[int] = {os.getpid()}
        self._ticks = 0
        self._fans = FanReading()
        self._asus_temp: float | None = None
        psutil.cpu_percent(None)

    def _open(self, kind: str, fail: str):
        try:
            if kind == "msr":
                from .fastloop import FastLoop
                from .sensors.msr import Msr
                return FastLoop(Msr())
            if kind == "asus":
                from .sensors.asus import AsusWmi
                return AsusWmi()
            if kind == "gpu":
                from .sensors.nvidia import NvGpu
                return NvGpu()
            if kind == "perf":
                from .sensors.system import CpuPerf
                return CpuPerf()
            if kind == "power":
                from .sensors.system import Power
                return Power()
        except Exception as e:
            self.notes.append(f"{fail}: {type(e).__name__}")
        return None

    def sample(self) -> Snapshot:
        s = Snapshot(t=time.time(), notes=list(self.notes))
        self._ticks += 1
        if self.asus is not None and (self._ticks % ASUS_EVERY == 1 or ASUS_EVERY == 1):
            self._fans = FanReading(*self.asus.fans())
            self._asus_temp = self.asus.cpu_temp() if self.fast is None else None
        c = s.cpu
        if self.fast is not None:
            if self.fast.error and self.fast.error not in " ".join(self.notes):
                self.notes.append(f"CPU 기록(MSR) 읽기가 멈췄습니다: {self.fast.error}")
            fs = self.fast.take()
            c.temp, c.peak_temp, c.power = fs.pkg_temp, fs.peak_temp, fs.power
            if fs.core_temps:
                c.temp_avg = sum(fs.core_temps) / len(fs.core_temps)
                c.temp_max = max(fs.core_temps)
            if fs.peak_temp is not None and self.fast.limits.tjmax:
                c.tj_distance = self.fast.limits.tjmax - fs.peak_temp
            c.thermal_frac, c.prochot_frac, c.limit_frac = fs.thermal_frac, fs.prochot_frac, fs.limit_frac
            c.source = "msr"
        elif self._asus_temp is not None:      # PawnIO 가 없으면 EC 의 CPU 온도 하나
            c.temp, c.source = self._asus_temp, "asus"
        c.load = psutil.cpu_percent(None)
        if self.perf is not None:
            c.perf_limit, c.clock = self.perf.read()
        gpu_use: dict[int, float] = {}
        if self.gpu is not None:
            s.gpu = self.gpu.read()
            gpu_use = self.gpu.process_use()
        s.fans = self._fans
        if self.power is not None:
            s.power = self.power.read()
        s.procs = self.procs.top(gpu_use=gpu_use, exclude=self.exclude)
        self.window.append(s)
        return s

    def stats(self) -> WindowStats:
        return summarize(list(self.window))

    def close(self) -> None:
        if self.fast is not None:
            self.fast.stop.set()
            self.fast.join(timeout=2)
        for x in (self.gpu,):
            if x is not None:
                try:
                    x.close()
                except Exception:
                    pass
