import queue
from collections import deque

import pytest

from thermo import gui
from thermo.diagnose import diagnose
from thermo.model import CpuReading, Level, Proc, Snapshot
from thermo.store import Steady

from fake import make, reference

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def tk_root():
    # Tk() 를 여러 번 만들면 Windows 에서 가끔 TclError 가 난다 → 하나만 만들고 창마다 Toplevel
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("화면 없음")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def root(tk_root):
    w = tk.Toplevel(tk_root)
    yield w
    try:
        w.destroy()
    except tk.TclError:
        pass


def hot_diag():
    s = make(cpu={"temp": 95, "power": 20, "load": 60, "thermal_frac": 0.4, "clock": 3900},
             fans={"cpu_rpm": 3700}, procs=[Proc("blender.exe", 10, cpu=50)])
    return diagnose(s, reference(), Steady(True, ""))


def test_measuring_then_diagnosis(root):
    app = gui.App(root, None)
    assert "측정 중" in app.level.cget("text")
    app.queue.put(("reference", "기준 측정 #5 (2026-10-24, Performance 모드)"))
    app.queue.put(("verdict", hot_diag()))
    app.poll()
    assert app.level.cget("text").startswith("● 이상")
    assert app.level.cget("fg") == gui.COLORS[Level.BAD]
    assert "→ CPU 가 20W 에서 95°C" in app.summary.cget("text")
    assert [c.cget("text") for c in app.cells["CPU"]] == ["95°C", "60%", "20W", "3.9GHz"]
    assert app.baseline.cget("text") == "기준: 기준 측정 #5 (2026-10-24, Performance 모드)"


def test_evidence_toggle(root):
    app = gui.App(root, None)
    app.show_verdict(hot_diag())
    assert not app.evidence.winfo_ismapped()
    app.toggle_evidence()
    root.update()
    assert "스로틀" in app.evidence.cget("text")
    assert app.ev_button.cget("text") == "근거 ▾"


def test_only_latest_verdict_shown(root):
    app = gui.App(root, None)
    app.queue.put(("verdict", hot_diag()))
    app.queue.put(("verdict", diagnose(make(), None, Steady(True, ""))))
    app.poll()
    assert app.level.cget("text").startswith("● 정상")
    assert "기준 측정 전" in app.notes.cget("text")


def test_no_reference_label(root):
    app = gui.App(root, None)
    app.show_reference(None)
    assert "기준 측정 전" in app.baseline.cget("text")


def test_error_and_notes(root):
    app = gui.App(root, None)
    app.show_measuring(3, 10, ["관리자 권한이 없어 CPU 코어 온도·팬 속도를 읽지 못합니다."])
    assert "(3/10)" in app.summary.cget("text")
    assert app.notes.cget("text").startswith("※ 관리자")
    app.show_error("RuntimeError: x")
    assert app.level.cget("text") == "● 오류"


class FakeCollector:
    def __init__(self):
        self.window = deque(maxlen=3)
        self.notes = []
        self.closed = False

    def sample(self):
        s = Snapshot(t=float(len(self.window)), cpu=CpuReading(temp=60, load=20, power=10))
        self.window.append(s)
        return s

    def stats(self):
        from thermo.model import summarize
        return summarize(list(self.window))

    def close(self):
        self.closed = True


class FakeStore:
    def __init__(self):
        self.added = 0
        self.closed = False

    def add(self, s):
        self.added += 1

    def note(self, *a):
        pass

    def latest_checkup(self, mode=None):
        return None

    def close(self):
        self.closed = True


def test_monitor_flow():
    col, store = FakeCollector(), FakeStore()
    q = queue.Queue()
    m = gui.Monitor(q, lambda: col, lambda: store, interval=0.001)
    kinds = []
    m.start()
    while len(kinds) < 8:
        kinds.append(q.get(timeout=5)[0])
    m.stop.set()
    m.join(timeout=5)
    assert kinds[0] == "reference"
    assert kinds[1:3] == ["measuring", "measuring"]   # 창(3초)이 찰 때까지
    assert "verdict" in kinds
    assert store.added >= 3
    assert col.closed and store.closed


def test_monitor_reports_error():
    def broken():
        raise RuntimeError("센서 없음")
    q = queue.Queue()
    m = gui.Monitor(q, broken, FakeStore, interval=0.001)
    m.start()
    m.join(timeout=5)
    kind, msg = q.get(timeout=1)
    assert kind == "error" and "센서 없음" in msg
