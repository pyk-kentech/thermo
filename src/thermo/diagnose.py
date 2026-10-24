"""판단 2판 (DESIGN 2판 6장): 하드웨어 사실 → 기준과 비교 → 원인.

순수 함수 `diagnose()` 와, 정상 상태·방 온도 보정을 위해 시간에 걸친 상태를 들고 있는 `Diagnoser` 로 나뉜다.
문턱값 근거는 이 노트북에서 잰 기준(reference)과 그 흩어짐(σ)이다. 고정 온도 문턱값은 하드웨어가 알려 준 값만 쓴다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .model import Level, Proc, WindowStats
from .reference import Reference, expected_fan, model_temp
from .store import Steady, steady_state

# 6.2 비교: 기준 측정의 흩어짐(σ)의 몇 배인가
SIGMA_WARN = 3.0
SIGMA_BAD = 6.0
# 팬: 같은 온도에서 기준 팬 곡선 대비. 공회전 팬은 0↔2500rpm 을 오가므로(M7) 기대값이 2000rpm 이상일 때만 본다
FAN_WARN = 0.85
FAN_BAD = 0.6
FAN_COMPARE_MIN = 2000
# 열 스로틀: 10초 중 이만큼이면 '스로틀 중'
THROTTLE_SIG = 0.2
THROTTLE_ANY = 0.05
# 스로틀이 걸린 전력이 기준 측정에서 처음 걸린 전력보다 이만큼 낮으면 냉각 저하
THROTTLE_POWER_WARN = 0.85
THROTTLE_POWER_BAD = 0.6
# 전력 한도(간접): CPU 를 이만큼 쓰는데 스로틀 없이 전력이 기준 지속 전력의 이 비율 아래에 묶임
HEAVY_CPU_LOAD = 80
POWER_LIMIT_RATIO = 0.8
# 외부 PROCHOT
PROCHOT_FRAC = 0.1
PROCHOT_MAX_TEMP = 90
# 모델 범위 밖으로 이만큼 넘으면 '범위 밖' 표시
RANGE_MARGIN = 0.1

# 창은 없지만 Windows 의 일부라 '백그라운드 작업'으로 부르면 오해를 부르는 것
SYSTEM = {"dwm.exe", "system", "csrss.exe", "registry", "memory compression", "audiodg.exe", "fontdrvhost.exe"}

KNOWN = {
    "msmpeng.exe": "Windows Defender 검사",
    "mscorsvw.exe": ".NET 최적화",
    "searchindexer.exe": "Windows 검색 색인",
    "tiworker.exe": "Windows 업데이트 설치",
    "trustedinstaller.exe": "Windows 업데이트 설치",
    "compattelrunner.exe": "Windows 호환성 원격 분석",
    "losslessscaling.exe": "Lossless Scaling 프레임 생성",
    "client-win64-shipping.exe": "게임(언리얼 엔진)",
    "league of legends.exe": "리그 오브 레전드",
    "msedgewebview2.exe": "웹 화면을 쓰는 앱",
    "dwm.exe": "Windows 화면 그리기",
}


@dataclass
class Diagnosis:
    level: Level
    headline: str
    situation: list[str]             # 지금 상황 (사실)
    judgement: list[str]             # 판단 (사슬)
    evidence: list[str]              # 근거 숫자
    notes: list[str]                 # 이 판단의 한계·읽지 못한 것
    keys: list[str] = field(default_factory=list)   # 걸린 판단 이름 (테스트·기록용)
    stats: WindowStats | None = None


def _f(v, nd=0, unit=""):
    return "–" if v is None else f"{v:.{nd}f}{unit}"


def _bg(p: Proc) -> bool:
    return p.background and p.name.lower() not in SYSTEM


def _name(p: Proc) -> str:
    known = KNOWN.get(p.name.lower())
    return f"{p.name}({known})" if known else p.name


def _proc_lines(stats: WindowStats, n: int = 3) -> list[tuple[Proc, float | None]]:
    """상위 프로세스와 그 프로세스 몫의 CPU 전력 추정(패키지 전력 × CPU 시간 몫)."""
    out = []
    load = stats.cpu.load or 0
    for p in stats.procs[:n]:
        watts = None
        if stats.cpu.power is not None and load > 1 and p.cpu > 0:
            watts = stats.cpu.power * min(1.0, p.cpu / load)
        out.append((p, watts))
    return out


def _situation(stats: WindowStats, ref: Reference | None) -> list[str]:
    c, g, f, pw = stats.cpu, stats.gpu, stats.fans, stats.power
    lines = []
    procs = _proc_lines(stats)
    if procs:
        parts = []
        for p, w in procs:
            use = [f"CPU {p.cpu:.0f}%"] if p.cpu >= 0.5 else []
            if p.gpu >= 0.5:
                use.append(f"GPU {p.gpu:.0f}%")
            parts.append(f"{_name(p)} {' · '.join(use) or '조금'}" + (" (창 없음)" if _bg(p) else ""))
        lines.append("프로그램  " + ", ".join(parts))
    cpu = f"CPU  {_f(c.temp, 0, '°C')} · {_f(c.power, 0, 'W')} · 사용률 {_f(c.load, 0, '%')}"
    if c.thermal_frac:
        cpu += f" · 95°C 스로틀 {c.thermal_frac:.0%}"
    lines.append(cpu)
    gl = [k for k, v in g.limit_frac.items() if v >= THROTTLE_ANY]
    gpu = f"GPU  {_f(g.temp, 0, '°C')} · {_f(g.power, 0, 'W')} · 사용률 {_f(g.load, 0, '%')}"
    if gl:
        gpu += " · 제한 " + ", ".join(f"{'전력' if k == 'power' else '열' if k == 'thermal' else k} {g.limit_frac[k]:.0%}"
                                    for k in gl)
    lines.append(gpu)
    lines.append(f"팬   CPU {_f(f.cpu_rpm)} · GPU {_f(f.gpu_rpm)}rpm")
    src = {True: "어댑터", False: "배터리", None: "전원 ?"}[pw.ac]
    lines.append(f"전원 {src} · 기준 모드 {ref.mode if ref else '없음'}")
    return lines


def _throttle_onset(ref: Reference) -> float | None:
    """기준 측정에서 CPU 가 95°C 스로틀(≥20%)에 걸린 가장 낮은 전력."""
    pts = [p.cpu_power for p in ref.stages if p.cpu_thermal >= THROTTLE_SIG and p.cpu_power is not None]
    return min(pts) if pts else None


def _out_of_range(model, **x) -> list[str]:
    out = []
    for k, (lo, hi) in (model.ranges or {}).items():
        v = x.get(k)
        if v is None:
            continue
        span = hi - lo
        if v > hi + RANGE_MARGIN * span or v < lo - RANGE_MARGIN * span:
            out.append(f"{'CPU' if k == 'cpu_power' else 'GPU'} {v:.0f}W 는 기준 측정 범위({lo:.0f}–{hi:.0f}W) 밖")
    return out


def diagnose(stats: WindowStats, ref: Reference | None, steady: Steady,
             ambient: dict[str, float] | None = None, ambient_now: bool = False) -> Diagnosis:
    """ambient_now: 이 공회전 구간으로 방 온도 차이를 쟀다(그래서 이 구간의 기준 차이는 ±6°C 안이면 0 이 된다)."""
    c, g, f = stats.cpu, stats.gpu, stats.fans
    ambient = ambient or {}
    level = Level.OK
    keys: list[str] = []
    judge: list[str] = []
    ev: list[str] = []
    notes: list[str] = list(stats.notes)
    headline = ""

    def raise_to(lv: Level, head: str) -> None:
        """판정을 올린다. 같은 단계면 먼저 걸린 제목을 남긴다."""
        nonlocal level, headline
        if lv > level or not headline:
            level, headline = max(level, lv), head if lv >= level else headline

    # --- 6.1 하드웨어 사실 --------------------------------------------------------
    throttling = (c.thermal_frac or 0) >= THROTTLE_SIG
    if (c.prochot_frac or 0) >= PROCHOT_FRAC and (c.peak_temp or c.temp or 0) < PROCHOT_MAX_TEMP:
        keys.append("external_prochot")
        raise_to(Level.BAD, "CPU 밖에서 속도를 묶음")
        judge.append("CPU 가 95°C 에 닿지 않았는데도 바깥(전원부·충전기·배터리)에서 속도를 낮추라는 신호(PROCHOT)가 "
                     f"10초 중 {c.prochot_frac:.0%} 동안 켜져 있습니다. 충전기 출력이나 배터리·전원부 문제일 수 있습니다.")
        ev.append(f"PROCHOT {c.prochot_frac:.0%}, 최고 온도 {_f(c.peak_temp, 0, '°C')} (MSR 0x1B1)")
    g_thermal = g.limit_frac.get("thermal", 0)
    if g_thermal >= THROTTLE_ANY or (g.margin is not None and g.margin <= 0):
        keys.append("gpu_thermal")
        raise_to(Level.BAD if (g.margin is not None and g.margin <= 0) else Level.WARN, "GPU 가 열 한계에 닿음")
        judge.append(f"그래픽카드가 열 때문에 속도를 낮추고 있습니다(10초 중 {g_thermal:.0%}, 한계까지 {_f(g.margin, 0, '°C')}). "
                     "기준 측정에서는 한 번도 없던 일입니다.")
        ev.append(f"GPU 열 제한 {g_thermal:.0%}, 한계까지 {_f(g.margin, 0, '°C')} (NVML)")
    if stats.power.ac is False:
        keys.append("battery")
        judge.append("배터리로 동작 중이라 전력 한도가 낮아집니다. 아래 비교는 어댑터 연결 때 잰 기준과의 비교입니다.")

    if ref is None:
        notes.insert(0, "기준 측정 전이라 '평소보다 뜨거운가'는 판단할 수 없습니다. 하드웨어가 알려 준 사실만 봅니다.")
        if throttling:
            keys.append("cpu_throttle")
            judge.append(f"CPU 가 95°C 에 닿아 10초 중 {c.thermal_frac:.0%} 동안 스스로 속도를 낮췄습니다.")
        return _finish(level, headline or ("CPU 95°C 스로틀" if throttling else "하드웨어 이상 신호 없음"),
                       stats, ref, judge, ev, notes, keys)

    # --- 6.2 기준과 비교 (정상 상태에서만) ----------------------------------------------
    if not steady.steady:
        keys.append("not_steady")
        judge.append(f"부하가 바뀌는 중이라 기준과의 비교를 미룹니다({steady.reason}).")
        if throttling:
            keys.append("cpu_throttle")
            judge.append(f"CPU 가 95°C 에 닿아 10초 중 {c.thermal_frac:.0%} 동안 스스로 속도를 낮췄습니다.")
        return _finish(level, headline or "비교 보류 (부하 변화 중)", stats, ref, judge, ev, notes, keys)

    hot_cooling = False      # 기대보다 뜨거움
    fan_slow = None          # 팬이 기준보다 느린 비율
    # CPU: 스로틀 중이면 온도가 95°C 에 묶여 있으므로 '몇 W 에서 묶였나'로 비교한다
    if throttling and c.power is not None:
        onset = _throttle_onset(ref)
        ev.append(f"CPU 95°C 스로틀 {c.thermal_frac:.0%} at {c.power:.0f}W (기준: {_f(onset, 0, 'W')} 부터 스로틀)")
        if onset and c.power < THROTTLE_POWER_BAD * onset:
            keys.append("cpu_throttle_low_power")
            hot_cooling = True
            raise_to(Level.BAD, "CPU 가 평소보다 훨씬 낮은 전력에서 95°C")
            judge.append(f"CPU 가 {c.power:.0f}W 에서 95°C 에 묶였습니다. 기준 측정에서는 {onset:.0f}W 까지 버텼습니다.")
        elif onset and c.power < THROTTLE_POWER_WARN * onset:
            keys.append("cpu_throttle_low_power")
            hot_cooling = True
            raise_to(Level.WARN, "CPU 가 평소보다 낮은 전력에서 95°C")
            judge.append(f"CPU 가 {c.power:.0f}W 에서 95°C 에 묶였습니다. 기준 측정에서는 {onset:.0f}W 부터 묶였습니다.")
        else:
            keys.append("cpu_design_limit")
            judge.append(f"CPU 가 {c.power:.0f}W 를 쓰며 95°C 에 닿아 10초 중 {c.thermal_frac:.0%} 동안 속도를 낮췄습니다. "
                         "기준 측정 때도 이 전력에서 똑같이 묶였습니다 — 이 모드의 냉각 설계 한계이며 고장은 아닙니다(성능 손해는 있음).")
    elif ref.cpu_model and model_temp(ref.cpu_model, c) is not None:
        m = ref.cpu_model
        ct = model_temp(m, c)
        what = "CPU 코어 평균" if m.target == "cpu_temp_avg" else "CPU"
        pred = m.predict(cpu_power=c.power, gpu_power=g.power)
        if pred is not None:
            off = ambient.get("CPU", 0.0)
            resid = ct - (pred + off)
            if ambient_now:
                ev.append(f"{what} {ct:.0f}°C, 모델 {pred:.0f}°C — 이 공회전 구간으로 방 온도 차이({off:+.0f}°C)를 쟀습니다. "
                          f"공회전에서는 방 온도 몫(최대 ±{AMBIENT_CLIP:.0f}°C)을 뺀 나머지만 비교합니다")
            else:
                ev.append(f"{what} 기대 {pred + off:.0f}°C (모델 {pred:.0f}°C" + (f", 방 온도 보정 {off:+.0f}°C" if round(off) else "")
                          + f") · 실제 {ct:.0f}°C · 차 {resid:+.1f}°C = {resid / m.sigma:+.1f}σ (σ {m.sigma:.1f}°C)")
            notes += _out_of_range(m, cpu_power=c.power, gpu_power=g.power)
            if resid > SIGMA_BAD * m.sigma:
                hot_cooling = True
                keys.append("cpu_hotter")
                raise_to(Level.BAD, f"CPU 가 평소보다 {resid:.0f}°C 뜨거움")
            elif resid > SIGMA_WARN * m.sigma:
                hot_cooling = True
                keys.append("cpu_hotter")
                raise_to(Level.WARN, f"CPU 가 평소보다 {resid:.0f}°C 뜨거움")
            if hot_cooling:
                judge.append(f"CPU 가 {c.power:.0f}W 에서 {ct:.0f}°C({what}) 입니다. "
                             f"이 노트북은 이 전력에서 보통 {pred + off:.0f}°C 입니다({resid:+.0f}°C).")
    if ref.gpu_model and g.temp is not None:
        m = ref.gpu_model
        pred = m.predict(gpu_power=g.power, cpu_power=c.power)
        if pred is not None:
            off = ambient.get("GPU", 0.0)
            resid = g.temp - (pred + off)
            if ambient_now:
                ev.append(f"GPU {g.temp:.0f}°C, 모델 {pred:.0f}°C — 방 온도 차이({off:+.0f}°C) 측정에 썼습니다")
            else:
                ev.append(f"GPU 기대 {pred + off:.0f}°C · 실제 {g.temp:.0f}°C · 차 {resid:+.1f}°C = {resid / m.sigma:+.1f}σ (σ {m.sigma:.1f}°C)")
            notes += [n for n in _out_of_range(m, gpu_power=g.power, cpu_power=c.power) if n not in notes]
            if resid > SIGMA_WARN * m.sigma:
                hot_cooling = True
                keys.append("gpu_hotter")
                raise_to(Level.BAD if resid > SIGMA_BAD * m.sigma else Level.WARN, f"GPU 가 평소보다 {resid:.0f}°C 뜨거움")
                judge.append(f"그래픽카드가 {g.power:.0f}W 에서 {g.temp:.0f}°C 입니다. 이 노트북은 보통 {pred + off:.0f}°C 입니다"
                             f"({resid:+.0f}°C).")
    # 팬
    curve = ref.fan_curve.get("cpu") or []
    if c.temp is not None and f.cpu_rpm is not None and curve:
        exp = expected_fan(curve, c.temp)
        if exp and exp >= FAN_COMPARE_MIN:
            ratio = f.cpu_rpm / exp
            ev.append(f"CPU 팬 {f.cpu_rpm:.0f}rpm, 기준 팬 곡선에서 {c.temp:.0f}°C 이면 {exp:.0f}rpm ({ratio:.0%})")
            if ratio < FAN_WARN:
                fan_slow = ratio
                keys.append("fan_slow")
                raise_to(Level.BAD if ratio < FAN_BAD else Level.WARN, "팬이 평소보다 느림")

    # 전력 한도 (간접): CPU 를 많이 쓰는데 스로틀 없이 전력이 낮게 묶임
    limited = (c.load or 0) >= HEAVY_CPU_LOAD and (c.thermal_frac or 0) < THROTTLE_ANY and ref.cpu_sustained_w \
        and c.power is not None and c.power < POWER_LIMIT_RATIO * ref.cpu_sustained_w
    if limited:
        keys.append("cpu_power_limited")
        ev.append(f"CPU 사용률 {c.load:.0f}%, 전력 {c.power:.0f}W < 기준 지속 전력 {ref.cpu_sustained_w:.0f}W 의 "
                  f"{POWER_LIMIT_RATIO:.0%}, 95°C 스로틀 없음 (간접 판단 — 0x64F 를 읽을 수 없음)")

    # --- 6.3 원인 -------------------------------------------------------------------
    if hot_cooling and fan_slow is not None:
        judge.append(f"팬이 기준 측정 때보다 느리게 돕니다({fan_slow:.0%})."
                     + (" CPU 전력 한도도 기준보다 낮습니다 — 기준과 다른 성능 모드(예: Silent)로 보입니다."
                        if limited else " 전력 한도는 같으니 팬 설정이나 팬 자체를 확인하세요."))
    elif hot_cooling:
        judge.append("팬은 기준대로 돌고 있는데도 열이 평소만큼 빠지지 않습니다. 흡·배기구 먼지, 서멀구리스 열화, "
                     "통풍이 막힌 자리(이불·무릎 위)를 의심하세요. 어느 것인지는 이 값들로 가를 수 없습니다.")
    elif fan_slow is not None:
        judge.append(f"온도는 평소 범위지만 팬이 기준 측정 때보다 느립니다({fan_slow:.0%}). 기준과 다른 성능 모드일 수 있습니다.")
    if limited and not hot_cooling:
        judge.append(f"CPU 를 많이 쓰는데 전력이 {c.power:.0f}W 에 묶여 있고 온도는 여유가 있습니다 — "
                     + ("배터리라 전력 한도가 낮습니다." if stats.power.ac is False
                        else "온도가 아니라 전력 한도 때문입니다. 기준과 다른 성능 모드로 보입니다."))
    # 부하와 범인
    procs = _proc_lines(stats)
    big = [(p, w) for p, w in procs if p.cpu >= 3 or p.gpu >= 5]
    if big and not hot_cooling:
        who = ", ".join(f"{_name(p)}(CPU {p.cpu:.0f}%" + (f"·약 {w:.0f}W" if w and w >= 1 else "")
                        + (f", GPU {p.gpu:.0f}%" if p.gpu >= 1 else "") + ")" + (" — 창 없는 작업" if _bg(p) else "")
                        for p, w in big)
        judge.append(f"전력을 쓰는 프로그램: {who}."
                     + ("" if "cpu_design_limit" in keys else " 온도는 이 전력에서 이 노트북의 평소 범위입니다."))
    # 히트파이프 공유
    if ref.cpu_model and "gpu_power" in ref.cpu_model.coef and g.power and c.power is not None:
        share = ref.cpu_model.coef["gpu_power"] * g.power
        if share >= 5 and c.power < 15:
            keys.append("shared_heatpipe")
            judge.append(f"CPU 는 {c.power:.0f}W 만 쓰지만 그래픽카드 {g.power:.0f}W 의 열이 같은 히트파이프로 CPU 를 약 "
                         f"{share:.0f}°C 데우고 있습니다 — 정상입니다.")
    if not judge:
        keys.append("normal")
        judge.append("부하가 거의 없고, 온도·팬 모두 이 노트북의 기준 범위입니다.")
    return _finish(level, headline or ("냉각 설계 한계에서 동작" if "cpu_design_limit" in keys else "이상 신호 없음"),
                   stats, ref, judge, ev, notes, keys)


def _finish(level, headline, stats, ref, judge, ev, notes, keys) -> Diagnosis:
    if ref is not None:
        notes = notes + [n for n in ref.notes if "범위" in n or "합쳐" in n][:2]
    return Diagnosis(level, f"{level.label} — {headline}", _situation(stats, ref), judge, ev,
                     list(dict.fromkeys(notes)), keys, stats)


# --- 시간에 걸친 상태 --------------------------------------------------------------

CARRY_SECONDS = 60             # 부하 변화 중이면 이 안의 마지막 비교 결과(주의·이상)를 이어 간다
AMBIENT_CLIP = 6.0
AMBIENT_MAX_AGE = 1800          # 30분 넘은 공회전 보정은 버린다
IDLE_CPU_W = 12.0
IDLE_GPU_W = 6.0


class Diagnoser:
    """1초 표본을 받아 정상 상태와 방 온도 보정을 들고 있다가, 10초 창마다 diagnose() 를 부른다.

    방 온도 보정 (DESIGN 6.3): 방 온도 센서가 없으므로, 이 세션에서 정상 상태 공회전일 때 모델과의 차이를
    '방 온도 차이'로 보고 이후 비교에서 뺀다(±6°C 로 자름, 30분 유효). 근거 칸에 보정값을 적는다.
    공회전 때는 냉각 저하가 거의 드러나지 않으므로(전력이 작음) 이 차이는 주로 방 온도 차이다.
    """

    def __init__(self, ref: Reference | None, history: int = 240) -> None:
        self.ref = ref
        self.points: deque[tuple] = deque(maxlen=history)
        self.ambient: dict[str, float] = {}
        self._ambient_at: float | None = None
        self.last: tuple[float, Diagnosis] | None = None    # 마지막으로 기준과 비교한 판단

    def add(self, t: float, cpu_w, gpu_w, gpu_t, fan=None) -> None:
        self.points.append((t, cpu_w, gpu_w, gpu_t, fan))

    def diagnose(self, stats: WindowStats, now: float) -> Diagnosis:
        st = steady_state(list(self.points), now)
        measured = False
        if self._ambient_at is not None and now - self._ambient_at > AMBIENT_MAX_AGE:
            self.ambient, self._ambient_at = {}, None
        if st.steady and self.ref is not None:
            c, g = stats.cpu, stats.gpu
            if (c.power or 99) < IDLE_CPU_W and (g.power or 99) < IDLE_GPU_W and (c.thermal_frac or 0) == 0:
                amb = {}
                cpu_t = model_temp(self.ref.cpu_model, c) if self.ref.cpu_model else None
                for dev, m, temp in (("CPU", self.ref.cpu_model, cpu_t), ("GPU", self.ref.gpu_model, g.temp)):
                    if m and temp is not None:
                        p = m.predict(cpu_power=c.power, gpu_power=g.power)
                        if p is not None:
                            amb[dev] = max(-AMBIENT_CLIP, min(AMBIENT_CLIP, temp - p))
                if amb:
                    self.ambient, self._ambient_at = amb, now
                    measured = True
        d = diagnose(stats, self.ref, st, self.ambient, measured)
        if "not_steady" not in d.keys and self.ref is not None:
            self.last = (now, d)
        elif self.last is not None and self.ref is not None:
            age = now - self.last[0]
            prev = self.last[1]
            d.judgement.append(f"{age:.0f}초 전 마지막 비교: {prev.headline}")
            d.evidence = d.evidence + [f"({age:.0f}초 전 비교) {e}" for e in prev.evidence]
            if age <= CARRY_SECONDS and prev.level > d.level:
                d.level = prev.level
                d.headline = f"{prev.headline} ({age:.0f}초 전 비교, 지금은 부하 변화 중)"
                d.keys.append("carried")
        return d


def load_reference(store, mode: str | None = None) -> tuple[Reference | None, str | None]:
    """가장 최근 완료된 기준 측정과 그 설명."""
    import time as _t
    d = store.latest_checkup(mode)
    if not d or not d.get("result"):
        return None, None
    ref = Reference.from_json(d["result"])
    when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(d["started"]))
    return ref, f"기준 측정 #{d['id']} ({when}, {ref.mode} 모드)"
