"""판단 결과를 화면·터미널용 한국어로 (DESIGN 2판 8장)."""

from __future__ import annotations

from .diagnose import Diagnosis
from .model import WindowStats


def _n(v: float | None, unit: str = "", nd: int = 0) -> str:
    return "–" if v is None else f"{v:.{nd}f}{unit}"


HEADER = ("온도", "부하", "전력", "클럭")


def device_cells(s: WindowStats) -> dict[str, tuple[str, str, str, str]]:
    """CPU/GPU 표 줄. 값은 HEADER 순서."""
    def ghz(mhz):
        return _n(None if mhz is None else mhz / 1000, "GHz", 1)
    return {
        "CPU": (_n(s.cpu.temp, "°C"), _n(s.cpu.load, "%"), _n(s.cpu.power, "W"), ghz(s.cpu.clock)),
        "GPU": (_n(s.gpu.temp, "°C"), _n(s.gpu.load, "%"), _n(s.gpu.power, "W"), ghz(s.gpu.clock)),
    }


def fan_line(s: WindowStats) -> str:
    return f"CPU {_n(s.fans.cpu_rpm, 'rpm')} · GPU {_n(s.fans.gpu_rpm, 'rpm')}"


def power_line(s: WindowStats) -> str:
    p = s.power
    power = {True: "어댑터", False: "배터리", None: "전원 ?"}[p.ac]
    if p.charging:
        power += "(충전 중)"
    return f"{power} · {p.scheme or '전원 계획 ?'}"


def report(d: Diagnosis, ref_label: str | None = None) -> str:
    """--once 터미널 출력."""
    out = [f"● {d.headline}", "", "지금 상황"]
    out += [f"  {line}" for line in d.situation]
    out += ["", "판단"]
    out += [f"  → {line}" for line in d.judgement]
    if d.evidence:
        out += ["", "근거"]
        out += [f"  · {e}" for e in d.evidence]
    if ref_label:
        out += ["", f"기준: {ref_label}"]
    if d.notes:
        out.append("")
        out += [f"※ {n}" for n in d.notes]
    return "\n".join(out)
