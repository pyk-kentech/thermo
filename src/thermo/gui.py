"""창 (DESIGN 2판 8장). 센서는 작업 스레드에서 1초마다 읽고, 판단은 2초마다 한다.

M8: 판단 2판(diagnose)으로 연결. 8장의 '지금 상황/판단/근거' 배치와 기록 그래프·기준 측정 화면은 M9.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import text
from .diagnose import Diagnoser, Diagnosis, load_reference
from .model import Level

FONT = "Malgun Gothic"
COLORS = {Level.OK: "#2e7d32", Level.WARN: "#ef6c00", Level.BAD: "#c62828"}
GREY = "#757575"
REFRESH_EVERY = 2     # 초마다 판단


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
            self.out.put(("reference", label))
            dg = Diagnoser(ref)
            size = col.window.maxlen
            i = 0
            while not self.stop.is_set():
                t0 = time.monotonic()
                snap = col.sample()
                store.add(snap)
                dg.add(snap.t, snap.cpu.power, snap.gpu.power, snap.gpu.temp, snap.fans.cpu_rpm)
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


class App:
    def __init__(self, root: tk.Tk, monitor: Monitor | None) -> None:
        self.root = root
        self.monitor = monitor
        self.queue: queue.Queue = monitor.out if monitor else queue.Queue()
        self.show_evidence = False
        root.title("thermo — 온도 진단")
        root.geometry("520x620")
        root.minsize(420, 460)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.show_measuring(0, 10, [])
        if monitor:
            monitor.start()
        self.root.after(200, self.poll)

    # --- 화면 구성 -------------------------------------------------------
    def _build(self) -> None:
        r = self.root
        r.configure(bg="white")
        top = tk.Frame(r, bg="white", padx=16, pady=12)
        top.pack(fill="x")
        self.level = tk.Label(top, text="", font=(FONT, 18, "bold"), bg="white", anchor="w",
                              justify="left", wraplength=480)
        self.level.pack(fill="x")
        self.summary = tk.Label(top, text="", font=(FONT, 10), bg="white", anchor="w",
                                justify="left", wraplength=480)
        self.summary.pack(fill="x", pady=(4, 0))

        ttk.Separator(r).pack(fill="x", padx=12)

        grid = tk.Frame(r, bg="white", padx=16, pady=10)
        grid.pack(fill="x")
        self.cells: dict[str, list[tk.Label]] = {}
        for col, h in enumerate(("",) + text.HEADER):
            tk.Label(grid, text=h, font=(FONT, 9), fg=GREY, bg="white", width=8,
                     anchor="e" if col else "w").grid(row=0, column=col, sticky="ew")
        for row, dev in enumerate(("CPU", "GPU"), start=1):
            tk.Label(grid, text=dev, font=(FONT, 11, "bold"), bg="white", anchor="w"
                     ).grid(row=row, column=0, sticky="w")
            self.cells[dev] = []
            for col in range(1, 5):
                lab = tk.Label(grid, text="–", font=(FONT, 11), bg="white", anchor="e")
                lab.grid(row=row, column=col, sticky="ew")
                self.cells[dev].append(lab)
        self.fans = self._line(grid, 3, "팬")
        self.power = self._line(grid, 4, "전원")
        self.baseline = tk.Label(r, text="", font=(FONT, 9), fg=GREY, bg="white", anchor="w", padx=16)
        self.baseline.pack(fill="x")

        ttk.Separator(r).pack(fill="x", padx=12, pady=(8, 0))

        self.ev_button = tk.Button(r, text="근거 ▸", font=(FONT, 10), bg="white", relief="flat",
                                   anchor="w", padx=16, command=self.toggle_evidence)
        self.ev_button.pack(fill="x")
        self.evidence = tk.Label(r, text="", font=(FONT, 9), bg="white", anchor="nw",
                                 justify="left", wraplength=480, padx=24)
        self.notes = tk.Label(r, text="", font=(FONT, 9), fg="#8d6e63", bg="white", anchor="w",
                              justify="left", wraplength=480, padx=16, pady=8)
        self.notes.pack(side="bottom", fill="x")

    def _line(self, grid: tk.Frame, row: int, name: str) -> tk.Label:
        tk.Label(grid, text=name, font=(FONT, 10), fg=GREY, bg="white", anchor="w"
                 ).grid(row=row, column=0, sticky="w", pady=(6, 0))
        lab = tk.Label(grid, text="–", font=(FONT, 10), bg="white", anchor="w")
        lab.grid(row=row, column=1, columnspan=4, sticky="w", pady=(6, 0))
        return lab

    def toggle_evidence(self) -> None:
        self.show_evidence = not self.show_evidence
        self.ev_button.config(text="근거 ▾" if self.show_evidence else "근거 ▸")
        if self.show_evidence:
            self.evidence.pack(fill="x", after=self.ev_button)
        else:
            self.evidence.pack_forget()

    # --- 갱신 ------------------------------------------------------------
    def show_measuring(self, n: int, size: int, notes: list[str]) -> None:
        self.level.config(text="● 측정 중…", fg=GREY)
        self.summary.config(text=f"{size}초 동안 잰 뒤 판단합니다 ({n}/{size}). "
                                 "기준과의 비교는 부하가 2분 동안 안정돼야 합니다.")
        self._notes(notes)

    def show_verdict(self, d: Diagnosis) -> None:
        self.level.config(text=f"● {d.headline}", fg=COLORS[d.level])
        self.summary.config(text="\n".join(f"→ {j}" for j in d.judgement))
        for dev, values in text.device_cells(d.stats).items():
            for lab, val in zip(self.cells[dev], values):
                lab.config(text=val)
        self.fans.config(text=text.fan_line(d.stats))
        self.power.config(text=text.power_line(d.stats))
        lines = d.situation[:1] + d.evidence
        self.evidence.config(text="\n".join(f"· {e}" for e in lines) or "· 비교한 값 없음")
        self._notes(d.notes)

    def show_reference(self, label: str | None) -> None:
        self.baseline.config(text=f"기준: {label}" if label else "기준 측정 전 — 하드웨어 사실만 판단합니다")

    def show_error(self, msg: str) -> None:
        self.level.config(text="● 오류", fg=COLORS[Level.BAD])
        self.summary.config(text=f"센서를 읽다가 멈췄습니다. 창을 닫고 다시 켜 주세요.\n{msg}")

    def _notes(self, notes: list[str]) -> None:
        self.notes.config(text="\n".join(f"※ {n}" for n in notes))

    def poll(self) -> None:
        msgs = []
        try:
            while True:
                msgs.append(self.queue.get_nowait())
        except queue.Empty:
            pass
        for kind, *rest in msgs:
            if kind == "reference":          # 한 번만 오므로 놓치지 않게 따로 처리
                self.show_reference(rest[0])
        latest = next((m for m in reversed(msgs) if m[0] != "reference"), None)
        if latest is not None:
            kind, *rest = latest
            if kind == "measuring":
                self.show_measuring(*rest)
            elif kind == "verdict":
                self.show_verdict(rest[0])
            elif kind == "error":
                self.show_error(rest[0])
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
