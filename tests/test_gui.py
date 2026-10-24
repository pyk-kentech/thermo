import queue
from collections import deque

import pytest

from thermo import gui, rules
from thermo.model import CpuReading, Level, Proc, Snapshot

from test_rules import make

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


def test_measuring_then_verdict(root):
    app = gui.App(root, None)
    assert "측정 중" in app.level.cget("text")
    v = rules.diagnose(make(cpu={"temp": 92, "load": 85, "power": 60, "clock": 3900},
                            procs=[Proc("blender.exe", 10, cpu=70)]))
    app.queue.put(("verdict", v))
    app.poll()
    assert app.level.cget("text") == "● 주의"
    assert app.level.cget("fg") == gui.COLORS[Level.WARN]
    assert "blender.exe" in app.summary.cget("text")
    assert [c.cget("text") for c in app.cells["CPU"]] == ["92°C", "85%", "60W", "3.9GHz"]
    assert "3300rpm" in app.fans.cget("text")


def test_evidence_toggle(root):
    app = gui.App(root, None)
    app.show_verdict(rules.diagnose(make(cpu={"temp": 98, "load": 85})))
    assert not app.evidence.winfo_ismapped()
    app.toggle_evidence()
    root.update()
    assert "98°C" in app.evidence.cget("text")
    assert app.ev_button.cget("text") == "근거 ▾"


def test_only_latest_message_shown(root):
    app = gui.App(root, None)
    app.queue.put(("verdict", rules.diagnose(make(cpu={"temp": 98, "load": 85}))))
    app.queue.put(("verdict", rules.diagnose(make())))
    app.poll()
    assert app.level.cget("text") == "● 정상"


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
        self.window.append(Snapshot(t=0, cpu=CpuReading(temp=60, load=20)))

    def stats(self):
        from thermo.model import summarize
        return summarize(list(self.window))

    def close(self):
        self.closed = True


class FakeBaseline:
    def __init__(self):
        self.recorded = 0
        self.closed = False

    def lookup(self, dev, s):
        return None

    def fan_max(self):
        return {}

    def record(self, v):
        self.recorded += 1
        return True

    def close(self):
        self.closed = True


def test_monitor_flow(monkeypatch):
    monkeypatch.setattr(gui, "RECORD_EVERY", 4)
    col, base = FakeCollector(), FakeBaseline()
    q = queue.Queue()
    m = gui.Monitor(q, lambda: col, lambda: base, interval=0.001)
    kinds = []
    m.start()
    while len(kinds) < 8:
        kinds.append(q.get(timeout=5)[0])
    m.stop.set()
    m.join(timeout=5)
    assert kinds[:2] == ["measuring", "measuring"]   # 창(3초)이 찰 때까지
    assert "verdict" in kinds
    assert base.recorded >= 1
    assert col.closed and base.closed


def test_monitor_reports_error():
    def broken():
        raise RuntimeError("센서 없음")
    q = queue.Queue()
    m = gui.Monitor(q, broken, FakeBaseline, interval=0.001)
    m.start()
    m.join(timeout=5)
    kind, msg = q.get(timeout=1)
    assert kind == "error" and "센서 없음" in msg
