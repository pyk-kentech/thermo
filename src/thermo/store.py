"""1초 표본 시계열 기록 (DESIGN 2판 5장). 진단을 나중에 다시 계산·검증할 수 있게 원시 값을 남긴다.

위치: %LOCALAPPDATA%\\thermo\\thermo.db (THERMO_HOME 으로 바꿀 수 있음). 30일 지난 표본은 지운다.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .baseline import home
from .model import Snapshot

KEEP_DAYS = 30

COLUMNS = {
    # 이름: (SQL 형, Snapshot 에서 꺼내는 함수)
    "ts": ("REAL", lambda s: s.t),
    "cpu_temp": ("REAL", lambda s: s.cpu.temp),
    "cpu_temp_max": ("REAL", lambda s: s.cpu.temp_max),
    "cpu_peak": ("REAL", lambda s: s.cpu.peak_temp),
    "cpu_tj_dist": ("REAL", lambda s: s.cpu.tj_distance),
    "cpu_power": ("REAL", lambda s: s.cpu.power),
    "cpu_load": ("REAL", lambda s: s.cpu.load),
    "cpu_clock": ("REAL", lambda s: s.cpu.clock),
    "cpu_perf_limit": ("REAL", lambda s: s.cpu.perf_limit),
    "cpu_thermal_frac": ("REAL", lambda s: s.cpu.thermal_frac),
    "cpu_prochot_frac": ("REAL", lambda s: s.cpu.prochot_frac),
    "cpu_limits": ("TEXT", lambda s: json.dumps(s.cpu.limit_frac) if s.cpu.limit_frac else None),
    "gpu_temp": ("REAL", lambda s: s.gpu.temp),
    "gpu_margin": ("REAL", lambda s: s.gpu.margin),
    "gpu_power": ("REAL", lambda s: s.gpu.power),
    "gpu_power_limit": ("REAL", lambda s: s.gpu.power_limit),
    "gpu_load": ("REAL", lambda s: s.gpu.load),
    "gpu_clock": ("REAL", lambda s: s.gpu.clock),
    "gpu_reasons": ("INTEGER", lambda s: s.gpu.reasons),
    "gpu_limits": ("TEXT", lambda s: json.dumps(s.gpu.limit_frac) if s.gpu.limit_frac else None),
    "fan_cpu": ("REAL", lambda s: s.fans.cpu_rpm),
    "fan_gpu": ("REAL", lambda s: s.fans.gpu_rpm),
    "ac": ("INTEGER", lambda s: None if s.power.ac is None else int(s.power.ac)),
    "charging": ("INTEGER", lambda s: None if s.power.charging is None else int(s.power.charging)),
    "scheme": ("TEXT", lambda s: s.power.scheme),
    "procs": ("TEXT", lambda s: json.dumps([[p.name, p.pid, round(p.cpu, 2), round(p.gpu, 2), p.background]
                                            for p in s.procs], ensure_ascii=False)),
}


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or home() / "thermo.db"
        self.db = sqlite3.connect(self.path)
        cols = ", ".join(f"{k} {t}" for k, (t, _) in COLUMNS.items())
        self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS samples ({cols});
            CREATE INDEX IF NOT EXISTS samples_ts ON samples (ts);
            CREATE TABLE IF NOT EXISTS meta (ts REAL, key TEXT, value TEXT);
        """)
        self._pending = 0
        self.prune()

    def add(self, s: Snapshot) -> None:
        names = list(COLUMNS)
        self.db.execute(f"INSERT INTO samples ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                        [get(s) for _, get in COLUMNS.values()])
        self._pending += 1
        if self._pending >= 10:   # 10초마다 커밋 (디스크 쓰기 줄이기)
            self.db.commit()
            self._pending = 0

    def note(self, key: str, value: object) -> None:
        """시작 때 CPU 한도 같은 설정값을 남긴다."""
        self.db.execute("INSERT INTO meta VALUES (?,?,?)", (time.time(), key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def rows(self, since: float, until: float | None = None) -> list[dict]:
        q = "SELECT * FROM samples WHERE ts >= ?" + (" AND ts < ?" if until else "") + " ORDER BY ts"
        cur = self.db.execute(q, (since, until) if until else (since,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur]

    def prune(self, now: float | None = None) -> None:
        cutoff = (now or time.time()) - KEEP_DAYS * 86400
        with self.db:
            self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))

    def close(self) -> None:
        self.db.commit()
        self.db.close()


# --- 정상 상태 판별 (DESIGN 2판 5장) ------------------------------------------

STEADY_SECONDS = 60
STEADY_REL = 0.15
STEADY_ABS_W = 3.0


@dataclass
class Steady:
    steady: bool
    reason: str          # 아닐 때 한국어 이유


def steady_state(points: Sequence[tuple[float, float | None, float | None]], now: float,
                 seconds: float = STEADY_SECONDS) -> Steady:
    """(시각, CPU W, GPU W) 목록에서 직전 seconds 초 동안 두 전력이 각각 평균 ±15%(또는 ±3W) 안이었나."""
    recent = [p for p in points if now - seconds <= p[0] <= now]
    if not recent or recent[-1][0] - recent[0][0] < seconds * 0.9:
        return Steady(False, f"아직 {seconds:.0f}초 동안 재지 못했습니다")
    for idx, dev in ((1, "CPU"), (2, "GPU")):
        vals = [p[idx] for p in recent if p[idx] is not None]
        if len(vals) < len(recent) * 0.9:
            return Steady(False, f"{dev} 전력을 읽지 못한 때가 많습니다")
        mean = sum(vals) / len(vals)
        tol = max(STEADY_REL * mean, STEADY_ABS_W)
        if any(abs(v - mean) > tol for v in vals):
            return Steady(False, f"{dev} 전력이 바뀌는 중입니다({min(vals):.0f}–{max(vals):.0f}W)")
    return Steady(True, "")


def power_points(rows: Iterable[dict]) -> list[tuple[float, float | None, float | None]]:
    return [(r["ts"], r["cpu_power"], r["gpu_power"]) for r in rows]
