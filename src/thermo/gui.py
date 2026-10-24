"""작은 창 (DESIGN 8장). 센서는 작업 스레드에서 1초마다 읽고, 화면은 2초마다 바꾼다."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import rules, text
from .model import Level, Verdict

FONT = "Malgun Gothic"
COLORS = {Level.OK: "#2e7d32", Level.WARN: "#ef6c00", Level.BAD: "#c62828"}
GREY = "#757575"
REFRESH_EVERY = 2     # 초마다 판정
RECORD_EVERY = 10     # 초마다 평소값 기록 (창 하나 = 표본 하나, DESIGN 6.3)


class Monitor(threading.Thread):
    """센서 읽기·판정·평소값 기록. 결과는 큐로 화면에 넘긴다.

    Collector·Baseline 은 이 스레드 안에서 만든다(sqlite 연결은 만든 스레드에서만 쓸 수 있다).
    """

    def __init__(self, out: queue.Queue, make_collector: Callable, make_baseline: Callable,
                 interval: float = 1.0) -> None:
        super().__init__(daemon=True, name="thermo-monitor")
        self.out = out
        self.make_collector = make_collector
        self.make_baseline = make_baseline
        self.interval = interval
        self.stop = threading.Event()

    def run(self) -> None:
        col = base = None
        try:
            col = self.make_collector()
            base = self.make_baseline()
            fan_max = base.fan_max()
            size = col.window.maxlen
            i = last_record = 0
            while not self.stop.is_set():
                t0 = time.monotonic()
                col.sample()
                i += 1
                if len(col.window) < size:
                    self.out.put(("measuring", len(col.window), size, list(col.notes)))
                elif i % REFRESH_EVERY == 0:
                    v = rules.diagnose(col.stats(), base.lookup, fan_max)
                    if i - last_record >= RECORD_EVERY:
                        base.record(v)
                        fan_max = base.fan_max()
                        last_record = i
                    self.out.put(("verdict", v))
                self.stop.wait(max(0.0, self.interval - (time.monotonic() - t0)))
        except Exception as e:  # 화면에 알리고 멈춘다
            self.out.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            for x in (base, col):
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
        root.geometry("460x560")
        root.minsize(400, 420)
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
        self.level = tk.Label(top, text="", font=(FONT, 22, "bold"), bg="white", anchor="w")
        self.level.pack(fill="x")
        self.summary = tk.Label(top, text="", font=(FONT, 11), bg="white", anchor="w",
                                justify="left", wraplength=420)
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
                                 justify="left", wraplength=420, padx=24)
        self.notes = tk.Label(r, text="", font=(FONT, 9), fg="#8d6e63", bg="white", anchor="w",
                              justify="left", wraplength=420, padx=16, pady=8)
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
        self.summary.config(text=f"{size}초 동안 재고 판정합니다. ({n}/{size})")
        self._notes(notes)

    def show_verdict(self, v: Verdict) -> None:
        self.level.config(text=f"● {v.level.label}", fg=COLORS[v.level])
        self.summary.config(text="\n".join(text.summary(v)))
        for dev, values in text.device_cells(v.stats).items():
            for lab, val in zip(self.cells[dev], values):
                lab.config(text=val)
        self.fans.config(text=text.fan_line(v.stats))
        self.power.config(text=text.power_line(v.stats))
        self.baseline.config(text=text.baseline_line(v) or "")
        self.evidence.config(text="\n".join(f"· {e}" for e in text.evidence(v)) or "· 걸린 기준 없음")
        self._notes(v.stats.notes)

    def show_error(self, msg: str) -> None:
        self.level.config(text="● 오류", fg=COLORS[Level.BAD])
        self.summary.config(text=f"센서를 읽다가 멈췄습니다. 창을 닫고 다시 켜 주세요.\n{msg}")

    def _notes(self, notes: list[str]) -> None:
        self.notes.config(text="\n".join(f"※ {n}" for n in notes))

    def poll(self) -> None:
        latest = None
        try:
            while True:
                latest = self.queue.get_nowait()
        except queue.Empty:
            pass
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
    from .baseline import Baseline
    from .collect import Collector
    root = tk.Tk()
    App(root, Monitor(queue.Queue(), Collector, Baseline))
    root.mainloop()
