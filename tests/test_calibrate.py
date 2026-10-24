"""평소 기록으로 만드는 코어 평균 기준 (calibrate.py)."""

import pytest

from thermo import calibrate as C
from thermo.diagnose import Diagnoser, diagnose, pick_cpu_model
from thermo.model import CpuReading, FanReading, GpuReading, Level, PowerReading, Snapshot
from thermo.store import Steady, Store

from fake import make, reference

REF = reference()      # 패키지 모델: 44.1 + 1.08·P_cpu + 0.15·P_gpu, σ 1.9


def pkg(c, g):
    return 44.1 + 1.08 * c + 0.15 * g


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMO_HOME", str(tmp_path))
    s = Store()
    yield s
    s.close()


def record(store, t0, seconds, cpu_w, gpu_w, pkg_off=0.0, ac=True):
    """정상 상태 구간을 기록한다. 코어 평균 = 0.9·P_cpu 관계를 따르는 합성 값."""
    for i in range(seconds):
        t = t0 + i
        avg = 40.0 + 0.9 * cpu_w + 0.1 * gpu_w
        store.add(Snapshot(
            t=t,
            cpu=CpuReading(temp=round(pkg(cpu_w, gpu_w) + pkg_off), temp_avg=avg,
                           power=cpu_w, load=30, thermal_frac=0.0),
            gpu=GpuReading(temp=50, power=gpu_w), fans=FanReading(3000, 3200), power=PowerReading(ac=ac)))
    store.db.commit()


def test_builds_core_average_model_from_matching_windows(store):
    now = 10_000_000.0
    t = now - 5000
    for cw in (10, 15, 20, 25):                     # 네 가지 부하, 각 300초
        record(store, t, 300, cw, 4.0)
        t += 400                                   # 사이에 끊김 → 다시 정상 상태부터
    m, info = C.core_avg_model(store, REF, now=now)
    assert m is not None, info
    assert m.target == "cpu_temp_avg"
    assert m.coef["cpu_power"] == pytest.approx(0.9, abs=0.02)
    assert "평소 기록" in info


def test_windows_not_matching_reference_are_not_learned(store):
    now = 10_000_000.0
    t = now - 5000
    for cw in (10, 15, 20, 25):
        record(store, t, 300, cw, 4.0, pkg_off=+9)   # 기준보다 9°C(4.7σ) 뜨거움 = 냉각이 나빠진 상태
        t += 400
    m, info = C.core_avg_model(store, REF, now=now)
    assert m is None
    assert "기준과 안 맞음" in info and "모으는 중" in info


def test_too_few_windows(store):
    now = 10_000_000.0
    record(store, now - 300, 200, 15, 4.0)
    m, info = C.core_avg_model(store, REF, now=now)
    assert m is None and "/30 구간" in info


def test_battery_windows_skipped(store):
    now = 10_000_000.0
    t = now - 5000
    for cw in (10, 15, 20, 25):
        record(store, t, 300, cw, 4.0, ac=False)
        t += 400
    m, info = C.core_avg_model(store, REF, now=now)
    assert m is None and "배터리" in info


def test_cache_reused(store):
    m1, info1 = C.cached_core_avg_model(store, REF, 7)
    n = store.db.execute("SELECT COUNT(*) FROM meta WHERE key='core_avg_model'").fetchone()[0]
    m2, info2 = C.cached_core_avg_model(store, REF, 7)
    assert info1 == info2
    assert store.db.execute("SELECT COUNT(*) FROM meta WHERE key='core_avg_model'").fetchone()[0] == n
    C.cached_core_avg_model(store, REF, 8)          # 기준이 바뀌면 다시 계산
    assert store.db.execute("SELECT COUNT(*) FROM meta WHERE key='core_avg_model'").fetchone()[0] == n + 1


# --- 판단에서 쓰기 -------------------------------------------------------------------
def avg_model():
    from thermo.reference import LinearModel
    return LinearModel("cpu_temp_avg", 40.0, {"cpu_power": 0.9, "gpu_power": 0.1}, 2.0, 40, ["평소"],
                       ranges={"cpu_power": [8, 40], "gpu_power": [3, 50]})


def test_single_thread_load_not_flagged_with_core_average_model():
    # 1스레드 부하: 패키지는 기준보다 9°C 높지만 코어 평균은 평소대로
    c, g = 30.0, 4.0
    s = make(cpu={"temp": pkg(c, g) + 9, "temp_avg": 40 + 0.9 * c + 0.4, "power": c, "load": 8},
             gpu={"power": g, "temp": 46.1 + 0.37 * g + 0.12 * c}, fans={"cpu_rpm": 3700})
    with_avg = diagnose(s, REF, Steady(True, ""), avg_model=avg_model())
    assert with_avg.level == Level.OK and "cpu_hotter" not in with_avg.keys
    assert any(e.startswith("CPU 코어 평균 기대") for e in with_avg.evidence)
    # 코어 평균 기준이 없으면 패키지로 '주의' — 하지만 몰린 부하라는 단서를 붙인다
    without = diagnose(s, REF, Steady(True, ""))
    assert "cpu_hotter" in without.keys and "concentrated_load" in without.keys
    assert "한 코어에 부하가 몰려" in " ".join(without.judgement)


def test_core_average_model_still_catches_real_cooling_problem():
    c, g = 20.0, 4.0
    s = make(cpu={"temp": pkg(c, g) + 10, "temp_avg": 40 + 0.9 * c + 0.4 + 10, "power": c},
             gpu={"power": g, "temp": 46.1 + 0.37 * g + 0.12 * c}, fans={"cpu_rpm": 3400})
    r = diagnose(s, REF, Steady(True, ""), avg_model=avg_model())
    assert "cpu_hotter" in r.keys and r.level >= Level.WARN


def test_pick_model_falls_back_without_core_average():
    s = make(cpu={"temp_avg": None})
    assert pick_cpu_model(REF, avg_model(), s.cpu) is REF.cpu_model
    s2 = make(cpu={"temp_avg": 50})
    assert pick_cpu_model(REF, avg_model(), s2.cpu).target == "cpu_temp_avg"
    assert Diagnoser(REF, avg_model=avg_model()).avg_model is not None
