"""M7 기준 측정: 모델 맞추기(reference)와 단계 진행(checkup)을 합성 노트북으로 시험한다."""

import json
import random

import pytest

from thermo import reference as R
from thermo.checkup import PLAN, Checkup, Stage
from thermo.model import CpuReading, FanReading, GpuReading, PowerReading, Proc, Snapshot
from thermo.store import Store

# 합성 노트북: 정상 상태 온도 = 40 + 0.9·P_cpu + 0.25·P_gpu (CPU), 35 + 0.45·P_gpu + 0.1·P_cpu (GPU)
CPU_A, CPU_B, CPU_C = 40.0, 0.9, 0.25
GPU_A, GPU_B, GPU_C = 35.0, 0.45, 0.1


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.0)


class FakeLaptop:
    """1초마다 온도가 목표로 다가가는(시정수 8초) 노트북. 95°C 에서 묶이고 그때 전력이 줄어든다."""

    def __init__(self, clock, noise=0.4, ac=True, busy_proc=None):
        self.clock = clock
        self.threads = 0
        self.game = False
        self.cpu_t = 50.0
        self.gpu_t = 45.0
        self.rng = random.Random(1)
        self.noise = noise
        self.ac = ac
        self.busy_proc = busy_proc

    def sample(self):
        self.clock.t += 0.01   # 읽는 데 드는 시간
        p_cpu = 8 + 3.6 * self.threads
        p_gpu = 70.0 if self.game else 4.0
        if self.game:
            p_cpu += 6
        target = CPU_A + CPU_B * p_cpu + CPU_C * p_gpu
        thermal = 0.0
        if target > 95:   # 95°C 에 묶임: 그 온도를 내는 전력까지 떨어진다
            p_cpu = (95 - CPU_A - CPU_C * p_gpu) / CPU_B
            target, thermal = 95.0, 0.5
        self.cpu_t += (target - self.cpu_t) / 8
        g_target = GPU_A + GPU_B * p_gpu + GPU_C * p_cpu
        self.gpu_t += (g_target - self.gpu_t) / 8
        n = lambda: self.rng.gauss(0, self.noise)  # noqa: E731
        procs = [Proc(*self.busy_proc)] if self.busy_proc else []
        return Snapshot(
            t=self.clock.t,
            cpu=CpuReading(temp=round(self.cpu_t + n()), power=p_cpu + n(), load=6.25 * self.threads,
                           thermal_frac=thermal, limit_frac={}),
            gpu=GpuReading(temp=round(self.gpu_t + n()), power=p_gpu + n(), margin=87 - self.gpu_t,
                           limit_frac={"power": 0.3 if self.game else 0.0, "thermal": 0.0}),
            fans=FanReading(cpu_rpm=max(0, round((self.cpu_t - 50) * 90, -2)),
                            gpu_rpm=max(0, round((self.gpu_t - 45) * 100, -2))),
            power=PowerReading(ac=self.ac), procs=procs)


class FakeLoad:
    def __init__(self, laptop, n):
        self.laptop, self.n = laptop, n

    def start(self):
        self.laptop.threads = self.n
        return self

    def stop(self):
        self.laptop.threads = 0


class FakeUI:
    def __init__(self, laptop, answers=None):
        self.laptop = laptop
        self.lines = []
        self.answers = list(answers or [])

    def say(self, text):
        self.lines.append(text)

    def ask(self, text):
        self.lines.append("▶ " + text)
        if self.answers:
            return self.answers.pop(0)
        if "게임을 켜고" in text:
            self.laptop.game = True
        if "게임을 끈" in text:
            self.laptop.game = False
        return True


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMO_HOME", str(tmp_path))
    s = Store()
    yield s
    s.close()


def run_checkup(store, plan=None, **kw):
    clock = Clock()
    lap = FakeLaptop(clock, **{k: v for k, v in kw.items() if k in ("noise", "ac", "busy_proc")})
    ui = FakeUI(lap, kw.get("answers"))
    c = Checkup(lap, store, "Performance", 24.0, ui, plan=plan or PLAN,
                load=lambda n: FakeLoad(lap, n), sleep=clock.sleep, monotonic=clock.monotonic)
    return c.run(), ui, lap


