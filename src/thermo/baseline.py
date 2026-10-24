"""이 PC 의 평소값 (DESIGN 6.3). 프로그램이 켜져 있는 동안의 창 결과를 SQLite 에 쌓는다.

위치: %LOCALAPPDATA%\\thermo\\baseline.db (THERMO_HOME 으로 바꿀 수 있음, 테스트용).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .model import Level, Verdict, WindowStats
from .rules import BaselineRef

LOAD_BUCKETS = (10, 30, 60)       # 0–10 / 10–30 / 30–60 / 60–100 %
KEEP_PER_BUCKET = 2000            # 묶음마다 최근 것만 둔다
KEEP_FANS = 20000
FAN_MIN_SAMPLES = 30
FAN_MAX_FLOOR = 4500              # 이보다 낮은 '최고 RPM' 은 아직 팬을 세게 돌린 적이 없는 것으로 본다
FANS = {"CPU": "cpu_rpm", "GPU": "gpu_rpm"}


def home() -> Path:
    env = os.environ.get("THERMO_HOME")
    base = Path(env) if env else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thermo"
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_bucket(load: float | None) -> int:
    if load is None:
        return -1
    return sum(1 for edge in LOAD_BUCKETS if load >= edge)


def _key(device: str, s: WindowStats) -> tuple[str, int, int, str]:
    load = s.cpu.load if device == "CPU" else s.gpu.load
    ac = -1 if s.power.ac is None else int(s.power.ac)
    return device, load_bucket(load), ac, s.power.perf_mode or "?"


def _quantile(sorted_vals: list[float], q: float) -> float:
    i = (len(sorted_vals) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


class Baseline:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or home() / "baseline.db"
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS temps (
                ts REAL, device TEXT, bucket INTEGER, ac INTEGER, mode TEXT, temp REAL);
            CREATE INDEX IF NOT EXISTS temps_key ON temps (device, bucket, ac, mode, ts);
            CREATE TABLE IF NOT EXISTS fans (ts REAL, fan TEXT, rpm REAL);
            CREATE INDEX IF NOT EXISTS fans_key ON fans (fan, rpm);
        """)

    def close(self) -> None:
        self.db.close()

    # --- 읽기 -----------------------------------------------------------
    def lookup(self, device: str, s: WindowStats) -> BaselineRef | None:
        key = _key(device, s)
        if key[1] < 0:
            return None
        rows = [r[0] for r in self.db.execute(
            "SELECT temp FROM temps WHERE device=? AND bucket=? AND ac=? AND mode=? ORDER BY temp", key)]
        if not rows:
            return BaselineRef(p95=float("nan"), n=0)
        return BaselineRef(p95=_quantile(rows, 0.95), n=len(rows), median=_quantile(rows, 0.5))

    def fan_max(self) -> dict[str, float]:
        """이 PC 에서 관측한 팬 최고 RPM (99번째 백분위, 튐 무시)."""
        out = {}
        for dev in FANS:
            rows = [r[0] for r in self.db.execute("SELECT rpm FROM fans WHERE fan=? ORDER BY rpm", (dev,))]
            if len(rows) >= FAN_MIN_SAMPLES:
                top = _quantile(rows, 0.99)
                if top >= FAN_MAX_FLOOR:
                    out[dev] = top
        return out

    # --- 쓰기 -----------------------------------------------------------
    def record(self, v: Verdict, now: float | None = None) -> bool:
        """창 하나를 평소값에 넣는다. 이상 판정은 넣지 않는다(나쁜 상태를 평소로 배우지 않게)."""
        if v.level >= Level.BAD:
            return False
        s, ts = v.stats, now or time.time()
        with self.db:
            for dev, temp in (("CPU", s.cpu.temp), ("GPU", s.gpu.temp)):
                key = _key(dev, s)
                if temp is None or key[1] < 0:
                    continue
                self.db.execute("INSERT INTO temps VALUES (?,?,?,?,?,?)", (ts, *key, temp))
                self.db.execute("""
                    DELETE FROM temps WHERE rowid IN (
                        SELECT rowid FROM temps WHERE device=? AND bucket=? AND ac=? AND mode=?
                        ORDER BY ts DESC LIMIT -1 OFFSET ?)""", (*key, KEEP_PER_BUCKET))
            for dev, attr in FANS.items():
                rpm = getattr(s.fans, attr)
                if rpm:
                    self.db.execute("INSERT INTO fans VALUES (?,?,?)", (ts, dev, rpm))
                    self.db.execute("""
                        DELETE FROM fans WHERE rowid IN (
                            SELECT rowid FROM fans WHERE fan=? ORDER BY ts DESC LIMIT -1 OFFSET ?)""",
                                    (dev, KEEP_FANS))
        return True
