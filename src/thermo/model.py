"""측정값과 판정 결과를 담는 자료형. 센서·화면과 무관한 순수 데이터만 둔다."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")


class Level(IntEnum):
    OK = 0
    WARN = 1
    BAD = 2

    @property
    def label(self) -> str:
        return ("정상", "주의", "이상")[self]


# NVML clocks event reasons (nvml.h). 이름은 nvidia-smi 표기를 따른다.
GPU_IDLE = 0x1
GPU_SW_POWER_CAP = 0x4
GPU_HW_SLOWDOWN = 0x8
GPU_SW_THERMAL = 0x20
GPU_HW_THERMAL = 0x40
GPU_HW_POWER_BRAKE = 0x80
GPU_THERMAL_MASK = GPU_SW_THERMAL | GPU_HW_THERMAL


@dataclass
class CpuReading:
    temp: float | None = None          # 패키지 온도 (LHM, 없으면 ASUS EC)
    temp_max: float | None = None      # 가장 뜨거운 코어
    temp_avg: float | None = None      # 코어 평균 (LHM 'Core Average'). 냉각 비교용 — 한 코어에 몰린 부하에 덜 흔들린다
    tj_distance: float | None = None   # TjMax 까지 가장 작은 거리
    load: float | None = None          # 전체 사용률 %
    power: float | None = None         # 패키지 전력 W
    clock: float | None = None         # 가장 빠른 코어 MHz
    perf_limit: float | None = None    # % Performance Limit (100 = 제한 없음)
    source: str = ""                   # "lhm" | "asus" | ""
    # MSR 10Hz 고리에서 (DESIGN 2판 5장). 1초(또는 창) 중 그 상태였던 비율 0–1
    peak_temp: float | None = None     # 그 사이 가장 높았던 패키지 온도 (TjMax - 최소 거리)
    thermal_frac: float | None = None  # 0x1B1 열 상태(95°C 도달) 비율
    prochot_frac: float | None = None  # 0x1B1 PROCHOT 비율
    limit_frac: dict[str, float] = field(default_factory=dict)   # 0x64F 이유별 비율


@dataclass
class GpuReading:
    temp: float | None = None
    temp_max: float | None = None
    margin: float | None = None        # 드라이버가 알려 주는 한계까지 남은 온도
    load: float | None = None
    power: float | None = None
    power_limit: float | None = None
    clock: float | None = None
    clock_max: float | None = None
    reasons: int = 0                   # clocks event reasons 비트
    limit_frac: dict[str, float] = field(default_factory=dict)   # NVML 제한 이유별 누적 시간 비율 0–1


@dataclass
class FanReading:
    cpu_rpm: float | None = None
    gpu_rpm: float | None = None


@dataclass
class PowerReading:
    ac: bool | None = None             # 어댑터 연결
    charging: bool | None = None
    scheme: str | None = None          # 전원 계획 이름
    perf_mode: str | None = None       # Armoury Crate 모드 (아직 못 읽음, DESIGN 3장)


@dataclass
class Proc:
    name: str
    pid: int
    cpu: float = 0.0                   # 전체 CPU 대비 %
    gpu: float = 0.0                   # GPU SM 사용률 %
    background: bool = False           # 보이는 창이 없는 프로세스


@dataclass
class Snapshot:
    t: float
    cpu: CpuReading = field(default_factory=CpuReading)
    gpu: GpuReading = field(default_factory=GpuReading)
    fans: FanReading = field(default_factory=FanReading)
    power: PowerReading = field(default_factory=PowerReading)
    procs: list[Proc] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # 읽지 못한 센서 안내


@dataclass
class WindowStats:
    """최근 몇 초의 Snapshot 을 판정용으로 줄인 값 (DESIGN 5장)."""

    n: int
    cpu: CpuReading
    gpu: GpuReading
    fans: FanReading
    power: PowerReading
    procs: list[Proc]
    notes: list[str]


def _vals(items: Iterable[T], get: Callable[[T], float | None]) -> list[float]:
    return [v for v in map(get, items) if v is not None]


def _median(items, get):
    v = _vals(items, get)
    return statistics.median(v) if v else None


def _mean(items, get):
    v = _vals(items, get)
    return statistics.fmean(v) if v else None


def _max(items, get):
    v = _vals(items, get)
    return max(v) if v else None


def _min(items, get):
    v = _vals(items, get)
    return min(v) if v else None


def _mean_dict(ds: list[dict[str, float]]) -> dict[str, float]:
    """키별 평균. 값이 없는 표본은 그 키에서 빠진다."""
    keys = dict.fromkeys(k for d in ds for k in d)
    return {k: statistics.fmean([d[k] for d in ds if k in d]) for k in keys}


def summarize(snaps: Sequence[Snapshot]) -> WindowStats:
    """온도는 중앙값(튐 무시)과 최댓값, 부하·전력·클럭은 평균, 여유 온도는 최솟값."""
    if not snaps:
        raise ValueError("빈 창")
    c = [s.cpu for s in snaps]
    g = [s.gpu for s in snaps]
    cpu = CpuReading(
        temp=_median(c, lambda r: r.temp),
        temp_max=_max(c, lambda r: r.temp_max if r.temp_max is not None else r.temp),
        temp_avg=_median(c, lambda r: r.temp_avg),
        tj_distance=_min(c, lambda r: r.tj_distance),
        load=_mean(c, lambda r: r.load),
        power=_mean(c, lambda r: r.power),
        clock=_mean(c, lambda r: r.clock),
        perf_limit=_mean(c, lambda r: r.perf_limit),
        source=snaps[-1].cpu.source,
        peak_temp=_max(c, lambda r: r.peak_temp),
        thermal_frac=_mean(c, lambda r: r.thermal_frac),
        prochot_frac=_mean(c, lambda r: r.prochot_frac),
        limit_frac=_mean_dict([r.limit_frac for r in c]),
    )
    # 이유 비트는 창의 절반 이상에서 켜진 것만 (한 번 깜빡인 것은 무시)
    reasons = 0
    for bit in range(16):
        if sum(1 for r in g if r.reasons >> bit & 1) * 2 >= len(g):
            reasons |= 1 << bit
    gpu = GpuReading(
        temp=_median(g, lambda r: r.temp),
        temp_max=_max(g, lambda r: r.temp_max if r.temp_max is not None else r.temp),
        margin=_median(g, lambda r: r.margin),
        load=_mean(g, lambda r: r.load),
        power=_mean(g, lambda r: r.power),
        power_limit=_mean(g, lambda r: r.power_limit),
        clock=_mean(g, lambda r: r.clock),
        clock_max=_max(g, lambda r: r.clock_max),
        reasons=reasons,
        limit_frac=_mean_dict([r.limit_frac for r in g]),
    )
    fans = FanReading(
        cpu_rpm=_mean(snaps, lambda s: s.fans.cpu_rpm),
        gpu_rpm=_mean(snaps, lambda s: s.fans.gpu_rpm),
    )
    # 프로세스: pid 별 평균 (창에 없던 순간은 0 으로 본다)
    acc: dict[int, Proc] = {}
    for s in snaps:
        for p in s.procs:
            a = acc.setdefault(p.pid, Proc(p.name, p.pid, background=p.background))
            a.cpu += p.cpu / len(snaps)
            a.gpu += p.gpu / len(snaps)
    procs = sorted(acc.values(), key=lambda p: max(p.cpu, p.gpu), reverse=True)
    notes = list(dict.fromkeys(n for s in snaps for n in s.notes))
    return WindowStats(len(snaps), cpu, gpu, fans, snaps[-1].power, procs, notes)