def test_full_checkup_recovers_model(store):
    ref, ui, lap = run_checkup(store)
    assert ref is not None, ui.lines
    m = ref.cpu_model
    assert m.coef["cpu_power"] == pytest.approx(CPU_B, abs=0.08)
    assert m.coef["gpu_power"] == pytest.approx(CPU_C, abs=0.08)
    assert m.intercept == pytest.approx(CPU_A, abs=3)
    assert m.sigma < 3
    g = ref.gpu_model
    assert g.coef["gpu_power"] == pytest.approx(GPU_B, abs=0.08)
    # CPU 16스레드는 95°C 에 묶임 → 온도 한계형, 모델에서 빠짐
    assert ref.cpu_thermal_limited
    assert "cpu16" not in m.stages
    assert any("95°C 에 묶인" in n for n in ref.notes)
    assert ref.cpu_sustained_w == pytest.approx((95 - CPU_A - CPU_C * 4) / CPU_B, abs=1)
    assert ref.gpu_sustained_w == pytest.approx(70, abs=1)
    assert lap.game is False and lap.threads == 0     # 끝나면 부하·게임 모두 꺼짐
    # 저장됐다
    saved = store.latest_checkup("Performance")
    assert R.Reference.from_json(saved["result"]).cpu_model.coef == m.coef
    assert len(store.checkup_stages(saved["id"])) == len(PLAN)


def test_expected_fan_curve(store):
    ref, _, _ = run_checkup(store)
    curve = ref.fan_curve["cpu"]
    assert curve == sorted(curve)
    lo, hi = curve[0], curve[-1]
    assert R.expected_fan(curve, lo[0] - 5) == lo[1]
    assert R.expected_fan(curve, hi[0] + 5) == hi[1]


def test_stop_when_user_quits_at_game(store):
    plan = [Stage("idle", "공회전", 0, False, 200), Stage("gpu", "게임", 0, True, 200)]
    # 첫 물음(게임 켜기)에 q
    ref, ui, lap = run_checkup(store, plan=plan, answers=[False])
    assert ref is None
    row = store.db.execute("SELECT status FROM checkup").fetchone()
    assert row[0].startswith("중단")


def test_unsteady_stage_marked_and_excluded(store):
    plan = [Stage("idle", "공회전", 0, False, 60)]   # 60초로는 정상 상태(120초+60초)에 못 닿음
    ref, ui, _ = run_checkup(store, plan=plan)
    assert ref is not None
    assert ref.stages[0].steady is False
    assert ref.cpu_model is None
    assert any("정상 상태에 닿지 못해" in n for n in ref.notes)


def test_warns_busy_program_and_adapter(store):
    plan = [Stage("idle", "공회전", 0, False, 5)]
    _, ui, _ = run_checkup(store, plan=plan, ac=False, busy_proc=("chrome.exe", 3, 20.0))
    text = "\n".join(ui.lines)
    assert "어댑터가 빠져" in text
    assert "chrome.exe 20%" in text


def test_fit_drops_narrow_input():
    bl = [R.Block("s", i, 10 + i, 5.0, 40 + 0.9 * (10 + i), 50, 0, 0, 0, 0) for i in range(20)]
    m = R.fit("cpu_temp", ["cpu_power", "gpu_power"], bl)
    assert list(m.coef) == ["cpu_power"]
    assert m.coef["cpu_power"] == pytest.approx(0.9)
    assert m.sigma == R.SIGMA_FLOOR       # 완벽히 맞아도 하한
    assert any("gpu_power" in n for n in m.notes)


def test_fit_needs_enough_blocks():
    bl = [R.Block("s", 0, 10, 5, 50, 50, 0, 0, 0, 0)]
    assert R.fit("cpu_temp", ["cpu_power"], bl) is None


def test_reference_json_roundtrip(store):
    ref, _, _ = run_checkup(store)
    again = R.Reference.from_json(json.loads(json.dumps(ref.to_json())))
    assert again.cpu_model.predict(cpu_power=20, gpu_power=4) == pytest.approx(
        ref.cpu_model.predict(cpu_power=20, gpu_power=4))
    assert R.summary(again)[0].startswith("성능 모드: Performance")
