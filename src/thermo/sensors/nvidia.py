"""GPU (NVML)."""

from __future__ import annotations

import pynvml as nv

from ..model import GpuReading

# NVML 제한 이유별 누적 시간 (nvmlDeviceGetViolationStatus). nvidia-smi 의 'Clocks Event Reasons Counters' 와 같은 값.
POLICIES = {
    "power": nv.NVML_PERF_POLICY_POWER,
    "thermal": nv.NVML_PERF_POLICY_THERMAL,
    "board": nv.NVML_PERF_POLICY_BOARD_LIMIT,
    "reliability": nv.NVML_PERF_POLICY_RELIABILITY,
}


def violation_frac(prev: tuple[int, int], cur: tuple[int, int]) -> float | None:
    """(referenceTime µs, violationTime ns) 두 개로 그 사이 제한된 시간 비율."""
    dt_ns = (cur[0] - prev[0]) * 1000
    if dt_ns <= 0:
        return None
    return max(0.0, min(1.0, (cur[1] - prev[1]) / dt_ns))


class NvGpu:
    def __init__(self) -> None:
        nv.nvmlInit()
        self._h = nv.nvmlDeviceGetHandleByIndex(0)
        self._last_ts = 0
        self._clock_max = self._try(lambda: nv.nvmlDeviceGetMaxClockInfo(self._h, nv.NVML_CLOCK_SM))
        self._viol: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _try(f):
        try:
            return f()
        except nv.NVMLError:
            return None

    def read(self) -> GpuReading:
        h, t = self._h, self._try
        util = t(lambda: nv.nvmlDeviceGetUtilizationRates(h))
        power = t(lambda: nv.nvmlDeviceGetPowerUsage(h))
        limit = t(lambda: nv.nvmlDeviceGetEnforcedPowerLimit(h))
        fracs: dict[str, float] = {}
        for name, pol in POLICIES.items():
            v = t(lambda pol=pol: nv.nvmlDeviceGetViolationStatus(h, pol))
            if v is None:
                continue
            cur = (int(v.referenceTime), int(v.violationTime))
            prev = self._viol.get(name)
            self._viol[name] = cur
            if prev is not None:
                f = violation_frac(prev, cur)
                if f is not None:
                    fracs[name] = f
        return GpuReading(
            temp=t(lambda: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
            margin=t(lambda: nv.nvmlDeviceGetMarginTemperature(h)),
            load=None if util is None else util.gpu,
            power=None if power is None else power / 1000,
            power_limit=None if limit is None else limit / 1000,
            clock=t(lambda: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM)),
            clock_max=self._clock_max,
            reasons=t(lambda: nv.nvmlDeviceGetCurrentClocksEventReasons(h)) or 0,
            limit_frac=fracs,
        )

    def process_use(self) -> dict[int, float]:
        """pid → SM 사용률 %. 지난 호출 이후의 표본만 본다."""
        samples = self._try(lambda: nv.nvmlDeviceGetProcessUtilization(self._h, self._last_ts)) or []
        out: dict[int, float] = {}
        for s in samples:
            out[s.pid] = max(out.get(s.pid, 0), s.smUtil)
            self._last_ts = max(self._last_ts, s.timeStamp)
        return out

    def close(self) -> None:
        nv.nvmlShutdown()
