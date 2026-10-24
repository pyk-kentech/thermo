"""GPU (NVML)."""

from __future__ import annotations

import pynvml as nv

from ..model import GpuReading


class NvGpu:
    def __init__(self) -> None:
        nv.nvmlInit()
        self._h = nv.nvmlDeviceGetHandleByIndex(0)
        self._last_ts = 0
        self._clock_max = self._try(lambda: nv.nvmlDeviceGetMaxClockInfo(self._h, nv.NVML_CLOCK_SM))

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
        return GpuReading(
            temp=t(lambda: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
            margin=t(lambda: nv.nvmlDeviceGetMarginTemperature(h)),
            load=None if util is None else util.gpu,
            power=None if power is None else power / 1000,
            power_limit=None if limit is None else limit / 1000,
            clock=t(lambda: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM)),
            clock_max=self._clock_max,
            reasons=t(lambda: nv.nvmlDeviceGetCurrentClocksEventReasons(h)) or 0,
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
