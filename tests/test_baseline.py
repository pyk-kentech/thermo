import pytest

from thermo import rules, text
from thermo.baseline import Baseline, home, load_bucket
from thermo.model import Level, Proc

from test_rules import make


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMO_HOME", str(tmp_path))
    b = Baseline()
    yield b
    b.close()


def teach(b, n, **kw):
    for i in range(n):
        b.record(rules.diagnose(make(**kw)), now=i)


def test_home_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMO_HOME", str(tmp_path / "x"))
    assert home() == tmp_path / "x"
    assert home().is_dir()


def test_load_buckets():
    assert [load_bucket(v) for v in (None, 0, 9.9, 10, 29, 30, 59, 60, 100)] == [-1, 0, 0, 1, 1, 2, 2, 3, 3]


def test_learning_then_ready(base):
    s = make(cpu={"temp": 70, "load": 45})
    assert base.lookup("CPU", s).n == 0
    teach(base, 29, cpu={"temp": 70, "load": 45})
    v = rules.diagnose(s, base.lookup)
    assert not v.baseline["CPU"].ready
    assert "CPU 배우는 중 (29/30)" in text.baseline_line(v)
    teach(base, 1, cpu={"temp": 70, "load": 45})
    ref = base.lookup("CPU", s)
    assert ref.ready and ref.p95 == pytest.approx(70)


def test_buckets_are_separate(base):
    teach(base, 40, cpu={"temp": 60, "load": 5})
    # 다른 부하 구간, 배터리, 성능 모드는 따로 센다
    assert base.lookup("CPU", make(cpu={"load": 80})).n == 0
    assert base.lookup("CPU", make(cpu={"load": 5}, power={"ac": False})).n == 0
    assert base.lookup("CPU", make(cpu={"load": 5}, power={"perf_mode": "Turbo"})).n == 0
    assert base.lookup("CPU", make(cpu={"load": 5})).n == 40


def test_above_usual_is_flagged(base):
    teach(base, 40, cpu={"temp": 62, "load": 40})
    v = rules.diagnose(make(cpu={"temp": 72, "load": 40}), base.lookup)
    assert v.level == Level.WARN
    assert any(f.rule == "baseline" for f in v.findings)


def test_bad_windows_not_learned(base):
    bad = rules.diagnose(make(cpu={"temp": 99, "load": 40}))
    assert bad.level == Level.BAD
    assert base.record(bad) is False
    assert base.lookup("CPU", make(cpu={"load": 40})).n == 0


def test_high_load_within_usual_downgraded(base):
    teach(base, 40, cpu={"temp": 93, "load": 90})
    v = rules.diagnose(make(cpu={"temp": 92, "load": 90}, procs=[Proc("game.exe", 1, cpu=80)]), base.lookup)
    assert v.level == Level.OK
    assert v.causes == []
    assert any("평소 범위" in line for line in text.summary(v))


def test_high_load_above_usual_not_downgraded(base):
    teach(base, 40, cpu={"temp": 85, "load": 90})
    v = rules.diagnose(make(cpu={"temp": 92, "load": 90}), base.lookup)
    assert v.level == Level.WARN


def test_bad_never_downgraded(base):
    teach(base, 40, cpu={"temp": 96, "load": 90})
    v = rules.diagnose(make(cpu={"temp": 97, "load": 90}), base.lookup)
    assert v.level == Level.BAD


def test_fan_max_needs_samples_and_floor(base):
    teach(base, 10, fans={"cpu_rpm": 6000})
    assert base.fan_max() == {}                       # 표본 부족
    teach(base, 30, fans={"cpu_rpm": 3500, "gpu_rpm": 3600})
    assert "GPU" not in base.fan_max()                # 최고가 4500 미만이면 아직 모름
    assert base.fan_max()["CPU"] == pytest.approx(6000, rel=0.05)


def test_fan_max_feeds_dust_rule(base):
    teach(base, 40, fans={"cpu_rpm": 6000})
    v = rules.diagnose(make(cpu={"temp": 80, "load": 5}, fans={"cpu_rpm": 5800}),
                       base.lookup, base.fan_max())
    assert "fan_max" in [c.key for c in v.causes]


def test_keep_per_bucket(base, monkeypatch):
    monkeypatch.setattr("thermo.baseline.KEEP_PER_BUCKET", 5)
    teach(base, 12, cpu={"temp": 60, "load": 5})
    assert base.lookup("CPU", make(cpu={"load": 5})).n == 5


def test_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("THERMO_HOME", str(tmp_path))
    b = Baseline()
    teach(b, 3, cpu={"temp": 60, "load": 5})
    b.close()
    b2 = Baseline()
    assert b2.lookup("CPU", make(cpu={"load": 5})).n == 3
    b2.close()
