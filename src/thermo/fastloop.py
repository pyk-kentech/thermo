"""MSR 10Hz 고리 (DESIGN 2판 5장). 1초 사이의 순간 스로틀·온도 꼭짓점을 놓치지 않으려고 따로 돈다.

MSR 접근은 모두 이 스레드에서 한다(패키지 전력, 1초마다 코어별 온도 포함).
최적화(2026-10-24): CPU 온도도 여기서 직접 읽어 LHM 을 쓰지 않는다 — LHM 의 CPU 갱신은 매초 약 150ms 걸렸다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .sensors.msr import LIMIT_BITS, CpuLimits, FastState


@dataclass
class FastSummary:
    n: int = 0                       # 그 사이 읽은 횟수
    thermal_frac: float | None = None
    prochot_frac: float | None = None
    limit_frac: dict[str, float] = field(default_factory=dict)
    peak_temp: float | None = None
    power: float | None = None       # RAPL 평균 W
    pkg_temp: float | None = None    # 그 사이 패키지 온도 중앙값 (0x1B1)
    core_temps: list[float] = field(default_factory=list)   # 물리 코어별 (0x19C, 1초마다)


class LimitAccumulator:
    """FastState 를 모아 비율로 줄인다. 순수 로직(테스트 대상)."""

    def __init__(self, tjmax: int) -> None:
        self.tjmax = tjmax
        self._reset()

    def _reset(self) -> None:
        self.n = 0
        self.thermal = self.prochot = 0
        self.reason_n = 0
        self.reasons = dict.fromkeys(LIMIT_BITS, 0)
        self.min_dist: int | None = None
        self.dists: list[int] = []

    def add(self, st: FastState) -> None:
        self.n += 1
        self.thermal += st.pkg_thermal
        self.prochot += st.pkg_prochot
        if st.tj_distance is not None:
            self.dists.append(st.tj_distance)
            self.min_dist = st.tj_distance if self.min_dist is None else min(self.min_dist, st.tj_distance)
        if st.reasons is not None:
            self.reason_n += 1
            for name, bit in LIMIT_BITS.items():
                self.reasons[name] += st.reasons >> bit & 1

    def take(self, power: float | None = None, core_temps: list[float] | None = None) -> FastSummary:
        if self.n == 0:
            return FastSummary(power=power, core_temps=core_temps or [])
        d = sorted(self.dists)
        out = FastSummary(
            n=self.n,
            thermal_frac=self.thermal / self.n,
            prochot_frac=self.prochot / self.n,
            limit_frac={k: v / self.reason_n for k, v in self.reasons.items()} if self.reason_n else {},
            peak_temp=None if self.min_dist is None else float(self.tjmax - self.min_dist),
            power=power,
            pkg_temp=float(self.tjmax - d[len(d) // 2]) if d else None,
            core_temps=core_temps or [],
        )
        self._reset()
        return out


class FastLoop(threading.Thread):
    HZ = 10

    def __init__(self, msr) -> None:
        super().__init__(daemon=True, name="thermo-msr")
        self.msr = msr
        self.limits: CpuLimits = msr.limits()
        self.acc = LimitAccumulator(self.limits.tjmax)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error: str | None = None
        self._power: float | None = None
        self._cores: list[float] = []

    def run(self) -> None:
        tick = 0
        try:
            self.msr.package_power(time.monotonic())
            while not self.stop.is_set():
                t0 = time.monotonic()
                st = self.msr.fast()
                tick += 1
                with self.lock:
                    self.acc.add(st)
                    if tick % self.HZ == 0:
                        self._power = self.msr.package_power(time.monotonic())
                        self._cores = self.msr.core_temps(self.limits.tjmax)
                    if tick % (self.HZ * 30) == 0:
                        self.limits = self.msr.limits()
                self.stop.wait(max(0.0, 1 / self.HZ - (time.monotonic() - t0)))
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            try:
                self.msr.close()
            except Exception:
                pass

    def take(self) -> FastSummary:
        with self.lock:
            return self.acc.take(self._power, self._cores)
