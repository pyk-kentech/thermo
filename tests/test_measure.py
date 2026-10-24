"""M6 측정 보강: MSR 해석, 10Hz 누적, NVML 누적 시간, 시계열 기록, 정상 상태 판별."""

import json

import pytest

from thermo.fastloop import LimitAccumulator
from thermo.model import CpuReading, GpuReading, Proc, Snapshot, summarize
from thermo.sensors import msr as M
from thermo.sensors.nvidia import violation_frac
from thermo.store import Store, power_points, steady_state

# --- MSR 해석: 이 노트북에서 실제로 읽은 원시 값 (DESIGN 2장) ------------------
REAL = {
    M.RAPL_POWER_UNIT: 0xA0E03,
    M.TEMPERATURE_TARGET: 0x5640000,
    M.PKG_POWER_LIMIT: 0x42843800DF8398,
    M.PKG_POWER_INFO: 0x168,
    M.PACKAGE_THERM_STATUS: 0x88130282,
    M.CORE_PERF_LIMIT_REASONS: 0x0,
}


class FakeMsr(M.Msr):
    def __init__(self, regs):  # noqa: D107 — 드라이버 없이 해석만 시험
        self.regs = dict(regs)
        unit = self.regs[M.RAPL_POWER_UNIT]
        self.power_unit = 1 / (1 << M.bits(unit, 3, 0))
        self.energy_unit = 1 / (1 << M.bits(unit, 12, 8))
        self._last_energy = None

    def read(self, idx):
        return self.regs.get(idx)


def test_real_limits_decode():
    lim = FakeMsr(REAL).limits()
    assert (lim.tjmax, lim.tcc_offset, lim.throttle_temp) == (100, 5, 95)
    assert (lim.pl1_w, lim.pl2_w, lim.tdp_w) == (115.0, 135.0, 45.0)


def test_real_fast_state_decode():
    st = FakeMsr(REAL).fast()
    assert st.pkg_thermal is False and st.pkg_prochot is False
    assert st.tj_distance == 0x13
    assert st.reasons == 0


def test_fast_state_bits():
    regs = {**REAL, M.PACKAGE_THERM_STATUS: (5 << 16) | 1 | 4,
            M.CORE_PERF_LIMIT_REASONS: (1 << 1) | (1 << 10) | (1 << 17)}   # 로그 비트는 무시
    st = FakeMsr(regs).fast()
    assert st.pkg_thermal and st.pkg_prochot and st.tj_distance == 5
    assert M.limit_names(st.reasons) == ["thermal", "pl1"]


def test_package_power_from_energy_with_wraparound():
    m = FakeMsr(REAL)
    m.regs[M.PKG_ENERGY_STATUS] = 0xFFFFFF00
    assert m.package_power(10.0) is None
    m.regs[M.PKG_ENERGY_STATUS] = 0x00000100 + (25 << 14)   # 넘침 + 약 25 J
    w = m.package_power(11.0)
    assert w == pytest.approx(25 + 0x200 * m.energy_unit, rel=1e-6)


# --- 10Hz 누적 ----------------------------------------------------------------
def test_accumulator_fractions_and_peak():
    acc = LimitAccumulator(tjmax=100)
    for i in range(10):
        acc.add(M.FastState(pkg_thermal=i < 3, pkg_prochot=False, tj_distance=30 - i, reasons=(1 << 10) if i < 5 else 0))
    s = acc.take(power=42.0)
    assert s.n == 10
    assert s.thermal_frac == pytest.approx(0.3)
    assert s.limit_frac["pl1"] == pytest.approx(0.5)
    assert s.limit_frac["thermal"] == 0
    assert s.peak_temp == 100 - 21
    assert s.power == 42.0
    assert acc.take().n == 0   # 비워졌다


def test_accumulator_without_reason_register():
    acc = LimitAccumulator(tjmax=100)
    acc.add(M.FastState(False, False, 20, None))
    assert acc.take().limit_frac == {}


