"""기준 측정 데이터로 이 노트북의 기준을 만든다 (DESIGN 2판 6.2·7장). 순수 함수.

- 기대 온도 모델: 정상 상태에서 T = a + b·P_cpu + c·P_gpu (CPU·GPU 따로).
  화면 판정이 10초 창의 중앙값으로 하므로, 모델도 10초 묶음(온도 중앙값, 전력 평균)으로 맞춘다.
  95°C 열 스로틀 중인 묶음은 넣지 않는다 — 온도가 95°C 에 묶여 있어 전력과의 관계가 끊기기 때문.
- 팬 곡선: 정상 상태 단계마다 (온도, 팬 RPM).
- 실효 한도: CPU 부하 최대 단계의 전력과 그때 열 스로틀 여부, GPU 단계의 전력과 제한 이유.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field

BLOCK_SECONDS = 10
SIGMA_FLOOR = 1.5          # 센서가 1°C 단위라 이보다 작은 흩어짐은 믿지 않는다
THROTTLE_FRAC = 0.05       # 묶음 안에서 이보다 오래 스로틀했으면 모델에서 뺀다
MIN_SPREAD_W = 5.0         # 이 전력 범위보다 좁게만 재졌으면 그 항은 맞추지 않는다


def _med(vals):
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None


def _mean(vals):
    v = [x for x in vals if x is not None]
    return statistics.fmean(v) if v else None


def _limits(row: dict, key: str) -> dict:
    raw = row.get(key)
    return json.loads(raw) if raw else {}


@dataclass
class Block:
    """10초 묶음 하나."""

    stage: str
    t: float
    cpu_power: float | None
    gpu_power: float | None
    cpu_temp: float | None
    gpu_temp: float | None
    fan_cpu: float | None
    fan_gpu: float | None
    cpu_thermal: float           # 열 스로틀 비율 평균
    gpu_thermal: float


def blocks(stage: str, rows: list[dict], seconds: int = BLOCK_SECONDS) -> list[Block]:
    out = []
    for i in range(0, len(rows) - seconds + 1, seconds):
        part = rows[i:i + seconds]
        out.append(Block(
            stage=stage, t=part[0]["ts"],
            cpu_power=_mean(r["cpu_power"] for r in part),
            gpu_power=_mean(r["gpu_power"] for r in part),
            cpu_temp=_med(r["cpu_temp"] for r in part),
            gpu_temp=_med(r["gpu_temp"] for r in part),
            fan_cpu=_med(r["fan_cpu"] for r in part),
            fan_gpu=_med(r["fan_gpu"] for r in part),
            cpu_thermal=_mean(r["cpu_thermal_frac"] for r in part) or 0.0,
            gpu_thermal=_mean(_limits(r, "gpu_limits").get("thermal") for r in part) or 0.0,
        ))
    return out


@dataclass
class LinearModel:
    target: str                      # "cpu_temp" | "gpu_temp"
    intercept: float
    coef: dict[str, float]           # 입력 이름 → 계수 (°C/W)
    sigma: float                     # 잔차 표준편차 (하한 SIGMA_FLOOR)
    n: int                           # 맞춘 묶음 수
    stages: list[str]                # 맞추는 데 쓴 단계
    notes: list[str] = field(default_factory=list)
    ranges: dict[str, list[float]] = field(default_factory=dict)   # 입력별 잰 범위 [최소, 최대] W

    def predict(self, **x: float | None) -> float | None:
        total = self.intercept
        for k, c in self.coef.items():
            if x.get(k) is None:
                return None
            total += c * x[k]
        return total


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """가우스 소거 (작은 연립방정식). 특이하면 None."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-9:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(target: str, inputs: list[str], data: list[Block]) -> LinearModel | None:
    """최소제곱. 범위가 좁은 입력은 빼고 맞춘다(그 사실을 notes 에 남김)."""
    rows = [d for d in data if getattr(d, target) is not None and all(getattr(d, k) is not None for k in inputs)]
    notes = []
    use = []
    for k in inputs:
        vals = [getattr(d, k) for d in rows]
        if vals and max(vals) - min(vals) >= MIN_SPREAD_W:
            use.append(k)
        else:
            notes.append(f"{k} 범위가 {MIN_SPREAD_W:.0f}W 보다 좁아 모델에서 뺐습니다")
    k = len(use) + 1
    if len(rows) < k + 2:
        return None
    x = [[1.0] + [getattr(d, n) for n in use] for d in rows]
    y = [getattr(d, target) for d in rows]
    xtx = [[sum(r[i] * r[j] for r in x) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * yy for r, yy in zip(x, y)) for i in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return None
    resid = [yy - sum(bi * xi for bi, xi in zip(beta, r)) for r, yy in zip(x, y)]
    sigma = math.sqrt(sum(e * e for e in resid) / max(1, len(rows) - k))
    ranges = {k: [min(getattr(d, k) for d in rows), max(getattr(d, k) for d in rows)] for k in inputs}
    return LinearModel(target, beta[0], dict(zip(use, beta[1:])), max(sigma, SIGMA_FLOOR), len(rows),
                       sorted({d.stage for d in rows}), notes, ranges)


@dataclass
class StagePoint:
    stage: str
    steady: bool
    cpu_power: float | None
    gpu_power: float | None
    cpu_temp: float | None
    gpu_temp: float | None
    fan_cpu: float | None
    fan_gpu: float | None
    cpu_thermal: float
    gpu_thermal: float
    gpu_power_cap: float
    top: list[str] = field(default_factory=list)     # 그 구간 상위 프로세스 ("이름 CPU x% GPU y%")


def top_processes(rows: list[dict], n: int = 4) -> list[str]:
    """1초 기록의 상위 프로세스를 구간 평균으로. 기준 측정 자신(python)은 뺀다."""
    cpu: dict[str, float] = {}
    gpu: dict[str, float] = {}
    for r in rows:
        for name, _pid, c, g, _bg in json.loads(r.get("procs") or "[]"):
            if name.lower() in ("python.exe", "pythonw.exe"):
                continue
            cpu[name] = cpu.get(name, 0) + c / len(rows)
            gpu[name] = gpu.get(name, 0) + g / len(rows)
    names = sorted(cpu, key=lambda k: -(cpu[k] + gpu[k]))
    return [f"{k} CPU {cpu[k]:.1f}% GPU {gpu[k]:.0f}%" for k in names[:n] if cpu[k] + gpu[k] >= 1]


def stage_point(stage: str, rows: list[dict], steady: bool) -> StagePoint:
    return StagePoint(
        stage=stage, steady=steady,
        cpu_power=_med(r["cpu_power"] for r in rows), gpu_power=_med(r["gpu_power"] for r in rows),
        cpu_temp=_med(r["cpu_temp"] for r in rows), gpu_temp=_med(r["gpu_temp"] for r in rows),
        fan_cpu=_med(r["fan_cpu"] for r in rows), fan_gpu=_med(r["fan_gpu"] for r in rows),
        cpu_thermal=_mean(r["cpu_thermal_frac"] for r in rows) or 0.0,
        gpu_thermal=_mean(_limits(r, "gpu_limits").get("thermal") for r in rows) or 0.0,
        gpu_power_cap=_mean(_limits(r, "gpu_limits").get("power") for r in rows) or 0.0,
        top=top_processes(rows),
    )


@dataclass
class Reference:
    mode: str
    room: float | None
    stages: list[StagePoint]
    cpu_model: LinearModel | None
    gpu_model: LinearModel | None
    fan_curve: dict[str, list[tuple[float, float]]]     # "cpu"/"gpu" → [(온도, rpm)] 온도 순
    cpu_sustained_w: float | None                         # CPU 최대 부하 단계 전력
    cpu_thermal_limited: bool                             # 그때 95°C 스로틀이 걸렸나
    gpu_sustained_w: float | None
    gpu_power_capped: float                               # 그때 전력 제한 비율
    idle_cpu_temp: float | None
    idle_gpu_temp: float | None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> Reference:
        m = lambda x: None if x is None else LinearModel(**x)  # noqa: E731
        return Reference(**{**d, "stages": [StagePoint(**s) for s in d["stages"]],
                            "cpu_model": m(d["cpu_model"]), "gpu_model": m(d["gpu_model"]),
                            "fan_curve": {k: [tuple(p) for p in v] for k, v in d["fan_curve"].items()}})


def build(mode: str, room: float | None, stages: list[tuple[str, list[dict], bool]]) -> Reference:
    """stages: (단계 이름, 그 단계 '유지' 구간의 1초 표본들, 정상 상태였나)."""
    points = [stage_point(name, rows, steady) for name, rows, steady in stages if rows]
    steady_blocks = [b for name, rows, steady in stages if steady for b in blocks(name, rows)]
    notes = [f"'{p.stage}' 단계는 정상 상태에 닿지 못해 모델에서 뺐습니다" for p in points if not p.steady]

    cpu_data = [b for b in steady_blocks if b.cpu_thermal <= THROTTLE_FRAC]
    gpu_data = [b for b in steady_blocks if b.gpu_thermal <= THROTTLE_FRAC]
    dropped = len(steady_blocks) - len(cpu_data)
    if dropped:
        notes.append(f"CPU 가 95°C 에 묶인 10초 묶음 {dropped}개는 CPU 모델에서 뺐습니다")
    cpu_model = fit("cpu_temp", ["cpu_power", "gpu_power"], cpu_data)
    gpu_model = fit("gpu_temp", ["gpu_power", "cpu_power"], gpu_data)
    if cpu_model is None:
        notes.append("CPU 모델을 만들 만큼 자료가 없습니다")
    if gpu_model is None:
        notes.append("GPU 모델을 만들 만큼 자료가 없습니다")

    steady_pts = [p for p in points if p.steady]
    fan_curve = {
        "cpu": sorted((p.cpu_temp, p.fan_cpu) for p in steady_pts if p.cpu_temp is not None and p.fan_cpu is not None),
        "gpu": sorted((p.gpu_temp, p.fan_gpu) for p in steady_pts if p.gpu_temp is not None and p.fan_gpu is not None),
    }
    by = {p.stage.split("#")[0]: p for p in points}   # 합친 기록이면 마지막 측정 것이 남는다
    cpu_max = by.get("cpu16")
    gpu_st = by.get("gpu")
    idle = by.get("idle")
    return Reference(
        mode=mode, room=room, stages=points, cpu_model=cpu_model, gpu_model=gpu_model, fan_curve=fan_curve,
        cpu_sustained_w=cpu_max.cpu_power if cpu_max else None,
        cpu_thermal_limited=bool(cpu_max and cpu_max.cpu_thermal > THROTTLE_FRAC),
        gpu_sustained_w=gpu_st.gpu_power if gpu_st else None,
        gpu_power_capped=gpu_st.gpu_power_cap if gpu_st else 0.0,
        idle_cpu_temp=idle.cpu_temp if idle else None, idle_gpu_temp=idle.gpu_temp if idle else None,
        notes=notes + (cpu_model.notes if cpu_model else []) + (gpu_model.notes if gpu_model else []),
    )


def expected_fan(curve: list[tuple[float, float]], temp: float) -> float | None:
    """팬 곡선에서 그 온도의 기대 RPM (점 사이는 직선, 바깥은 끝값)."""
    if not curve:
        return None
    if temp <= curve[0][0]:
        return curve[0][1]
    if temp >= curve[-1][0]:
        return curve[-1][1]
    for (t0, r0), (t1, r1) in zip(curve, curve[1:]):
        if t0 <= temp <= t1:
            return r0 if t1 == t0 else r0 + (r1 - r0) * (temp - t0) / (t1 - t0)
    return None


STAGE_NAMES = {"idle": "공회전", "cpu1": "CPU 1스레드", "cpu2": "CPU 2스레드", "cpu3": "CPU 3스레드",
               "cpu4": "CPU 4스레드", "cpu8": "CPU 8스레드", "cpu16": "CPU 16스레드",
               "gpu": "게임(GPU)", "both": "게임 + CPU 16스레드", "cool": "식힘"}


def summary(ref: Reference) -> list[str]:
    def f(v, unit="", nd=0):
        return "–" if v is None else f"{v:.{nd}f}{unit}"
    out = [f"성능 모드: {ref.mode}" + (f", 방 온도 {ref.room:.0f}°C" if ref.room is not None else "")]
    out.append("단계별 (중앙값):")
    for p in ref.stages:
        name, _, run = p.stage.partition("#")
        out.append(f"  {STAGE_NAMES.get(name, name) + (f' #{run}' if run else ''):<16} CPU {f(p.cpu_power, 'W', 1)} {f(p.cpu_temp, '°C')}"
                   f" · GPU {f(p.gpu_power, 'W', 1)} {f(p.gpu_temp, '°C')} · 팬 {f(p.fan_cpu)}/{f(p.fan_gpu)}rpm"
                   f" · CPU 열 스로틀 {p.cpu_thermal:.0%}" + ("" if p.steady else " (정상 상태 아님)"))
        if p.top:
            out.append(f"  {'':16} 돌던 프로그램: {', '.join(p.top)}")
    for m in (ref.cpu_model, ref.gpu_model):
        if m:
            terms = " ".join(f"{c:+.2f}°C/W×{k}" for k, c in m.coef.items())
            out.append(f"{m.target} 모델: {m.intercept:.1f}°C {terms} (흩어짐 σ {m.sigma:.1f}°C, 10초 묶음 {m.n}개)")
    if ref.cpu_sustained_w is not None:
        out.append(f"CPU 최대 부하 지속 전력 {ref.cpu_sustained_w:.0f}W — "
                   + ("95°C 에 묶여 온도가 한계" if ref.cpu_thermal_limited else "온도 여유 있음(전력 한도가 한계)"))
    if ref.gpu_sustained_w is not None:
        out.append(f"게임 중 GPU 전력 {ref.gpu_sustained_w:.0f}W, 전력 제한 {ref.gpu_power_capped:.0%}")
    out += [f"※ {n}" for n in ref.notes]
    return out
