"""창 (DESIGN 2판 8장). 센서는 작업 스레드에서 1초마다 읽고, 판단은 2초마다 한다.

배치: 판정 제목 → 지금 상황 → 판단 → [근거 | 기록 | 기준 측정] 탭 → 안내(※).
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk
from typing import Callable

from .diagnose import Diagnoser, Diagnosis, load_reference
from .model import Level
from .reference import Reference, summary

FONT = "Malgun Gothic"
COLORS = {Level.OK: "#2e7d32", Level.WARN: "#ef6c00", Level.BAD: "#c62828"}
GREY = "#757575"
REFRESH_EVERY = 2          # 초마다 판단
HISTORY_SECONDS = 600      # 기록 그래프: 최근 10분
MODES = ("Performance", "Silent", "Turbo", "Windows", "Manual")

# 그래프 선: (이름, 색, 표본 튜플 안 위치)
TEMP_LINES = (("CPU 패키지", "#c62828", 1), ("CPU 코어 평균", "#ef9a9a", 2), ("GPU", "#1565c0", 4))
POWER_LINES = (("CPU W", "#c62828", 3), ("GPU W", "#1565c0", 5))
FAN_LINE = ("CPU 팬 rpm", "#2e7d32", 6)


def sample_tuple(s) -> tuple:
    """그래프용: (시각, CPU 패키지 °C, CPU 코어 평균 °C, CPU W, GPU °C, GPU W, CPU 팬 rpm)."""
    return (s.t, s.cpu.temp, s.cpu.temp_avg, s.cpu.power, s.gpu.temp, s.gpu.power, s.fans.cpu_rpm)


class Monitor(threading.Thread):
    """센서 읽기·1초 기록·판단. 결과는 큐로 화면에 넘긴다.

    Collector·Store 는 이 스레드 안에서 만든다(sqlite 연결은 만든 스레드에서만 쓸 수 있다).
    """

    def __init__(self, out: queue.Queue, make_collector: Callable, make_store: Callable,
                 interval: float = 1.0) -> None:
        super().__init__(daemon=True, name="thermo-monitor")
        self.out = out
        self.make_collector = make_collector
        self.make_store = make_store
        self.interval = interval
        self.stop = threading.Event()

    def run(self) -> None:
        col = store = None
        try:
            col = self.make_collector()
            store = self.make_store()
            if getattr(col, "fast", None) is not None:
                store.note("cpu_limits", vars(col.fast.limits))
            ref, label = load_reference(store)
            avg_model, avg_info = None, None
            if ref is not None and hasattr(store, "rows"):
                from .calibrate import cached_core_avg_model
                d = store.latest_checkup()
                avg_model, avg_info = cached_core_avg_model(store, ref, d["id"] if d else None)
            self.out.put(("reference", ref, label, avg_info))
            if hasattr(store, "rows"):     # 창을 다시 켰을 때 최근 10분을 그래프에 이어 그린다
                from .store import snapshot_from_row
                rows = store.rows(time.time() - HISTORY_SECONDS)
                self.out.put(("history", [sample_tuple(snapshot_from_row(r)) for r in rows]))
            dg = Diagnoser(ref, avg_model=avg_model)
            size = col.window.maxlen
            i = 0
            while not self.stop.is_set():
                t0 = time.monotonic()
                snap = col.sample()
                store.add(snap)
                dg.add(snap.t, snap.cpu.power, snap.gpu.power, snap.gpu.temp, snap.fans.cpu_rpm)
                self.out.put(("sample", sample_tuple(snap)))
                i += 1
                if len(col.window) < size:
                    self.out.put(("measuring", len(col.window), size, list(col.notes)))
                elif i % REFRESH_EVERY == 0:
                    self.out.put(("verdict", dg.diagnose(col.stats(), snap.t)))
                self.stop.wait(max(0.0, self.interval - (time.monotonic() - t0)))
        except Exception as e:  # 화면에 알리고 멈춘다
            self.out.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            for x in (store, col):
                if x is not None:
                    try:
                        x.close()
                    except Exception:
                        pass


def launch_checkup(mode: str) -> None:
    """기준 측정을 새 콘솔 창에서 시작한다(이 창은 이미 관리자 권한이라 그대로 물려받는다)."""
    exe = sys.executable.replace("pythonw.exe", "python.exe")
    subprocess.Popen([exe, "-m", "thermo", "--checkup", mode], creationflags=subprocess.CREATE_NEW_CONSOLE)


class App:
    def __init__(self, root: tk.Tk, monitor: Monitor | None,
                 start_checkup: Callable[[str], None] = launch_checkup) -> None:
        self.root = root
        self.monitor = monitor
        self.start_checkup = start_checkup
        self.queue: queue.Queue = monitor.out if monitor else queue.Queue()
        self.history: deque[tuple] = deque(maxlen=HISTORY_SECONDS)
        self.ref: Reference | None = None
        root.title("thermo — 온도 진단")
        root.geometry("600x760")
        root.minsize(480, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.show_measuring(0, 10, [])
        if monitor:
            monitor.start()
        self.root.after(200, self.poll)

    # --- 화면 구성 -------------------------------------------------------
    def _section(self, parent, title: str) -> tk.Label:
        tk.Label(parent, text=title, font=(FONT, 9, "bold"), fg=GREY, bg="white", anchor="w"
                 ).pack(fill="x", padx=16, pady=(8, 0))
        lab = tk.Label(parent, text="", font=(FONT, 10), bg="white", anchor="w", justify="left", wraplength=560)
        lab.pack(fill="x", padx=24)
        return lab

    def _build(self) -> None:
        r = self.root
        r.configure(bg="white")
        self.level = tk.Label(r, text="", font=(FONT, 16, "bold"), bg="white", anchor="w",
                              justify="left", wraplength=560, padx=16, pady=10)
        self.level.pack(fill="x")
        ttk.Separator(r).pack(fill="x", padx=12)
        self.situation = self._section(r, "지금 상황")
        self.judgement = self._section(r, "판단")
        self.notes = tk.Label(r, text="", font=(FONT, 9), fg="#8d6e63", bg="white", anchor="w",
                              justify="left", wraplength=560, padx=16, pady=6)
        self.notes.pack(side="bottom", fill="x")

        self.tabs = ttk.Notebook(r)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(10, 0))
        ev = tk.Frame(self.tabs, bg="white")
        self.evidence = tk.Label(ev, text="", font=(FONT, 9), bg="white", anchor="nw", justify="left",
                                 wraplength=540, padx=8, pady=8)
        self.evidence.pack(fill="both", expand=True)
        self.tabs.add(ev, text="근거")

        rec = tk.Frame(self.tabs, bg="white")
        self.graph = tk.Canvas(rec, bg="white", highlightthickness=0, height=300)
        self.graph.pack(fill="both", expand=True)
        self.graph.bind("<Configure>", lambda e: self.draw_graph())
        self.tabs.add(rec, text="기록 (10분)")

        refp = tk.Frame(self.tabs, bg="white")
        self.ref_label = tk.Label(refp, text="", font=(FONT, 9), fg=GREY, bg="white", anchor="w",
                                  justify="left", wraplength=540, padx=8, pady=4)
        self.ref_label.pack(fill="x")
        self.ref_text = tk.Text(refp, font=("Consolas", 9), height=12, wrap="none", relief="flat", bg="#fafafa")
        self.ref_text.pack(fill="both", expand=True, padx=8)
        bar = tk.Frame(refp, bg="white")
        bar.pack(fill="x", padx=8, pady=6)
        tk.Label(bar, text="지금 성능 모드:", font=(FONT, 9), bg="white").pack(side="left")
        self.mode = tk.StringVar(value="Performance")
        ttk.Combobox(bar, textvariable=self.mode, values=MODES, width=12, state="readonly").pack(side="left", padx=4)
        self.checkup_button = ttk.Button(bar, text="기준 측정 시작…", command=self.ask_checkup)
        self.checkup_button.pack(side="right")
        self.tabs.add(refp, text="기준 측정")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.draw_graph())
        self._wrapped = [self.level, self.situation, self.judgement, self.notes, self.evidence, self.ref_label]
        r.bind("<Configure>", self._rewrap)

    def _rewrap(self, event=None) -> None:
        """창 폭이 바뀌면 줄바꿈 폭도 맞춘다(긴 프로그램 줄이 잘리지 않게)."""
        w = max(self.root.winfo_width() - 60, 300)
        for lab in self._wrapped:
            lab.config(wraplength=w)

    # --- 갱신 ------------------------------------------------------------
    def show_measuring(self, n: int, size: int, notes: list[str]) -> None:
        self.level.config(text="● 측정 중…", fg=GREY)
        self.situation.config(text=f"{size}초 동안 잰 뒤 판단합니다 ({n}/{size}).")
        self.judgement.config(text="기준과의 비교는 부하가 2분 동안 안정돼야 합니다.")
        self._notes(notes)

    def show_verdict(self, d: Diagnosis) -> None:
        self.level.config(text=f"● {d.headline}", fg=COLORS[d.level])
        self.situation.config(text="\n".join(d.situation))
        self.judgement.config(text="\n".join(f"→ {j}" for j in d.judgement))
        self.evidence.config(text="\n".join(f"· {e}" for e in d.evidence) or "· 기준과 비교한 값이 아직 없습니다")
        self._notes(d.notes)

    def show_reference(self, ref: Reference | None, label: str | None, avg_info: str | None = None) -> None:
        self.ref = ref
        self.ref_text.config(state="normal")
        self.ref_text.delete("1.0", "end")
        if ref is None:
            self.ref_label.config(text="기준 측정 전입니다. 기준이 없으면 '평소보다 뜨거운가'는 판단하지 못하고 "
                                       "하드웨어가 알려 준 사실만 봅니다.")
        else:
            self.ref_label.config(text=f"지금 기준: {label}. 같은 모드에서 다시 재면 첫 결과와 비교할 수 있습니다.")
            lines = summary(ref)
            if avg_info:
                lines += ["", f"코어 평균 기준: {avg_info}"]
            self.ref_text.insert("1.0", "\n".join(lines))
            self.mode.set(ref.mode)
        self.ref_text.config(state="disabled")

    def ask_checkup(self) -> None:
        mode = self.mode.get()
        ok = messagebox.askokcancel(
            "기준 측정",
            f"성능 모드 '{mode}' 로 기준 측정을 시작합니다(약 25분).\n\n"
            "· 게임·무거운 프로그램을 끄고, 어댑터를 연결하고, 평평한 책상에 두세요.\n"
            "· 새 콘솔 창의 안내를 따르세요(중간에 게임을 켜고 끄는 안내가 나옵니다).\n"
            "· 측정을 흐리지 않도록 이 창은 닫힙니다.", parent=self.root)
        if ok:
            self.start_checkup(mode)
            self.close()

    def show_error(self, msg: str) -> None:
        self.level.config(text="● 오류", fg=COLORS[Level.BAD])
        self.judgement.config(text=f"센서를 읽다가 멈췄습니다. 창을 닫고 다시 켜 주세요.\n{msg}")

    def _notes(self, notes: list[str]) -> None:
        self.notes.config(text="\n".join(f"※ {n}" for n in notes))

    # --- 기록 그래프 ---------------------------------------------------------
    def draw_graph(self) -> None:
        c = self.graph
        if not c.winfo_ismapped():
            return
        c.delete("all")
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        pts = list(self.history)
        if len(pts) < 2:
            c.create_text(w / 2, h / 2, text="기록이 쌓이는 중입니다", fill=GREY, font=(FONT, 10))
            return
        left, right, gap = 44, w - 50, 18
        top_h = (h - 3 * gap) * 0.55
        boxes = ((gap, gap + top_h), (2 * gap + top_h, h - gap))
        t1 = pts[-1][0]
        t0 = t1 - HISTORY_SECONDS

        def x(t):
            return left + (right - left) * (t - t0) / HISTORY_SECONDS

        def plot(box, lo, hi, idx, color, scale=1.0, width=1.5):
            y0, y1 = box
            prev = None
            for p in pts:
                v = p[idx]
                if v is None or p[0] < t0:
                    prev = None
                    continue
                v = v * scale
                y = y1 - (y1 - y0) * (min(max(v, lo), hi) - lo) / (hi - lo)
                if prev:
                    c.create_line(prev[0], prev[1], x(p[0]), y, fill=color, width=width)
                prev = (x(p[0]), y)

        # 위: 온도 30–100°C, 95°C 선
        (ty0, ty1) = boxes[0]
        c.create_rectangle(left, ty0, right, ty1, outline="#e0e0e0")
        for v in (40, 60, 80, 95):
            y = ty1 - (ty1 - ty0) * (v - 30) / 70
            c.create_line(left, y, right, y, fill="#ffcdd2" if v == 95 else "#f0f0f0", dash=(3, 2) if v == 95 else ())
            c.create_text(left - 4, y, text=f"{v}°", anchor="e", fill=GREY, font=(FONT, 8))
        for name, color, idx in TEMP_LINES:
            plot(boxes[0], 30, 100, idx, color)
        # 아래: 전력 0–60W(왼쪽), 팬 0–6000rpm(오른쪽)
        (py0, py1) = boxes[1]
        c.create_rectangle(left, py0, right, py1, outline="#e0e0e0")
        for v in (0, 20, 40, 60):
            y = py1 - (py1 - py0) * v / 60
            c.create_text(left - 4, y, text=f"{v}W", anchor="e", fill=GREY, font=(FONT, 8))
            c.create_text(right + 4, y, text=f"{v * 100:.0f}", anchor="w", fill="#2e7d32", font=(FONT, 8))
        for name, color, idx in POWER_LINES:
            plot(boxes[1], 0, 60, idx, color)
        plot(boxes[1], 0, 60, FAN_LINE[2], FAN_LINE[1], scale=0.01, width=1)
        # 범례와 시간 축
        lx = left
        for name, color, _ in TEMP_LINES + POWER_LINES + (FAN_LINE,):
            c.create_line(lx, h - 6, lx + 12, h - 6, fill=color, width=2)
            item = c.create_text(lx + 15, h - 6, text=name, anchor="w", fill=GREY, font=(FONT, 8))
            lx = c.bbox(item)[2] + 10
        c.create_text(right, ty0 - 9, text="지금", anchor="e", fill=GREY, font=(FONT, 8))
        c.create_text(left, ty0 - 9, text="10분 전", anchor="w", fill=GREY, font=(FONT, 8))

    # --- 큐 ------------------------------------------------------------------
    def poll(self) -> None:
        msgs = []
        try:
            while True:
                msgs.append(self.queue.get_nowait())
        except queue.Empty:
            pass
        latest = None
        new_points = False
        for kind, *rest in msgs:
            if kind == "reference":
                self.show_reference(*rest)
            elif kind == "history":
                self.history.extend(rest[0])
                new_points = True
            elif kind == "sample":
                self.history.append(rest[0])
                new_points = True
            else:
                latest = (kind, rest)
        if latest is not None:
            kind, rest = latest
            if kind == "measuring":
                self.show_measuring(*rest)
            elif kind == "verdict":
                self.show_verdict(rest[0])
            elif kind == "error":
                self.show_error(rest[0])
        if new_points:
            self.draw_graph()
        self.root.after(200, self.poll)

    def close(self) -> None:
        if self.monitor:
            self.monitor.stop.set()
            self.monitor.join(timeout=3)
        self.root.destroy()


def run() -> None:
    from .collect import Collector
    from .store import Store
    root = tk.Tk()
    App(root, Monitor(queue.Queue(), Collector, Store))
    root.mainloop()
