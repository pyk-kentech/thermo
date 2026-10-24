"""인텔 CPU 레지스터(MSR) 읽기. LHM 의 PawnIO IntelMSR 모듈을 쓴다 — 쓰기 함수가 없는 읽기 전용 모듈.

레지스터 뜻은 Intel SDM 4권. 이 노트북에서 읽은 값은 DESIGN 2장.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import dotnet

PLATFORM_INFO = 0xCE
PACKAGE_THERM_STATUS = 0x1B1
TEMPERATURE_TARGET = 0x1A2
RAPL_POWER_UNIT = 0x606
PKG_POWER_LIMIT = 0x610
PKG_ENERGY_STATUS = 0x611
PKG_POWER_INFO = 0x614
CORE_PERF_LIMIT_REASONS = 0x64F

# 0x64F 상태 비트 (로그 비트는 +16)
LIMIT_BITS = {
    "prochot": 0,
    "thermal": 1,
    "residency": 4,
    "ratl": 5,          # running average thermal limit
    "vr_thermal": 6,
    "vr_tdc": 7,        # 전압 조정기 전류 한도
    "other": 8,
    "pl1": 10,
    "pl2": 11,
    "max_turbo": 12,
    "turbo_atten": 13,
}
# 0x1B1 비트
PKG_THERMAL_STATUS = 1 << 0     # 지금 TCC 활성(= TjMax - 오프셋 에 닿음)
PKG_PROCHOT = 1 << 2
PKG_POWER_LIMIT_STATUS = 1 << 10


def bits(v: int, hi: int, lo: int) -> int:
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


@dataclass
class CpuLimits:
    tjmax: int                  # 0x1A2[23:16]
    tcc_offset: int             # 0x1A2[29:24]
    pl1_w: float | None
    pl2_w: float | None
    tdp_w: float | None

    @property
    def throttle_temp(self) -> int:
        """CPU 가 스스로 클럭을 낮추기 시작하는 온도."""
        return self.tjmax - self.tcc_offset


@dataclass
class FastState:
    """10Hz 로 읽는 순간 상태."""

    pkg_thermal: bool           # 0x1B1 bit0
    pkg_prochot: bool           # 0x1B1 bit2
    tj_distance: int | None     # 0x1B1[22:16]
    reasons: int | None         # 0x64F 하위 16비트 (None = 못 읽음)


class Msr:
    def __init__(self) -> None:
        dotnet.load()
        import System
        from LibreHardwareMonitor.PawnIo import IntelMsr
        self._u32, self._u64 = System.UInt32, System.UInt64
        self._m = IntelMsr()
        unit = self.read(RAPL_POWER_UNIT)
        if unit is None:
            raise RuntimeError("MSR 을 읽지 못했습니다 (PawnIO·관리자 권한 확인)")
        self.power_unit = 1 / (1 << bits(unit, 3, 0))
        self.energy_unit = 1 / (1 << bits(unit, 12, 8))
        self._last_energy: tuple[int, float] | None = None

    def read(self, idx: int) -> int | None:
        ok, val = self._m.ReadMsr(self._u32(idx), self._u64(0))
        return int(val) if ok else None

    def limits(self) -> CpuLimits:
        tt = self.read(TEMPERATURE_TARGET) or 0
        pl = self.read(PKG_POWER_LIMIT)
        info = self.read(PKG_POWER_INFO)
        return CpuLimits(
            tjmax=bits(tt, 23, 16), tcc_offset=bits(tt, 29, 24),
            pl1_w=None if pl is None else bits(pl, 14, 0) * self.power_unit,
            pl2_w=None if pl is None else bits(pl, 46, 32) * self.power_unit,
            tdp_w=None if info is None else bits(info, 14, 0) * self.power_unit,
        )

    def fast(self) -> FastState:
        pkg = self.read(PACKAGE_THERM_STATUS) or 0
        lim = self.read(CORE_PERF_LIMIT_REASONS)
        return FastState(
            pkg_thermal=bool(pkg & PKG_THERMAL_STATUS),
            pkg_prochot=bool(pkg & PKG_PROCHOT),
            tj_distance=bits(pkg, 22, 16) if pkg else None,
            reasons=None if lim is None else lim & 0xFFFF,
        )

    def package_power(self, now: float) -> float | None:
        """지난 호출 이후 평균 패키지 전력 W (RAPL 에너지 카운터 차, 32비트 넘침 처리)."""
        e = self.read(PKG_ENERGY_STATUS)
        if e is None:
            return None
        e &= 0xFFFFFFFF
        prev, self._last_energy = self._last_energy, (e, now)
        if prev is None or now <= prev[1]:
            return None
        return ((e - prev[0]) & 0xFFFFFFFF) * self.energy_unit / (now - prev[1])

    def close(self) -> None:
        self._m.Close()


def limit_names(reasons: int) -> list[str]:
    return [n for n, b in LIMIT_BITS.items() if reasons >> b & 1]
