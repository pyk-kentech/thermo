"""창 (DESIGN 2판 8장). 센서는 작업 스레드에서 1초마다 읽고, 판단은 2초마다 한다.

어두운 카드형 (2026-10-24 사용자 선택): 판정 띠 → CPU·GPU·팬 카드(큰 숫자 + 2분 미니 그래프) → 판단 → 프로그램
→ [근거 | 기록 | 기준 측정] 탭 → ※ 안내. 새 의존성 없이 tkinter 로 그린다.
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

from .diagnose import (PROGRAMS_PREFIX, Diagnoser, Diagnosis, is_background, known_name, load_reference,
                       procs_with_watts)
from .model import Level, Proc, WindowStats
from .reference import Reference, summary

# --- 색·글꼴 -------------------------------------------------------------------
BG = "#121418"
CARD = "#1c2027"
EDGE = "#2a2f38"
TEXT = "#e8eaed"
SUB = "#9aa0a6"
DIM = "#5f6670"
COLORS = {Level.OK: "#34c759", Level.WARN: "#ff9f0a", Level.BAD: "#ff453a"}
GREY = "#8e8e93"
CPU_C, CPU_AVG_C, GPU_C, FAN_C = "#ff6b6b", "#ffb0b0", "#5ac8fa", "#34c759"
KO = "Malgun Gothic"
NUM = "Segoe UI"

REFRESH_EVERY = 2          # 초마다 판단
HISTORY_SECONDS = 600      # 기록 그래프: 최근 10분
SPARK_SECONDS = 120        # 카드 미니 그래프: 최근 2분
MODES = ("Performance", "Silent", "Turbo", "Windows", "Manual")

# 그래프 선: (이름, 색, 표본 튜플 안 위치)
TEMP_LINES = (("CPU 패키지", CPU_C, 1), ("CPU 코어 평균", CPU_AVG_C, 2), ("GPU", GPU_C, 4))
POWER_LINES = (("CPU W", CPU_C, 3), ("GPU W", GPU_C, 5))
FAN_LINE = ("CPU 팬 rpm", FAN_C, 6)


def sample_tuple(s) -> tuple:
    """그래프용: (시각, CPU 패키지 °C, CPU 코어 평균 °C, CPU W, GPU °C, GPU W, CPU 팬 rpm)."""
    return (s.t, s.cpu.temp, s.cpu.temp_avg, s.cpu.power, s.gpu.temp, s.gpu.power, s.fans.cpu_rpm)


def _n(v, nd=0, unit=""):
    return "–" if v is None else f"{v:.{nd}f}{unit}"


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


def polyline(points: list[tuple], idx: int, x, y) -> list[list[float]]:
    """값이 빈 곳에서 끊기는 선 조각들의 좌표 목록 (한 조각 = create_line 한 번)."""
    runs, cur = [], []
    for p in points:
        v = p[idx]
        if v is None:
            if len(cur) >= 4:
                runs.append(cur)
            cur = []
            continue
        cur += [x(p[0]), y(v)]
    if len(cur) >= 4:
        runs.append(cur)
    return runs


def dark_title_bar(root: tk.Tk) -> None:
    """Windows 10/11 의 어두운 제목 표시줄 (DWMWA_USE_IMMERSIVE_DARK_MODE). 안 되면 그냥 둔다."""
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


class Card(tk.Frame):
    """CPU·GPU·팬 카드: 제목, 큰 숫자, 작은 줄 두 개, 표시 띠, 2분 미니 그래프."""

    def __init__(self, parent, title: str, color: str) -> None:
        super().__init__(parent, bg=CARD, highlightbackground=EDGE, highlightthickness=1, padx=12, pady=10)
        self.color = color
        tk.Label(self, text=title, font=(KO, 9, "bold"), fg=SUB, bg=CARD, anchor="w").pack(fill="x")
        self.big = tk.Label(self, text="–", font=(NUM, 26, "bold"), fg=TEXT, bg=CARD, anchor="w")
        self.big.pack(fill="x")
        self.line1 = tk.Label(self, text="", font=(KO, 9), fg=SUB, bg=CARD, anchor="w")
        self.line1.pack(fill="x")
        self.line2 = tk.Label(self, text="", font=(KO, 9), fg=SUB, bg=CARD, anchor="w")
        self.line2.pack(fill="x")
        self.badge = tk.Label(self, text="", font=(KO, 8, "bold"), fg=BG, bg=CARD, padx=6)
        self.badge.pack(anchor="w", pady=(4, 0))
        self.spark = tk.Canvas(self, height=34, bg=CARD, highlightthickness=0)
        self.spark.pack(fill="x", pady=(6, 0))

    def set(self, big: str, line1: str, line2: str, badge: str = "", badge_color: str = "") -> None:
        self.big.config(text=big)
        self.line1.config(text=line1)
        self.line2.config(text=line2)
        self.badge.config(text=badge, bg=badge_color if badge else CARD)

    def draw(self, pts: list[tuple], idx: int, lo: float, hi: float) -> None:
        c = self.spark
        c.delete("all")
        w, h = max(c.winfo_width(), 60), max(c.winfo_height(), 30)
        if len(pts) < 2:
            return
        t1 = pts[-1][0]
        x = lambda t: w * (t - (t1 - SPARK_SECONDS)) / SPARK_SECONDS  # noqa: E731
        y = lambda v: h - 2 - (h - 4) * (min(max(v, lo), hi) - lo) / (hi - lo)  # noqa: E731
        for run in polyline([p for p in pts if p[0] >= t1 - SPARK_SECONDS], idx, x, y):
            c.create_line(*run, fill=self.color, width=2, smooth=False)


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
        root.geometry("660x880")
        root.minsize(560, 640)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        dark_title_bar(root)
        self._style()
        self._build()
        self.show_measuring(0, 10, [])
        if monitor:
            monitor.start()
        self.root.after(200, self.poll)

    # --- 화면 구성 -------------------------------------------------------
    def _style(self) -> None:
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0),
                     bordercolor=EDGE, lightcolor=CARD, darkcolor=CARD)
        st.configure("TNotebook.Tab", background=BG, foreground=SUB, padding=(14, 6), borderwidth=0,
                     font=(KO, 9), bordercolor=BG, lightcolor=BG, darkcolor=BG, focuscolor=CARD)
        st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)],
               lightcolor=[("selected", CARD)], bordercolor=[("selected", EDGE)])
        st.configure("TFrame", background=CARD)
        st.configure("TCombobox", fieldbackground=CARD, background=EDGE, foreground=TEXT, arrowcolor=TEXT)
        st.configure("Accent.TButton", background="#2f6fed", foreground="white", borderwidth=0, padding=(12, 6),
                     font=(KO, 9, "bold"))
        st.map("Accent.TButton", background=[("active", "#4a84f5")])

    def _box(self, parent, title: str | None = None) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, highlightbackground=EDGE, highlightthickness=1, padx=14, pady=10)
        f.pack(fill="x", padx=14, pady=(10, 0))
        if title:
            tk.Label(f, text=title, font=(KO, 9, "bold"), fg=SUB, bg=CARD, anchor="w").pack(fill="x")
        return f

    def _build(self) -> None:
        r = self.root
        # 판정 띠
        head = tk.Frame(r, bg=CARD, highlightbackground=EDGE, highlightthickness=1)
        head.pack(fill="x", padx=14, pady=(14, 0))
        self.bar = tk.Frame(head, bg=GREY, width=8)
        self.bar.pack(side="left", fill="y")
        inner = tk.Frame(head, bg=CARD, padx=14, pady=10)
        inner.pack(side="left", fill="both", expand=True)
        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")
        self.level = tk.Label(top, text="", font=(KO, 20, "bold"), fg=GREY, bg=CARD, anchor="w")
        self.level.pack(side="left")
        self.ref_short = tk.Label(top, text="", font=(KO, 8), fg=DIM, bg=CARD, anchor="e")
        self.ref_short.pack(side="right")
        self.headline = tk.Label(inner, text="", font=(KO, 11), fg=TEXT, bg=CARD, anchor="w", justify="left")
        self.headline.pack(fill="x")

        # 카드 세 개
        row = tk.Frame(r, bg=BG)
        row.pack(fill="x", padx=14, pady=(10, 0))
        self.cards = {"CPU": Card(row, "CPU", CPU_C), "GPU": Card(row, "GPU", GPU_C), "FAN": Card(row, "팬", FAN_C)}
        for i, k in enumerate(("CPU", "GPU", "FAN")):
            self.cards[k].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            row.grid_columnconfigure(i, weight=1, uniform="card")

        # 판단, 프로그램
        jb = self._box(r, "판단")
        self.judgement = tk.Label(jb, text="", font=(KO, 10), fg=TEXT, bg=CARD, anchor="w", justify="left")
        self.judgement.pack(fill="x", pady=(4, 0))
        pb = self._box(r, "전력을 쓰는 프로그램")
        self.procs = tk.Frame(pb, bg=CARD)
        self.procs.pack(fill="x", pady=(4, 0))

        # 안내 (맨 아래)
        self.notes = tk.Label(r, text="", font=(KO, 8), fg=DIM, bg=BG, anchor="w", justify="left", padx=16, pady=8)
        self.notes.pack(side="bottom", fill="x")

        # 탭
        self.tabs = ttk.Notebook(r)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(12, 0))
        ev = tk.Frame(self.tabs, bg=CARD)
        self.evidence = tk.Label(ev, text="", font=(KO, 9), fg=SUB, bg=CARD, anchor="nw", justify="left",
                                 padx=12, pady=10)
        self.evidence.pack(fill="both", expand=True)
        self.tabs.add(ev, text="근거")

        rec = tk.Frame(self.tabs, bg=CARD)
        self.graph = tk.Canvas(rec, bg=CARD, highlightthickness=0, height=260)
        self.graph.pack(fill="both", expand=True)
        self.graph.bind("<Configure>", lambda e: self.draw_graph())
        self.tabs.add(rec, text="기록 (10분)")

        refp = tk.Frame(self.tabs, bg=CARD)
        self.ref_label = tk.Label(refp, text="", font=(KO, 9), fg=SUB, bg=CARD, anchor="w", justify="left",
                                  padx=12, pady=8)
        self.ref_label.pack(fill="x")
        self.ref_text = tk.Text(refp, font=("Consolas", 9), height=10, wrap="none", relief="flat", bg=BG, fg=SUB,
                                insertbackground=TEXT, padx=8, pady=6)
        self.ref_text.pack(fill="both", expand=True, padx=12)
        bar = tk.Frame(refp, bg=CARD)
        bar.pack(fill="x", padx=12, pady=8)
        tk.Label(bar, text="지금 성능 모드", font=(KO, 9), fg=SUB, bg=CARD).pack(side="left")
        self.mode = tk.StringVar(value="Performance")
        ttk.Combobox(bar, textvariable=self.mode, values=MODES, width=12, state="readonly").pack(side="left", padx=6)
        self.checkup_button = ttk.Button(bar, text="기준 측정 시작…", style="Accent.TButton", command=self.ask_checkup)
        self.checkup_button.pack(side="right")
        self.tabs.add(refp, text="기준 측정")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.draw_graph())

        self._wrapped = [self.headline, self.judgement, self.notes, self.evidence, self.ref_label]
        r.bind("<Configure>", self._rewrap)

    def _rewrap(self, event=None) -> None:
        """창 폭이 바뀌면 줄바꿈 폭도 맞춘다."""
        w = max(self.root.winfo_width() - 90, 300)
        for lab in self._wrapped:
            lab.config(wraplength=w)

    # --- 갱신 ------------------------------------------------------------
    def _set_level(self, text: str, color: str, headline: str) -> None:
        self.level.config(text=text, fg=color)
        self.bar.config(bg=color)
        self.headline.config(text=headline)

    def show_measuring(self, n: int, size: int, notes: list[str]) -> None:
        self._set_level("측정 중", GREY, f"{size}초 동안 잰 뒤 판단합니다 ({n}/{size})")
        self.judgement.config(text="기준과의 비교는 부하가 2분 동안 안정돼야 합니다.")
        self._notes(notes)

    def show_verdict(self, d: Diagnosis) -> None:
        label, _, rest = d.headline.partition(" — ")
        self._set_level(label, COLORS[d.level], rest)
        lines = [j for j in d.judgement if not j.startswith(PROGRAMS_PREFIX)]   # 프로그램은 아래 카드에
        self.judgement.config(text="\n".join(f"→  {j}" for j in lines))
        self.evidence.config(text="\n".join(f"·  {e}" for e in d.evidence) or "·  기준과 비교한 값이 아직 없습니다")
        if d.stats is not None:
            self._cards(d.stats)
            self._procs(d.stats)
        self._notes(d.notes)

    def _cards(self, s: WindowStats) -> None:
        c, g, f = s.cpu, s.gpu, s.fans
        badge, bc = "", ""
        if (c.thermal_frac or 0) >= 0.05:
            badge, bc = f"95°C 스로틀 {c.thermal_frac:.0%}", COLORS[Level.WARN]
        self.cards["CPU"].set(_n(c.temp, 0, "°C"), f"{_n(c.power, 0, 'W')} · 사용률 {_n(c.load, 0, '%')}",
                              f"코어 평균 {_n(c.temp_avg, 0, '°C')} · {_n(None if c.clock is None else c.clock / 1000, 1, 'GHz')}",
                              badge, bc)
        gl = {k: v for k, v in g.limit_frac.items() if v >= 0.05}
        gbadge, gbc = "", ""
        if gl.get("thermal"):
            gbadge, gbc = f"열 제한 {gl['thermal']:.0%}", COLORS[Level.BAD]
        elif gl.get("power"):
            gbadge, gbc = f"전력 제한 {gl['power']:.0%}", "#5e5ce6"
        self.cards["GPU"].set(_n(g.temp, 0, "°C"), f"{_n(g.power, 0, 'W')} · 사용률 {_n(g.load, 0, '%')}",
                              f"한계까지 {_n(g.margin, 0, '°C')}", gbadge, gbc)
        src = {True: "어댑터", False: "배터리", None: "전원 ?"}[s.power.ac]
        self.cards["FAN"].set(_n(f.cpu_rpm, 0), f"CPU 팬 rpm · GPU 팬 {_n(f.gpu_rpm, 0)}", src,
                              "배터리" if s.power.ac is False else "", COLORS[Level.WARN])

    def _procs(self, s: WindowStats) -> None:
        for w in self.procs.winfo_children():
            w.destroy()
        # 같은 이름의 프로세스(여러 프로세스로 도는 앱)는 한 줄로 합친다
        merged: dict[str, list] = {}
        for p, w in procs_with_watts(s, 8):
            m = merged.setdefault(p.name, [p, 0.0, 0.0, 0.0])
            m[1] += p.cpu
            m[2] += p.gpu
            m[3] += w or 0.0
        rows = []
        for p, cpu, gpu, w in merged.values():
            if cpu >= 0.5 or gpu >= 0.5:
                rows.append((Proc(p.name, p.pid, cpu, gpu, p.background), w))
        rows = sorted(rows, key=lambda r: -(r[0].cpu + r[0].gpu))[:4]
        if not rows:
            tk.Label(self.procs, text="눈에 띄게 전력을 쓰는 프로그램이 없습니다", font=(KO, 9), fg=DIM, bg=CARD,
                     anchor="w").pack(fill="x")
            return
        for p, w in rows:
            line = tk.Frame(self.procs, bg=CARD)
            line.pack(fill="x", pady=1)
            tk.Label(line, text=p.name, font=(KO, 10, "bold"), fg=TEXT, bg=CARD).pack(side="left")
            what = known_name(p)
            if what:
                tk.Label(line, text=what, font=(KO, 8), fg=DIM, bg=CARD).pack(side="left", padx=(6, 0))
            if is_background(p):
                tk.Label(line, text="창 없음", font=(KO, 7, "bold"), fg=BG, bg=SUB, padx=4).pack(side="left", padx=6)
            use = f"CPU {p.cpu:.0f}%" + (f" · 약 {w:.0f}W" if w and w >= 1 else "") + (f" · GPU {p.gpu:.0f}%" if p.gpu >= 1 else "")
            tk.Label(line, text=use, font=(KO, 9), fg=SUB, bg=CARD).pack(side="right")

    def show_reference(self, ref: Reference | None, label: str | None, avg_info: str | None = None) -> None:
        self.ref = ref
        self.ref_text.config(state="normal")
        self.ref_text.delete("1.0", "end")
        if ref is None:
            self.ref_short.config(text="기준 없음")
            self.ref_label.config(text="기준 측정 전입니다. 기준이 없으면 '평소보다 뜨거운가'는 판단하지 못하고 "
                                       "하드웨어가 알려 준 사실만 봅니다.")
        else:
            self.ref_short.config(text=f"기준 {label.split(' (')[0].replace('기준 측정 ', '')} · {ref.mode}")
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
        self._set_level("오류", COLORS[Level.BAD], "센서를 읽다가 멈췄습니다. 창을 닫고 다시 켜 주세요.")
        self.judgement.config(text=msg)

    def _notes(self, notes: list[str]) -> None:
        self.notes.config(text="\n".join(f"※ {n}" for n in notes))

    # --- 그래프 ------------------------------------------------------------
    def draw_sparks(self) -> None:
        pts = list(self.history)[-SPARK_SECONDS - 5:]
        self.cards["CPU"].draw(pts, 1, 30, 100)
        self.cards["GPU"].draw(pts, 4, 30, 100)
        self.cards["FAN"].draw(pts, 6, 0, 6000)

    def draw_graph(self) -> None:
        c = self.graph
        if not c.winfo_ismapped():
            return
        c.delete("all")
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200)
        pts = list(self.history)
        if len(pts) < 2:
            c.create_text(w / 2, h / 2, text="기록이 쌓이는 중입니다", fill=DIM, font=(KO, 10))
            return
        left, right, gap = 44, w - 50, 18
        top_h = (h - 3 * gap - 12) * 0.55
        boxes = ((gap, gap + top_h), (2 * gap + top_h, h - gap - 12))
        t1 = pts[-1][0]
        t0 = t1 - HISTORY_SECONDS
        pts = [p for p in pts if p[0] >= t0]

        def x(t):
            return left + (right - left) * (t - t0) / HISTORY_SECONDS

        def yf(box, lo, hi, scale=1.0):
            y0, y1 = box
            return lambda v: y1 - (y1 - y0) * (min(max(v * scale, lo), hi) - lo) / (hi - lo)

        (ty0, ty1) = boxes[0]
        for v in (40, 60, 80, 95):
            y = yf(boxes[0], 30, 100)(v)
            c.create_line(left, y, right, y, fill="#5a2a2a" if v == 95 else EDGE, dash=(3, 3) if v == 95 else ())
            c.create_text(left - 6, y, text=f"{v}°", anchor="e", fill=DIM, font=(NUM, 8))
        for name, color, idx in TEMP_LINES:
            for run in polyline(pts, idx, x, yf(boxes[0], 30, 100)):
                c.create_line(*run, fill=color, width=2 if idx != 2 else 1)
        (py0, py1) = boxes[1]
        for v in (0, 20, 40, 60):
            y = yf(boxes[1], 0, 60)(v)
            c.create_line(left, y, right, y, fill=EDGE)
            c.create_text(left - 6, y, text=f"{v}W", anchor="e", fill=DIM, font=(NUM, 8))
            c.create_text(right + 6, y, text=f"{v * 100:.0f}", anchor="w", fill="#2f7d45", font=(NUM, 8))
        for name, color, idx in POWER_LINES:
            for run in polyline(pts, idx, x, yf(boxes[1], 0, 60)):
                c.create_line(*run, fill=color, width=2)
        for run in polyline(pts, FAN_LINE[2], x, yf(boxes[1], 0, 60, 0.01)):
            c.create_line(*run, fill=FAN_LINE[1], width=1)
        lx = left
        for name, color, _ in TEMP_LINES + POWER_LINES + (FAN_LINE,):
            c.create_line(lx, h - 8, lx + 12, h - 8, fill=color, width=2)
            item = c.create_text(lx + 15, h - 8, text=name, anchor="w", fill=SUB, font=(KO, 8))
            lx = c.bbox(item)[2] + 10
        c.create_text(right, ty0 - 9, text="지금", anchor="e", fill=DIM, font=(KO, 8))
        c.create_text(left, ty0 - 9, text="10분 전", anchor="w", fill=DIM, font=(KO, 8))

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
            self.draw_sparks()
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
