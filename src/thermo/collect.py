"""센서를 모아 1초마다 Snapshot 을 만들고 최근 10초를 창으로 둔다 (DESIGN 5장)."""

from __future__ import annotations

import time
from collections import deque

import psutil

from .model import FanReading, Snapshot, WindowStats, summarize
from .sensors import is_admin

WINDOW = 10


class Collector:
    def __init__(self, window: int = WINDOW) -> None:
        self.notes: list[str] = []
        self.admin = is_admin()
        if not self.admin:
            self.notes.append("관리자 권한이 없어 CPU 코어 온도·팬 속도를 읽지 못합니다.")
        self.lhm = self._open("lhm", "CPU 센서(LHM)를 열지 못했습니다")
        self.fast = self._open("msr", "CPU 기록(MSR)을 읽지 못해 스로틀 이유를 알 수 없습니다") if self.admin else None
        if self.fast is not None:
            self.fast.start()
        self.asus = self._open("asus", "ASUS 팬·온도를 읽지 못했습니다") if self.admin else None
        self.gpu = self._open("gpu", "NVIDIA GPU 를 읽지 못했습니다")
        self.perf = self._open("perf", "CPU 성능 제한 카운터를 읽지 못했습니다")
        self.power = self._open("power", "전원 상태를 읽지 못했습니다")
        from .sensors.system import Processes
        self.procs = Processes()
        self.window: deque[Snapshot] = deque(maxlen=window)
        # 판단에서 뺄 pid: thermo 자신과 기준 측정이 띄운 부하 프로세스. 사용자의 python 프로그램은 범인으로 보여야 하므로
        # 이름(python.exe)으로 빼지 않는다(M10).
        import os
        self.exclude: set[int] = {os.getpid()}
        self._pawnio_noted = False
        psutil.cpu_percent(None)

    def _open(self, kind: str, fail: str):
        try:
            if kind == "lhm":
                from .sensors.lhm import LhmCpu
                return LhmCpu()
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
                from .sensors.system import PerfLimit
                return PerfLimit()
            if kind == "power":
                from .sensors.system import Power
                return Power()
        except Exception as e:
            self.notes.append(f"{fail}: {type(e).__name__}")
        return None

    def sample(self) -> Snapshot:
        s = Snapshot(t=time.time(), notes=list(self.notes))
        if self.lhm is not None:
            s.cpu = self.lhm.read()
        if s.cpu.temp is None and self.asus is not None:
            s.cpu.temp = self.asus.cpu_temp()
            s.cpu.source = "asus" if s.cpu.temp is not None else ""
            if self.admin and not self._pawnio_noted:
                self.notes.append("CPU 코어별 온도·전력은 PawnIO 가 없어 읽지 못합니다 (ASUS 온도로 대신).")
                self._pawnio_noted = True
        if self.fast is not None:
            if self.fast.error and self.fast.error not in " ".join(self.notes):
                self.notes.append(f"CPU 기록(MSR) 읽기가 멈췄습니다: {self.fast.error}")
            fs = self.fast.take()
            if fs.power is not None:
                s.cpu.power = fs.power
            s.cpu.peak_temp = fs.peak_temp
            s.cpu.thermal_frac = fs.thermal_frac
            s.cpu.prochot_frac = fs.prochot_frac
            s.cpu.limit_frac = fs.limit_frac
        if s.cpu.load is None:
            s.cpu.load = psutil.cpu_percent(None)
        if self.perf is not None:
            s.cpu.perf_limit = self.perf.read()
        gpu_use: dict[int, float] = {}
        if self.gpu is not None:
            s.gpu = self.gpu.read()
            gpu_use = self.gpu.process_use()
        if self.asus is not None:
            s.fans = FanReading(*self.asus.fans())
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
        for x in (self.lhm, self.gpu):
            if x is not None:
                try:
                    x.close()
                except Exception:
                    pass
