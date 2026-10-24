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
    tj_distance: float | None = None   # TjMax 까지 가장 작은 거리
    load: float | None = None          # 전체 사용률 %
    power: float | None = None         # 패키지 전력 W
    clock: float | None = None         # 가장 빠른 코어 MHz
    perf_limit: float | None = None    # % Performance Limit (100 = 제한 없음)
    source: str = ""                   # "lhm" | "asus" | ""


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


def summarize(snaps: Sequence[Snapshot]) -> WindowStats:
    """온도는 중앙값(튐 무시)과 최댓값, 부하·전력·클럭은 평균, 여유 온도는 최솟값."""
    if not snaps:
        raise ValueError("빈 창")
    c = [s.cpu for s in snaps]
    g = [s.gpu for s in snaps]
    cpu = CpuReading(
        temp=_median(c, lambda r: r.temp),
        temp_max=_max(c, lambda r: r.temp_max if r.temp_max is not None else r.temp),
        tj_distance=_min(c, lambda r: r.tj_distance),
        load=_mean(c, lambda r: r.load),
        power=_mean(c, lambda r: r.power),
        clock=_mean(c, lambda r: r.clock),
        perf_limit=_mean(c, lambda r: r.perf_limit),
        source=snaps[-1].cpu.source,
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


@dataclass
class Finding:
    """6장 기준 하나가 걸린 것."""

    device: str          # "CPU" | "GPU"
    level: Level
    rule: str            # "absolute" | "load" | "baseline"
    evidence: str


@dataclass
class Cause:
    """7장 원인 하나. 문장은 text.py 가 key·data 로 만든다."""

    key: str
    device: str
    data: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    level: Level
    findings: list[Finding]
    causes: list[Cause]       # 최대 2개
    extras: list[Cause]       # 보조 문장 (배터리, 전원 계획)
    stats: WindowStats
