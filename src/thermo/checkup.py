"""기준 측정("건강검진") 진행 (DESIGN 2판 7장).

단계마다 부하를 걸고, 정상 상태(전력·온도가 60초 동안 안정)에 닿으면 60초 더 재서 그 구간을 기준으로 쓴다.
정상 상태에 못 닿고 단계 최대 시간이 지나면 마지막 60초를 남기되 '정상 상태 아님'으로 표시해 모델에서 뺀다.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from . import reference
from .load import CpuLoad
from .model import Snapshot
from .store import Store, steady_state

HOLD_SECONDS = 60
TEMP_STEADY_C = 1.0          # 최근 30초와 그 앞 30초의 온도 중앙값 차
GAME_MIN_GPU_W = 25.0        # 게임이 켜졌다고 볼 GPU 전력
GAME_WAIT_SECONDS = 120


@dataclass
class Stage:
    name: str
    title: str
    threads: int
    game: bool
    max_seconds: int
    fixed: bool = False      # 정상 상태를 기다리지 않고 정해진 시간만 잰다(식힘)


PLAN = [
    Stage("idle", "공회전", 0, False, 600),
    Stage("cpu4", "CPU 4스레드", 4, False, 300),
    Stage("cpu8", "CPU 8스레드", 8, False, 300),
    Stage("cpu16", "CPU 16스레드", 16, False, 300),
    Stage("gpu", "게임(GPU)", 0, True, 360),
    Stage("both", "게임 + CPU 16스레드", 16, True, 300),
    Stage("cool", "식힘", 0, False, 180, fixed=True),
]


class UI(Protocol):
    def say(self, text: str) -> None: ...
    def ask(self, text: str) -> bool: ...   # Enter 면 True, 그만두면 False


class Collector(Protocol):
    def sample(self) -> Snapshot: ...


class Stopped(Exception):
    pass


def temp_steady(points: list[tuple[float, float | None, float | None]], now: float) -> bool:
    """(시각, CPU°C, GPU°C): 최근 30초와 그 앞 30초의 중앙값 차가 장치마다 1°C 이하."""
    for idx in (1, 2):
        a = [p[idx] for p in points if now - 30 < p[0] <= now and p[idx] is not None]
        b = [p[idx] for p in points if now - 60 < p[0] <= now - 30 and p[idx] is not None]
        if len(a) < 20 or len(b) < 20:
            return False
        if abs(statistics.median(a) - statistics.median(b)) > TEMP_STEADY_C:
            return False
    return True


class Checkup:
    def __init__(self, col: Collector, store: Store, mode: str, room: float | None, ui: UI,
                 plan: list[Stage] = PLAN, load: Callable[[int], CpuLoad] = CpuLoad,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
                 hold_seconds: int = HOLD_SECONDS) -> None:
        self.col, self.store, self.mode, self.room, self.ui = col, store, mode, room, ui
        self.plan, self.load, self.sleep, self.monotonic = plan, load, sleep, monotonic
        self.hold_seconds = hold_seconds
        self.stage_rows: list[tuple[str, list[dict], bool]] = []

    # --- 1초 표본 --------------------------------------------------------------
    def _tick(self) -> Snapshot:
        t0 = self.monotonic()
        s = self.col.sample()
        self.store.add(s)
        self.sleep(max(0.0, 1.0 - (self.monotonic() - t0)))
        return s

    def _safety(self, s: Snapshot) -> str | None:
        if s.gpu.margin is not None and s.gpu.margin <= 0:
            return f"GPU 가 한계 온도에 닿았습니다(여유 {s.gpu.margin:.0f}°C)"
        if (s.cpu.prochot_frac or 0) > 0.5 and (s.cpu.temp or 0) < 90:
            return "CPU 밖에서 속도를 묶는 신호(PROCHOT)가 계속 켜져 있습니다"
        return None

    # --- 단계 ------------------------------------------------------------------
    def _wait_game(self) -> None:
        if not self.ui.ask("게임을 켜고 정해 둔 장면에 들어간 뒤 Enter 를 누르세요 (그만두려면 q)"):
            raise Stopped("사용자가 그만뒀습니다")
        for _ in range(GAME_WAIT_SECONDS):
            s = self._tick()
            if (s.gpu.power or 0) >= GAME_MIN_GPU_W:
                self.ui.say(f"게임 부하 확인: GPU {s.gpu.power:.0f}W")
                return
        self.ui.say(f"※ {GAME_WAIT_SECONDS}초 동안 GPU 전력이 {GAME_MIN_GPU_W:.0f}W 를 넘지 않았습니다. 그대로 잽니다.")

    def _run_stage(self, st: Stage, cid: int) -> None:
        self.ui.say(f"[{st.title}] 시작" + (f" — CPU {st.threads}스레드 부하" if st.threads else ""))
        load = self.load(st.threads).start() if st.threads else None
        pts: list[tuple[float, float | None, float | None]] = []
        temps: list[tuple[float, float | None, float | None]] = []
        start = hold_start = None
        steady = False
        note = ""
        try:
            for i in range(st.max_seconds):
                s = self._tick()
                start = start or s.t
                pts.append((s.t, s.cpu.power, s.gpu.power))
                temps.append((s.t, s.cpu.temp, s.gpu.temp))
                bad = self._safety(s)
                if bad:
                    note = f"안전 중단: {bad}"
                    self.ui.say(f"※ {note}. 이 단계를 끝냅니다.")
                    break
                if i % 15 == 0:
                    self.ui.say(f"  {i}초 CPU {s.cpu.power or 0:.0f}W {s.cpu.temp or 0:.0f}°C · "
                                f"GPU {s.gpu.power or 0:.0f}W {s.gpu.temp or 0:.0f}°C · "
                                f"팬 {s.fans.cpu_rpm or 0:.0f}/{s.fans.gpu_rpm or 0:.0f}"
                                + (" · 안정, 기준 재는 중" if hold_start else ""))
                if st.fixed:
                    continue
                if hold_start is None and steady_state(pts, s.t).steady and temp_steady(temps, s.t):
                    hold_start = s.t
                    self.ui.say(f"  정상 상태 도달 ({i}초). {self.hold_seconds}초 더 잽니다.")
                if hold_start is not None and s.t - hold_start >= self.hold_seconds - 1:
                    steady = True
                    break
        finally:
            if load:
                load.stop()
        end = pts[-1][0] if pts else start
        if st.fixed:
            hold_start, steady = start, False
        elif not steady:
            hold_start = max(start, end - self.hold_seconds + 1)
            note = note or f"{st.max_seconds}초 안에 정상 상태에 닿지 못했습니다"
            self.ui.say(f"  ※ {note}")
        self.store.db.commit()
        self.store.checkup_stage(cid, st.name, start, hold_start, end, steady, note)
        if st.name != "cool":
            self.stage_rows.append((st.name, self.store.rows(hold_start, end + 0.5), steady))

    def run(self) -> reference.Reference | None:
        cid = self.store.checkup_begin(self.mode, self.room)
        self.ui.say(f"기준 측정을 시작합니다 (성능 모드: {self.mode}). 약 20–25분 걸립니다. Ctrl+C 로 언제든 멈춥니다.")
        try:
            s = self._tick()
            if s.power.ac is False:
                if not self.ui.ask("어댑터가 빠져 있습니다. 연결한 뒤 Enter (그만두려면 q)"):
                    raise Stopped("어댑터 없음")
            busy = [p for p in s.procs if p.cpu >= 5]
            if busy:
                names = ", ".join(f"{p.name} {p.cpu:.0f}%" for p in busy[:3])
                if not self.ui.ask(f"CPU 를 쓰는 프로그램이 있습니다({names}). 끄고 Enter, 그대로 하려면 Enter (그만두려면 q)"):
                    raise Stopped("사용자가 그만뒀습니다")
            game_on = False
            for st in self.plan:
                if st.game and not game_on:
                    self._wait_game()
                    game_on = True
                if not st.game and game_on:
                    if not self.ui.ask("게임을 끈 뒤 Enter 를 누르세요"):
                        raise Stopped("사용자가 그만뒀습니다")
                    game_on = False
                self._run_stage(st, cid)
            ref = reference.build(self.mode, self.room, self.stage_rows)
            self.store.checkup_end(cid, "완료", ref.to_json())
            self.ui.say("기준 측정을 마쳤습니다.")
            for line in reference.summary(ref):
                self.ui.say(line)
            return ref
        except (Stopped, KeyboardInterrupt) as e:
            reason = str(e) or "Ctrl+C"
            self.store.checkup_end(cid, f"중단: {reason}", None)
            self.ui.say(f"기준 측정을 멈췄습니다: {reason}")
            return None


class ConsoleUI:
    """콘솔 창에서 안내하고, 같은 내용을 로그 파일에도 쓴다."""

    def __init__(self, log_path=None) -> None:
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    def say(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        print(line, flush=True)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def ask(self, text: str) -> bool:
        self.say(f"▶ {text}")
        try:
            ans = input("> ").strip().lower()
        except EOFError:
            return False
        self.say(f"  (입력: {ans or 'Enter'})")
        return ans != "q"
