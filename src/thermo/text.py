"""판정 결과를 한국어 문장으로 만든다 (DESIGN 7·8장)."""

from __future__ import annotations

from .model import Cause, Level, Verdict, WindowStats

DEVICE = {"CPU": "CPU", "GPU": "GPU"}


def _n(v: float | None, unit: str = "", nd: int = 0) -> str:
    return "–" if v is None else f"{v:.{nd}f}{unit}"


def cause_sentence(c: Cause) -> str:
    d = c.data
    dev = DEVICE.get(c.device, c.device)
    if c.key == "high_load":
        busy = d["busy"]
        if d.get("proc"):
            who = f"{d['proc']} 가 {busy} 를 {d['proc_use']:.0f}% 쓰고 있어"
        else:
            who = f"{busy} 부하가 {d['load']:.0f}% 로 높아"
        return f"{who} {dev} 온도가 올라갔습니다. 부하에 따른 발열입니다."
    if c.key == "background":
        what = f"({d['what']})" if d.get("what") else ""
        return f"보이지 않는 작업 {d['proc']}{what} 가 CPU 를 {d['use']:.0f}% 쓰고 있습니다."
    if c.key == "fan_max":
        return "팬이 최고 속도인데도 온도가 높습니다. 흡·배기구 먼지나 서멀 열화를 의심하세요."
    if c.key == "fan_slow":
        if d.get("mode") == "Silent":
            return "Armoury Crate 가 Silent 모드라 팬이 천천히 돕니다."
        return (f"온도에 비해 팬이 느립니다({d['rpm']:.0f}rpm). "
                "Armoury Crate 가 Silent 모드인지, 팬에 이상이 없는지 확인하세요.")
    if c.key == "thermal_throttle":
        return f"{dev} 가 온도 한계에 닿아 클럭을 낮추고 있습니다(열 스로틀)."
    if c.key == "power_limit":
        return f"{dev} 클럭이 온도가 아니라 전력 한도 때문에 제한되고 있습니다."
    if c.key == "battery":
        return "배터리로 동작 중이라 성능이 제한됩니다."
    if c.key == "perf_mode":
        if d["mode"] == "Turbo":
            return "성능 모드가 Turbo 라 평소보다 높은 온도가 정상입니다."
        return f"성능 모드: {d['mode']}."
    if c.key == "scheme":
        return f"전원 계획이 '{d['scheme']}' 이라 온도가 높게 유지될 수 있습니다."
    if c.key == "unknown":
        return "뚜렷한 원인을 찾지 못했습니다. 주변 온도나 바닥 통풍을 확인하세요."
    raise KeyError(c.key)


def _ok_sentence(s: WindowStats) -> str:
    parts = []
    if s.cpu.temp is not None:
        parts.append(f"CPU {s.cpu.temp:.0f}°C(부하 {_n(s.cpu.load, '%')})")
    if s.gpu.temp is not None:
        parts.append(f"GPU {s.gpu.temp:.0f}°C(부하 {_n(s.gpu.load, '%')})")
    if not parts:
        return "온도를 읽지 못했습니다."
    return ", ".join(parts) + " — 부하에 맞는 온도입니다."


def summary(v: Verdict) -> list[str]:
    """화면 맨 위에 보일 한두 문장 + 보조 문장."""
    if v.level == Level.OK:
        lines = [_ok_sentence(v.stats)]
    else:
        lines = [cause_sentence(c) for c in v.causes]
    return lines + [cause_sentence(c) for c in v.extras]


def evidence(v: Verdict) -> list[str]:
    out = [f.evidence for f in v.findings]
    for c in v.causes + v.extras:
        out += c.evidence
    return list(dict.fromkeys(out))


def table(s: WindowStats) -> list[str]:
    c, g, f, p = s.cpu, s.gpu, s.fans, s.power
    ghz = lambda mhz: _n(None if mhz is None else mhz / 1000, "GHz", 1)  # noqa: E731
    rows = [
        f"{'':4}{'온도':>5}{'부하':>5}{'전력':>5}{'클럭':>7}",  # 한글은 두 칸 폭
        f"{'CPU':4}{_n(c.temp, '°C'):>7}{_n(c.load, '%'):>7}{_n(c.power, 'W'):>7}{ghz(c.clock):>9}",
        f"{'GPU':4}{_n(g.temp, '°C'):>7}{_n(g.load, '%'):>7}{_n(g.power, 'W'):>7}{ghz(g.clock):>9}",
        f"팬   CPU {_n(f.cpu_rpm, 'rpm')} · GPU {_n(f.gpu_rpm, 'rpm')}",
    ]
    power = {True: "어댑터", False: "배터리", None: "전원 ?"}[p.ac]
    if p.charging:
        power += "(충전 중)"
    rows.append(f"전원 {power} · {p.scheme or '전원 계획 ?'} · 성능 모드 {p.perf_mode or '확인 불가'}")
    return rows


def report(v: Verdict) -> str:
    """--once 터미널 출력."""
    out = [f"● {v.level.label}"]
    out += [f"  {line}" for line in summary(v)]
    out.append("")
    out += table(v.stats)
    ev = evidence(v)
    if ev:
        out.append("")
        out.append("근거")
        out += [f"  · {e}" for e in ev]
    if v.stats.notes:
        out.append("")
        out += [f"※ {n}" for n in v.stats.notes]
    return "\n".join(out)
