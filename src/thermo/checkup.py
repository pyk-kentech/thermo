"""기준 측정("건강검진") 진행 (DESIGN 2판 7장).

단계마다 부하를 걸고, 정상 상태(store.steady_state: 120초 동안 전력 평균·GPU 온도가 움직이지 않음)에 닿으면
60초 더 재서 그 구간을 기준으로 쓴다.
정상 상태에 못 닿고 단계 최대 시간이 지나면 마지막 60초를 남기되 '정상 상태 아님'으로 표시해 모델에서 뺀다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from . import reference
from .load import CpuLoad
from .model import Snapshot
from .store import Store, steady_state

HOLD_SECONDS = 60
GAME_MIN_GPU_W = 25.0        # 게임이 켜졌다고 볼 GPU 전력
GAME_WAIT_SECONDS = 120
PRECHECK_SECONDS = 6
BUSY_CPU = 3.0               # 사전 확인: 이 이상 CPU 를 쓰는 프로그램 (전체 대비 %)
BUSY_GPU = 5.0               # 사전 확인: 이 이상 GPU 를 쓰는 프로그램
IDLE_GPU_W = 8.0             # 공회전 때 dGPU 전력은 3W 안팎(M7 1차) — 이보다 크면 무언가 그래픽카드를 쓰는 중


@dataclass
class Stage:
    name: str
    title: str
    threads: int
    game: bool
    max_seconds: int
    fixed: bool = False      # 정상 상태를 기다리지 않고 정해진 시간만 잰다(식힘)


# M7 첫 측정에서 CPU 4스레드(37W)부터 이미 95°C 에 묶였다 → 모델에 쓸 수 있는(묶이지 않은) 점을 얻으려고
# 1·2·3스레드로 바꾸고, 지속 한도를 보는 16스레드만 남긴다.
PLAN = [
    Stage("idle", "공회전", 0, False, 600),
    Stage("cpu1", "CPU 1스레드", 1, False, 300),
    Stage("cpu2", "CPU 2스레드", 2, False, 300),
    Stage("cpu3", "CPU 3스레드", 3, False, 300),
    Stage("cpu16", "CPU 16스레드", 16, False, 300),
    Stage("gpu", "게임(GPU)", 0, True, 360),
    Stage("both", "게임 + CPU 16스레드", 16, True, 420),   # 첫 측정에서 300초로는 GPU 온도가 아직 오르는 중이었다
    Stage("cool", "식힘", 0, False, 180, fixed=True),
]


class UI(Protocol):
    def say(self, text: str) -> None: ...
    def ask(self, text: str) -> bool: ...   # Enter 면 True, 그만두면 False


class Collector(Protocol):
    def sample(self) -> Snapshot: ...


class Stopped(Exception):
    pass


def busy_programs(snaps: list[Snapshot]) -> list[str]:
    """여러 표본 평균으로 CPU·GPU 를 쓰는 프로그램 (이름 사용률)."""
    if not snaps:
        return []
    cpu: dict[str, float] = {}
    gpu: dict[str, float] = {}
    for s in snaps:
        for p in s.procs:
            if p.name.lower() in ("python.exe", "pythonw.exe"):
                continue
            cpu[p.name] = cpu.get(p.name, 0) + p.cpu / len(snaps)
            gpu[p.name] = gpu.get(p.name, 0) + p.gpu / len(snaps)
    out = [f"{n} CPU {v:.0f}%" for n, v in sorted(cpu.items(), key=lambda x: -x[1]) if v >= BUSY_CPU]
    out += [f"{n} GPU {v:.0f}%" for n, v in sorted(gpu.items(), key=lambda x: -x[1]) if v >= BUSY_GPU]
    return out[:4]


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
        pts: list[tuple] = []
        start = hold_start = None
        steady = False
        note = ""
        try:
            for i in range(st.max_seconds):
                s = self._tick()
                start = start or s.t
                pts.append((s.t, s.cpu.power, s.gpu.power, s.gpu.temp))
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
                if hold_start is None and steady_state(pts, s.t).steady:
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
            # 프로세스 사용률은 두 번째 표본부터 나온다(psutil). 2차 측정에서 첫 표본만 봐서 켜져 있던 게임을
            # 놓쳤다 → 몇 초 재서 평균으로 본다.
            snaps = [self._tick() for _ in range(PRECHECK_SECONDS)]
            if snaps[-1].power.ac is False:
                if not self.ui.ask("어댑터가 빠져 있습니다. 연결한 뒤 Enter (그만두려면 q)"):
                    raise Stopped("어댑터 없음")
            busy = busy_programs(snaps[1:])
            gpu_w = [s.gpu.power for s in snaps[1:] if s.gpu.power is not None]
            if busy or (gpu_w and min(gpu_w) > IDLE_GPU_W):
                what = ", ".join(busy) or f"그래픽카드 {min(gpu_w):.0f}W 사용 중"
                if not self.ui.ask(f"다른 프로그램이 돌고 있습니다({what}). 게임·무거운 프로그램을 끄고 Enter, "
                                   "그대로 하려면 Enter (그만두려면 q)"):
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


def stage_holds(store: Store, cid: int, hold_seconds: int = HOLD_SECONDS) -> list[tuple[str, list[dict], bool]]:
    """저장된 기준 측정 기록에 지금의 정상 상태 기준을 다시 적용해 단계마다 '유지' 구간을 다시 고른다."""
    out = []
    for s in store.checkup_stages(cid):
        if s["stage"] == "cool":
            continue
        rows = store.rows(s["start"], s["end"] + 0.5)
        pts = [(r["ts"], r["cpu_power"], r["gpu_power"], r["gpu_temp"]) for r in rows]
        hold = next((i for i in range(len(rows)) if steady_state(pts[:i + 1], rows[i]["ts"]).steady), None)
        if hold is not None and hold + hold_seconds <= len(rows):
            out.append((s["stage"], rows[hold:hold + hold_seconds], True))
        else:
            out.append((s["stage"], rows[-hold_seconds:], False))
    return out


def rebuild(store: Store, ids: list[int], mode: str) -> reference.Reference:
    """여러 기준 측정 기록을 합쳐 기준을 다시 만들고 새 기록으로 저장한다(원래 기록은 그대로 둔다).

    모델은 전력과 온도의 짝으로 맞추므로, 무엇이 그 전력을 냈는지(측정 단계 이름)와 상관없이 같은 모드의
    정상 상태 자료는 합칠 수 있다.
    """
    stages = []
    for cid in ids:
        stages += [(f"{name}#{cid}", rows, steady) for name, rows, steady in stage_holds(store, cid)]
    ref = reference.build(mode, None, stages)
    ref.notes.insert(0, f"기준 측정 {', '.join(f'#{i}' for i in ids)} 기록을 합쳐 다시 계산했습니다")
    cid = store.checkup_begin(mode, None)
    store.checkup_end(cid, "완료", ref.to_json())
    return ref
