"""판정(DESIGN 6장)과 원인 가르기(7장). 센서에 의존하지 않는 순수 함수.

문턱값은 첫 추정이다(DESIGN 11장). 바꾸면 DESIGN.md 도 함께 고친다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model import (
    GPU_HW_SLOWDOWN, GPU_SW_POWER_CAP, GPU_THERMAL_MASK,
    Cause, Finding, Level, Proc, Verdict, WindowStats,
)

# 6.1 절대 한계
CPU_WARN_TEMP = 90
CPU_BAD_TEMP = 97
CPU_BAD_TJ_DISTANCE = 3
GPU_TARGET_TEMP = 87          # 드라이버 여유값이 없을 때만 씀 (DESIGN 2장)
GPU_WARN_MARGIN = 10
GPU_BAD_MARGIN = 3
# 6.2 부하 대비 온도
CPU_IDLE_LOAD = 15
CPU_IDLE_WARN_TEMP = 75
CPU_IDLE_BAD_TEMP = 85
GPU_IDLE_LOAD = 10
GPU_IDLE_WARN_TEMP = 70
# 6.3 평소값
BASELINE_MIN_SAMPLES = 30
BASELINE_WARN_OVER = 8
BASELINE_BAD_OVER = 15
# 7장 원인
HIGH_LOAD = 60
LOW_LOAD = 30
BUSY_PROC = 5                 # 프로세스 하나가 이만큼(%) 쓰면 '눈에 띔'
FAN_NEAR_MAX = 0.9
FAN_SLOW_RATIO = 0.5
FAN_SLOW_RPM = 2500           # 최고 RPM 을 아직 모를 때
PERF_LIMITED = 95             # % Performance Limit 이 이보다 낮으면 클럭 제한 중
CPU_THERMAL_TJ_DISTANCE = 5
POWER_LIMIT_TJ_DISTANCE = 10

KNOWN_BACKGROUND = {
    "msmpeng.exe": "Windows Defender 검사",
    "searchindexer.exe": "Windows 검색 색인",
    "tiworker.exe": "Windows 업데이트 설치",
    "trustedinstaller.exe": "Windows 업데이트 설치",
    "musnotification.exe": "Windows 업데이트",
    "compattelrunner.exe": "Windows 호환성 원격 분석",
    "onedrive.exe": "OneDrive 동기화",
    "svchost.exe": "Windows 서비스",
}


@dataclass
class BaselineRef:
    p95: float
    n: int


# (장치, 창 값) → 그 상황의 평소값. M3 에서 baseline.py 가 채운다.
BaselineLookup = Callable[[str, WindowStats], "BaselineRef | None"]


def _f(v: float | None, unit: str = "", nd: int = 0) -> str:
    return "?" if v is None else f"{v:.{nd}f}{unit}"


def assess(stats: WindowStats, baseline: BaselineLookup | None = None) -> list[Finding]:
    out: list[Finding] = []
    c, g = stats.cpu, stats.gpu

    # 6.1 절대 한계 — CPU
    if c.temp is not None:
        if c.temp >= CPU_BAD_TEMP:
            out.append(Finding("CPU", Level.BAD, "absolute", f"CPU 온도 중앙값 {_f(c.temp, '°C')} ≥ {CPU_BAD_TEMP}°C"))
        elif c.temp >= CPU_WARN_TEMP:
            out.append(Finding("CPU", Level.WARN, "absolute", f"CPU 온도 중앙값 {_f(c.temp, '°C')} ≥ {CPU_WARN_TEMP}°C"))
    if (c.tj_distance is not None and c.tj_distance <= CPU_BAD_TJ_DISTANCE
            and c.perf_limit is not None and c.perf_limit < 100):
        out.append(Finding("CPU", Level.BAD, "absolute",
                           f"TjMax 까지 {_f(c.tj_distance, '°C')} 남은 채 성능 제한 {_f(c.perf_limit, '%')}"))

    # 6.1 절대 한계 — GPU (드라이버 여유값 우선)
    margin = g.margin
    if margin is None and g.temp is not None:
        margin = GPU_TARGET_TEMP - g.temp
    if g.reasons & GPU_THERMAL_MASK:
        out.append(Finding("GPU", Level.BAD, "absolute", "GPU 열 때문에 클럭을 낮추는 중(Thermal Slowdown)"))
    if margin is not None:
        if margin <= GPU_BAD_MARGIN:
            out.append(Finding("GPU", Level.BAD, "absolute", f"GPU 한계까지 {_f(margin, '°C')} ≤ {GPU_BAD_MARGIN}°C"))
        elif margin <= GPU_WARN_MARGIN:
            out.append(Finding("GPU", Level.WARN, "absolute", f"GPU 한계까지 {_f(margin, '°C')} ≤ {GPU_WARN_MARGIN}°C"))

    # 6.2 부하 대비 온도
    if c.temp is not None and c.load is not None and c.load < CPU_IDLE_LOAD:
        if c.temp >= CPU_IDLE_BAD_TEMP:
            out.append(Finding("CPU", Level.BAD, "load",
                               f"CPU 부하 {_f(c.load, '%')} < {CPU_IDLE_LOAD}% 인데 {_f(c.temp, '°C')} ≥ {CPU_IDLE_BAD_TEMP}°C"))
        elif c.temp >= CPU_IDLE_WARN_TEMP:
            out.append(Finding("CPU", Level.WARN, "load",
                               f"CPU 부하 {_f(c.load, '%')} < {CPU_IDLE_LOAD}% 인데 {_f(c.temp, '°C')} ≥ {CPU_IDLE_WARN_TEMP}°C"))
    cpu_busy = c.load is not None and c.load >= HIGH_LOAD
    if (not cpu_busy and g.temp is not None and g.load is not None
            and g.load < GPU_IDLE_LOAD and g.temp >= GPU_IDLE_WARN_TEMP):
        out.append(Finding("GPU", Level.WARN, "load",
                           f"GPU 부하 {_f(g.load, '%')} < {GPU_IDLE_LOAD}% 인데 {_f(g.temp, '°C')} ≥ {GPU_IDLE_WARN_TEMP}°C"))

    # 6.3 평소값
    if baseline is not None:
        for dev, temp in (("CPU", c.temp), ("GPU", g.temp)):
            ref = baseline(dev, stats) if temp is not None else None
            if ref is None or ref.n < BASELINE_MIN_SAMPLES:
                continue
            over = temp - ref.p95
            lvl = Level.BAD if over > BASELINE_BAD_OVER else Level.WARN if over > BASELINE_WARN_OVER else None
            if lvl is not None:
                out.append(Finding(dev, lvl, "baseline",
                                   f"{dev} {_f(temp, '°C')} 가 평소(95%값 {_f(ref.p95, '°C')})보다 {over:.0f}°C 높음"))
    return out


def _top(procs: list[Proc], key: str) -> Proc | None:
    best = max(procs, key=lambda p: getattr(p, key), default=None)
    return best if best is not None and getattr(best, key) >= BUSY_PROC else None


def _fan(stats: WindowStats, dev: str) -> float | None:
    return stats.fans.cpu_rpm if dev == "CPU" else stats.fans.gpu_rpm


def find_causes(stats: WindowStats, findings: list[Finding],
                fan_max: dict[str, float] | None = None) -> list[Cause]:
    """판정이 주의 이상인 장치마다 7장 규칙을 위에서부터 본다. 최대 두 개."""
    c, g = stats.cpu, stats.gpu
    hot = list(dict.fromkeys(f.device for f in findings if f.level >= Level.WARN))
    causes: list[Cause] = []

    for dev in hot:
        load = c.load if dev == "CPU" else g.load
        other = g.load if dev == "CPU" else c.load
        fan = _fan(stats, dev)
        fmax = (fan_max or {}).get(dev)

        # 1. 고부하 → 범인 프로세스
        if (load or 0) >= HIGH_LOAD or (other or 0) >= HIGH_LOAD:
            busy_dev = dev if (load or 0) >= HIGH_LOAD else ("GPU" if dev == "CPU" else "CPU")
            busy_load = load if busy_dev == dev else other
            use_key = "cpu" if busy_dev == "CPU" else "gpu"
            p = _top(stats.procs, use_key)
            use = getattr(p, use_key) if p else None
            ev = [f"{busy_dev} 부하 {_f(busy_load, '%')} ≥ {HIGH_LOAD}%"]
            if p:
                ev.append(f"상위 프로세스: {p.name} {busy_dev} {_f(use, '%')}")
            causes.append(Cause("high_load", dev, {
                "busy": busy_dev, "load": busy_load, "proc": p.name if p else None, "proc_use": use,
            }, ev))
            continue  # 부하로 설명되면 같은 장치의 다른 원인은 보지 않는다

        # 2. 저부하 + 백그라운드 작업
        if load is not None and load < LOW_LOAD:
            p = _top([p for p in stats.procs if p.background], "cpu")
            if p is not None:
                causes.append(Cause("background", dev, {
                    "proc": p.name, "what": KNOWN_BACKGROUND.get(p.name.lower()), "use": p.cpu,
                }, [f"창 없는 프로세스 {p.name} 가 CPU {_f(p.cpu, '%')} 사용"]))

        # 5·6. 클럭 하락
        throttle = _throttle(stats, dev)
        if throttle is not None:
            causes.append(throttle)

        # 3·4. 팬
        if fan is not None:
            if fmax and fan >= FAN_NEAR_MAX * fmax:
                causes.append(Cause("fan_max", dev, {"rpm": fan},
                                    [f"{dev} 팬 {fan:.0f}rpm ≥ 최고 {fmax:.0f}rpm 의 {FAN_NEAR_MAX:.0%}"]))
            elif fan < (FAN_SLOW_RATIO * fmax if fmax else FAN_SLOW_RPM):
                causes.append(Cause("fan_slow", dev, {"rpm": fan, "mode": stats.power.perf_mode},
                                    [f"{dev} 팬 {fan:.0f}rpm 으로 느림"]))

    # 같은 원인이 두 장치에 걸리면 하나로
    seen: set[tuple] = set()
    uniq = []
    for k in causes:
        sig = (k.key, k.data.get("proc"), k.data.get("busy"))
        if sig not in seen:
            seen.add(sig)
            uniq.append(k)
    if not uniq and hot:
        uniq.append(Cause("unknown", hot[0]))
    return uniq[:2]


def _throttle(stats: WindowStats, dev: str) -> Cause | None:
    c, g = stats.cpu, stats.gpu
    if dev == "CPU":
        if c.perf_limit is None or c.perf_limit >= PERF_LIMITED:
            return None
        ev = [f"CPU 성능 제한 {_f(c.perf_limit, '%')} < {PERF_LIMITED}%"]
        if c.tj_distance is not None and c.tj_distance <= CPU_THERMAL_TJ_DISTANCE:
            return Cause("thermal_throttle", dev, {}, ev + [f"TjMax 까지 {_f(c.tj_distance, '°C')}"])
        if c.tj_distance is None or c.tj_distance >= POWER_LIMIT_TJ_DISTANCE:
            return Cause("power_limit", dev, {"power": c.power},
                         ev + [f"TjMax 까지 {_f(c.tj_distance, '°C')} 여유", f"패키지 전력 {_f(c.power, 'W')}"])
        return None
    if g.reasons & GPU_THERMAL_MASK or (g.reasons & GPU_HW_SLOWDOWN and (g.margin or 99) <= GPU_BAD_MARGIN):
        return Cause("thermal_throttle", dev, {}, [f"GPU 클럭 이유 비트 {g.reasons:#x} (열)"])
    if g.reasons & GPU_SW_POWER_CAP and (g.load or 0) >= HIGH_LOAD and (g.margin or 99) > GPU_WARN_MARGIN:
        return Cause("power_limit", dev, {"power": g.power, "limit": g.power_limit},
                     [f"GPU 'SW Power Cap', 전력 {_f(g.power, 'W')} / 한도 {_f(g.power_limit, 'W')}"])
    return None


def extras(stats: WindowStats) -> list[Cause]:
    """보조 문장 (7장 7·8)."""
    out = []
    p = stats.power
    if p.ac is False:
        out.append(Cause("battery", "", {}, ["어댑터 연결 안 됨"]))
    if p.perf_mode:
        out.append(Cause("perf_mode", "", {"mode": p.perf_mode}, [f"성능 모드 {p.perf_mode}"]))
    elif p.scheme and any(w in p.scheme for w in ("고성능", "최고 성능", "High performance", "Ultimate")):
        out.append(Cause("scheme", "", {"scheme": p.scheme}, [f"전원 계획 {p.scheme}"]))
    return out


def diagnose(stats: WindowStats, baseline: BaselineLookup | None = None,
             fan_max: dict[str, float] | None = None) -> Verdict:
    findings = assess(stats, baseline)
    level = max((f.level for f in findings), default=Level.OK)
    causes = find_causes(stats, findings, fan_max) if level >= Level.WARN else []
    return Verdict(level, findings, causes, extras(stats), stats)
