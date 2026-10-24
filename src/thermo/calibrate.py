"""평소 기록으로 CPU 코어 평균 온도 기준을 만든다 (2026-10-24 사용자 결정: 3차 기준 측정 대신).

왜: 기준 측정 #1·#2 에는 코어 평균 온도가 없어 CPU 모델이 패키지 온도(가장 뜨거운 코어)로 되어 있다. 패키지 온도는
한 코어에 몰린 부하에서 같은 전력이라도 8°C 까지 높아 '주의' 오탐을 낸다(M8). 코어 평균은 M8 부터 기록한다.

어떻게: thermo 가 켜져 있던 동안의 1초 기록을 10초 구간으로 보고, 다음을 모두 만족하는 구간만 쓴다.
  - 정상 상태(store.steady_state)이고 95°C 스로틀이 5% 이하, 어댑터 연결
  - **기준 측정의 패키지 모델과 맞는 구간**(잔차 ≤ 2σ) — 지금 냉각이 기준 측정 때와 같다고 확인된 구간만.
    그래서 냉각이 나빠진 상태를 '평소'로 배우지 않는다.
그 구간들로 T_avg = a + b·P_cpu + c·P_gpu 를 맞춘다. 구간이 MIN_BLOCKS 개 미만이거나 CPU 전력 범위가 좁으면 만들지 않는다.

한계: 기준 측정 때의 냉각 상태를 '패키지 모델과 맞음'으로 간접 확인한다. 한 코어 몰림이 심한 구간은 패키지 잔차가 커서
빠지므로, 코어 평균 모델은 몰리지 않은 부하로 만들어진다(코어 평균은 몰림에 덜 흔들리므로 그대로 써도 된다고 본다).
"""

from __future__ import annotations

import json
import time
from collections import deque

from .model import summarize
from .reference import THROTTLE_FRAC, LinearModel, Reference, fit
from .reference import Block
from .store import Store, snapshot_from_row, steady_state

MIN_BLOCKS = 30
MATCH_SIGMA = 2.0
LOOKBACK_DAYS = 14
CACHE_HOURS = 6


def collect_blocks(rows: list[dict], ref: Reference) -> tuple[list[Block], dict]:
    """조건을 만족하는 10초 구간. 두 번째 값은 왜 빠졌는지 세어 둔 것."""
    pkg = ref.cpu_model
    out: list[Block] = []
    why = {"정상 상태 아님": 0, "스로틀": 0, "배터리": 0, "기준과 안 맞음": 0, "코어 평균 없음": 0}
    pts: deque = deque(maxlen=130)
    window: deque = deque(maxlen=10)
    last_t = None
    for i, r in enumerate(rows):
        if last_t is not None and r["ts"] - last_t > 5:      # 기록이 끊긴 곳(창을 껐다 켬)에서 다시 시작
            pts.clear()
            window.clear()
        last_t = r["ts"]
        s = snapshot_from_row(r)
        pts.append((s.t, s.cpu.power, s.gpu.power, s.gpu.temp, s.fans.cpu_rpm))
        window.append(s)
        if len(window) < 10 or i % 10 != 9:
            continue
        if not steady_state(list(pts), s.t).steady:
            why["정상 상태 아님"] += 1
            continue
        w = summarize(list(window))
        if (w.cpu.thermal_frac or 0) > THROTTLE_FRAC:
            why["스로틀"] += 1
            continue
        if w.power.ac is False:
            why["배터리"] += 1
            continue
        if w.cpu.temp_avg is None or w.cpu.temp is None or w.cpu.power is None or w.gpu.power is None:
            why["코어 평균 없음"] += 1
            continue
        p = pkg.predict(cpu_power=w.cpu.power, gpu_power=w.gpu.power)
        if p is None or abs(w.cpu.temp - p) > MATCH_SIGMA * pkg.sigma:
            why["기준과 안 맞음"] += 1
            continue
        out.append(Block("평소", s.t, w.cpu.power, w.gpu.power, w.cpu.temp, w.gpu.temp,
                         w.fans.cpu_rpm, w.fans.gpu_rpm, w.cpu.thermal_frac or 0.0, 0.0, w.cpu.temp_avg))
    return out, why


def core_avg_model(store: Store, ref: Reference, now: float | None = None) -> tuple[LinearModel | None, str]:
    """평소 기록으로 코어 평균 모델을 만든다. 실패하면 (None, 이유)."""
    if ref.cpu_model is None:
        return None, "기준 측정의 CPU 모델이 없습니다"
    if ref.cpu_model.target == "cpu_temp_avg":
        return None, "기준 측정이 이미 코어 평균으로 되어 있습니다"
    now = now or time.time()
    rows = store.db.execute(
        "SELECT * FROM samples WHERE ts >= ? AND cpu_temp_avg IS NOT NULL ORDER BY ts", (now - LOOKBACK_DAYS * 86400,))
    names = [d[0] for d in rows.description]
    blocks, why = collect_blocks([dict(zip(names, r)) for r in rows], ref)
    skipped = ", ".join(f"{k} {v}" for k, v in why.items() if v)
    if len(blocks) < MIN_BLOCKS:
        return None, f"코어 평균 기준을 모으는 중 ({len(blocks)}/{MIN_BLOCKS} 구간" + (f"; 뺀 구간: {skipped})" if skipped else ")")
    m = fit("cpu_temp_avg", ["cpu_power", "gpu_power"], blocks)
    if m is None or "cpu_power" not in m.coef:
        return None, f"코어 평균 기준을 만들 만큼 CPU 전력 범위가 넓지 않습니다 ({len(blocks)} 구간)"
    m.notes.append(f"평소 기록 {len(blocks)}구간으로 만든 코어 평균 기준(기준 측정 패키지 모델과 2σ 안에서 맞는 구간만)")
    return m, m.notes[-1]


def cached_core_avg_model(store: Store, ref: Reference, ref_id: int | None) -> tuple[LinearModel | None, str]:
    """몇 시간에 한 번만 새로 계산한다(meta 표에 남김)."""
    row = store.db.execute("SELECT ts, value FROM meta WHERE key='core_avg_model' ORDER BY ts DESC LIMIT 1").fetchone()
    if row:
        v = json.loads(row[1])
        if v.get("ref_id") == ref_id and time.time() - row[0] < CACHE_HOURS * 3600:
            return (LinearModel(**v["model"]) if v.get("model") else None), v["info"]
    m, info = core_avg_model(store, ref)
    from dataclasses import asdict
    store.note("core_avg_model", {"ref_id": ref_id, "model": asdict(m) if m else None, "info": info})
    return m, info
