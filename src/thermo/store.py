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

from .paths import home
from .model import Snapshot

KEEP_DAYS = 30

COLUMNS = {
    # 이름: (SQL 형, Snapshot 에서 꺼내는 함수)
    "ts": ("REAL", lambda s: s.t),
    "cpu_temp": ("REAL", lambda s: s.cpu.temp),
    "cpu_temp_max": ("REAL", lambda s: s.cpu.temp_max),
    "cpu_temp_avg": ("REAL", lambda s: s.cpu.temp_avg),
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
            CREATE TABLE IF NOT EXISTS checkup (
                id INTEGER PRIMARY KEY, started REAL, finished REAL, mode TEXT, room REAL,
                status TEXT, result TEXT);
            CREATE TABLE IF NOT EXISTS checkup_stage (
                checkup_id INTEGER, stage TEXT, start REAL, hold_start REAL, end REAL,
                steady INTEGER, note TEXT);
        """)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(samples)")}
        for k, (t, _) in COLUMNS.items():          # 나중에 더한 열 (cpu_temp_avg: M8)
            if k not in have:
                self.db.execute(f"ALTER TABLE samples ADD COLUMN {k} {t}")
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

    # --- 기준 측정 (DESIGN 2판 7장) ---------------------------------------------
    def checkup_begin(self, mode: str, room: float | None, now: float | None = None) -> int:
        cur = self.db.execute("INSERT INTO checkup (started, mode, room, status) VALUES (?,?,?,?)",
                              (now or time.time(), mode, room, "진행 중"))
        self.db.commit()
        return int(cur.lastrowid)

    def checkup_stage(self, cid: int, stage: str, start: float, hold_start: float | None, end: float,
                      steady: bool, note: str = "") -> None:
        self.db.execute("INSERT INTO checkup_stage VALUES (?,?,?,?,?,?,?)",
                        (cid, stage, start, hold_start, end, int(steady), note))
        self.db.commit()

    def checkup_end(self, cid: int, status: str, result: dict | None, now: float | None = None) -> None:
        self.db.execute("UPDATE checkup SET finished=?, status=?, result=? WHERE id=?",
                        (now or time.time(), status, None if result is None else json.dumps(result, ensure_ascii=False), cid))
        self.db.commit()

    def checkup_stages(self, cid: int) -> list[dict]:
        cur = self.db.execute("SELECT * FROM checkup_stage WHERE checkup_id=? ORDER BY start", (cid,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur]

    def latest_checkup(self, mode: str | None = None, status: str = "완료") -> dict | None:
        q = "SELECT * FROM checkup WHERE status=?" + (" AND mode=?" if mode else "") + " ORDER BY started DESC LIMIT 1"
        cur = self.db.execute(q, (status, mode) if mode else (status,))
        r = cur.fetchone()
        if r is None:
            return None
        d = dict(zip([x[0] for x in cur.description], r))
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def rows(self, since: float, until: float | None = None) -> list[dict]:
        q = "SELECT * FROM samples WHERE ts >= ?" + (" AND ts < ?" if until else "") + " ORDER BY ts"
        cur = self.db.execute(q, (since, until) if until else (since,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur]

    def prune(self, now: float | None = None) -> None:
        """30일 지난 표본을 지운다. 기준 측정 구간의 표본은 남긴다(다시 계산할 수 있게)."""
        cutoff = (now or time.time()) - KEEP_DAYS * 86400
        with self.db:
            self.db.execute("""
                DELETE FROM samples WHERE ts < ? AND NOT EXISTS (
                    SELECT 1 FROM checkup c WHERE samples.ts BETWEEN c.started AND COALESCE(c.finished, c.started))""",
                            (cutoff,))

    def close(self) -> None:
        self.db.commit()
        self.db.close()


# --- 정상 상태 판별 (DESIGN 2판 5장) ------------------------------------------
#
# M7 첫 기준 측정에서 1판 기준(60초 동안 1초 값이 모두 평균 ±15%/±3W 안)은 너무 엄격했다:
# 공회전은 짧은 부스트(1초 51W)로, 게임은 장면 변화(10초 평균 GPU 26–40W)로 끝내 통과하지 못했다.
# 열평형에서 중요한 것은 '평균이 움직이지 않는가'이므로, 120초를 앞뒤 60초로 나눠 평균을 비교하고,
# 느리게 데워지는 쪽(GPU 온도·방열판)이 30초 묶음 사이에서 2°C 안에 머무는지를 본다.
# CPU 칩 온도는 전력을 1–2초 안에 따라가므로(M6) 평형 판단에 쓰지 않는다.
# M8: 팬도 본다. 기록 재생에서, 공회전(팬 0rpm) 중 터진 백그라운드 작업(28W)을 전력·GPU 온도만 보고 '정상 상태'로
# 판단해 CPU 가 기대보다 15°C 뜨겁다고 잘못 말했다 — 그때 팬은 2600rpm 으로 아직 오르는 중이었다(3700rpm 까지 약 40초).
# 그래서 마지막 30초 팬 중앙값이 처음 30초보다 STEADY_FAN_RISE 넘게 높으면(팬이 따라오는 중) 정상 상태가 아니다.
# 또, 판단하는 마지막 10초 자체가 직전 60초 평균과 같아야 한다. 같은 재생에서 부하가 끝나는 10초(27W→13W)를
# 판단해 오탐했다(앞뒤 60초 평균은 34W/31W 로 비슷했음).

STEADY_SECONDS = 120
STEADY_REL = 0.15
STEADY_ABS_W = 3.0
STEADY_GPU_TEMP_C = 2.0
STEADY_FAN_RISE = 500.0
STEADY_FAN_MIN_W = 15.0     # 공회전 팬은 EC 가 0↔2500rpm 을 오가게 둔다(M7) → 부하가 있을 때만 팬을 본다


@dataclass
class Steady:
    steady: bool
    reason: str          # 아닐 때 한국어 이유


def steady_state(points: Sequence[tuple], now: float, seconds: float = STEADY_SECONDS) -> Steady:
    """points: (시각, CPU W, GPU W[, GPU °C[, CPU 팬 rpm]]). 직전 seconds 초가 열평형인가."""
    recent = [p for p in points if now - seconds < p[0] <= now]
    if not recent or recent[-1][0] - recent[0][0] < seconds * 0.9:
        return Steady(False, f"아직 {seconds:.0f}초 동안 재지 못했습니다")
    mid = now - seconds / 2
    for idx, dev in ((1, "CPU"), (2, "GPU")):
        a = [p[idx] for p in recent if p[0] <= mid and p[idx] is not None]
        b = [p[idx] for p in recent if p[0] > mid and p[idx] is not None]
        if len(a) + len(b) < len(recent) * 0.9 or not a or not b:
            return Steady(False, f"{dev} 전력을 읽지 못한 때가 많습니다")
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        if abs(mb - ma) > max(STEADY_REL * (ma + mb) / 2, STEADY_ABS_W):
            return Steady(False, f"{dev} 전력 평균이 바뀌는 중입니다({ma:.0f}W → {mb:.0f}W)")
        c = [p[idx] for p in recent if p[0] > now - 10 and p[idx] is not None]
        if c:
            mc = sum(c) / len(c)
            if abs(mc - mb) > max(STEADY_REL * mb, STEADY_ABS_W):
                return Steady(False, f"{dev} 전력이 지금 바뀌고 있습니다(1분 평균 {mb:.0f}W, 최근 10초 {mc:.0f}W)")
    if len(recent[0]) > 3:
        meds = []
        for k in range(int(seconds // 30)):
            lo = now - seconds + 30 * k
            v = sorted(p[3] for p in recent if lo < p[0] <= lo + 30 and p[3] is not None)
            if v:
                meds.append(v[len(v) // 2])
        if len(meds) >= 2 and max(meds) - min(meds) > STEADY_GPU_TEMP_C:
            return Steady(False, f"GPU 온도가 아직 움직입니다({min(meds):.0f}–{max(meds):.0f}°C)")
    load_w = [p[1] for p in recent if p[0] > now - 60 and p[1] is not None]
    under_load = bool(load_w) and sum(load_w) / len(load_w) >= STEADY_FAN_MIN_W
    if len(recent[0]) > 4 and under_load:
        first = sorted(p[4] for p in recent if p[0] <= now - seconds + 30 and p[4] is not None)
        last = sorted(p[4] for p in recent if p[0] > now - 30 and p[4] is not None)
        if first and last and last[len(last) // 2] - first[len(first) // 2] > STEADY_FAN_RISE:
            return Steady(False, f"팬이 아직 빨라지는 중입니다({first[len(first) // 2]:.0f} → {last[len(last) // 2]:.0f}rpm)")
    return Steady(True, "")


def power_points(rows: Iterable[dict]) -> list[tuple]:
    return [(r["ts"], r["cpu_power"], r["gpu_power"], r["gpu_temp"], r["fan_cpu"]) for r in rows]


def snapshot_from_row(r: dict):
    """기록 한 줄을 다시 Snapshot 으로 (기록 재생·검증용)."""
    from .model import CpuReading, FanReading, GpuReading, PowerReading, Proc, Snapshot
    return Snapshot(
        t=r["ts"],
        cpu=CpuReading(temp=r["cpu_temp"], temp_max=r["cpu_temp_max"], temp_avg=r.get("cpu_temp_avg"),
                       tj_distance=r["cpu_tj_dist"],
                       load=r["cpu_load"], power=r["cpu_power"], clock=r["cpu_clock"], perf_limit=r["cpu_perf_limit"],
                       peak_temp=r["cpu_peak"], thermal_frac=r["cpu_thermal_frac"], prochot_frac=r["cpu_prochot_frac"],
                       limit_frac=json.loads(r["cpu_limits"]) if r["cpu_limits"] else {}),
        gpu=GpuReading(temp=r["gpu_temp"], margin=r["gpu_margin"], load=r["gpu_load"], power=r["gpu_power"],
                       power_limit=r["gpu_power_limit"], clock=r["gpu_clock"], reasons=r["gpu_reasons"] or 0,
                       limit_frac=json.loads(r["gpu_limits"]) if r["gpu_limits"] else {}),
        fans=FanReading(r["fan_cpu"], r["fan_gpu"]),
        power=PowerReading(ac=None if r["ac"] is None else bool(r["ac"]),
                           charging=None if r["charging"] is None else bool(r["charging"]), scheme=r["scheme"]),
        procs=[Proc(n, pid, c, g, bool(b)) for n, pid, c, g, b in json.loads(r["procs"] or "[]")],
    )