def test_summarize_new_fields():
    snaps = [Snapshot(t=i, cpu=CpuReading(temp=70, peak_temp=80 + i, thermal_frac=0.1 * (i % 2),
                                          limit_frac={"pl1": 1.0 if i < 5 else 0.0}),
                      gpu=GpuReading(temp=60, limit_frac={"power": 0.5})) for i in range(10)]
    s = summarize(snaps)
    assert s.cpu.peak_temp == 89
    assert s.cpu.thermal_frac == pytest.approx(0.05)
    assert s.cpu.limit_frac["pl1"] == pytest.approx(0.5)
    assert s.gpu.limit_frac["power"] == pytest.approx(0.5)


# --- NVML 누적 시간 -----------------------------------------------------------
def test_violation_frac():
    # 1초(1e6 µs) 동안 0.25초(2.5e8 ns) 제한
    assert violation_frac((1_000_000, 0), (2_000_000, 250_000_000)) == pytest.approx(0.25)
    assert violation_frac((1, 0), (1, 5)) is None
    assert violation_frac((0, 0), (1_000, 10**9)) == 1.0


# --- 기록 -----------------------------------------------------------------------
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMO_HOME", str(tmp_path))
    s = Store()
    yield s
    s.close()


def snap(t, cpu_w=20.0, gpu_w=10.0):
    return Snapshot(t=t, cpu=CpuReading(temp=70, power=cpu_w, limit_frac={"pl1": 0.0}),
                    gpu=GpuReading(temp=55, power=gpu_w), procs=[Proc("게임.exe", 7, 12.5, 80.0)])


def test_store_roundtrip(store):
    for t in range(5):
        store.add(snap(1000 + t))
    rows = store.rows(1000)
    assert len(rows) == 5
    assert rows[0]["cpu_power"] == 20.0
    assert json.loads(rows[0]["procs"])[0][:2] == ["게임.exe", 7]
    assert json.loads(rows[0]["cpu_limits"]) == {"pl1": 0.0}


def test_store_prune(store):
    store.add(snap(1.0))
    store.add(snap(10 * 86400.0))
    store.db.commit()
    store.prune(now=40 * 86400.0)
    assert [r["ts"] for r in store.rows(0)] == [10 * 86400.0]


def test_store_meta(store):
    store.note("cpu_limits", {"tjmax": 100})
    assert store.db.execute("SELECT key FROM meta").fetchone()[0] == "cpu_limits"


# --- 정상 상태 ------------------------------------------------------------------
def pts(values, start=0.0):
    return [(start + i, c, g) for i, (c, g) in enumerate(values)]


def test_steady_when_power_flat():
    p = pts([(40 + (i % 3), 20.0) for i in range(61)])
    assert steady_state(p, now=60).steady


def test_not_steady_when_power_changes():
    p = pts([(10.0, 5.0)] * 30 + [(45.0, 5.0)] * 31)
    r = steady_state(p, now=60)
    assert not r.steady and "CPU" in r.reason


def test_not_steady_when_too_short():
    assert not steady_state(pts([(10.0, 5.0)] * 20), now=19).steady


def test_small_power_uses_absolute_tolerance():
    # 2W↔4W 는 비율로는 크지만 ±3W 안이라 정상 상태
    p = pts([(2.0 if i % 2 else 4.0, 1.0) for i in range(61)])
    assert steady_state(p, now=60).steady


def test_power_points(store):
    for t in range(3):
        store.add(snap(t, cpu_w=10 + t))
    assert power_points(store.rows(0))[2] == (2, 12.0, 10.0)


def test_fan_glitch_filtered():
    from thermo.sensors.asus import fan_rpm
    assert fan_rpm(37) == 3700
    assert fan_rpm(0) == 0
    assert fan_rpm(323) is None     # 실제로 본 튐 값 32300rpm
    assert fan_rpm(None) is None
