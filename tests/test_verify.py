"""M10 검증 채점 함수. 판단 흐름(Timeline)을 만들어 채점이 맞게 되는지 본다."""

from thermo import verify as V
from thermo.diagnose import Diagnosis
from thermo.model import Level


def dg(level=Level.OK, keys=(), situation=(), judgement=(), headline=None):
    return Diagnosis(level, headline or f"{level.label} — x", list(situation), list(judgement), [], [], list(keys))


def tl(*ds, step=10):
    return [(step * (i + 1), d) for i, d in enumerate(ds)]


def test_idle_pass_and_fail():
    assert V.expect_idle(tl(dg(keys=["not_steady"]), dg(keys=["normal"])))[0]
    assert not V.expect_idle(tl(dg(keys=["not_steady"])))[0]
    assert not V.expect_idle(tl(dg(Level.WARN, keys=["cpu_hotter"])))[0]


def test_cpu_full():
    early = [dg(keys=["not_steady"], situation=["프로그램  python.exe CPU 90%"])] * 3
    late = [dg(keys=["cpu_design_limit"], situation=["프로그램  python.exe CPU 90%"])] * 5
    ok, why = V.expect_cpu_full(tl(*early, *late))
    assert ok, why
    # 첫 30초 안에 비교해 버리면 실패
    ok, why = V.expect_cpu_full(tl(*late))
    assert not ok and "30초" in why
    # 범인을 못 짚으면 실패
    ok, why = V.expect_cpu_full(tl(*([dg(keys=["not_steady"])] * 3), *([dg(keys=["cpu_design_limit"])] * 5)))
    assert not ok and "python.exe" in why


def test_defender():
    hit = dg(keys=["normal"], situation=["프로그램  MsMpEng.exe(Windows Defender 검사) CPU 9% (창 없음)"])
    assert V.expect_defender(tl(hit))[0]
    no_bg = dg(keys=["normal"], situation=["프로그램  MsMpEng.exe(Windows Defender 검사) CPU 9%"])
    assert "창 없는" in V.expect_defender(tl(no_bg))[1]


def test_battery():
    good = dg(keys=["battery", "cpu_power_limited"], judgement=["… 배터리라 전력 한도가 낮습니다."])
    assert V.expect_battery(tl(good, good))[0]
    wrong = dg(Level.WARN, keys=["battery", "cpu_throttle_low_power"])
    ok, why = V.expect_battery(tl(wrong, wrong))
    assert not ok and "냉각 문제로 잘못" in why


def test_other_mode():
    assert V.expect_other_mode(tl(dg(Level.WARN, keys=["cpu_hotter", "fan_slow"])))[0]
    assert V.expect_other_mode(tl(dg(keys=["cpu_power_limited"])))[0]
    ok, why = V.expect_other_mode(tl(dg(Level.WARN, keys=["cpu_hotter"])))
    assert not ok and "냉각 저하로 잘못" in why


def test_game():
    g = dg(keys=["normal"], situation=["프로그램  Client-Win64-Shipping.exe(게임(언리얼 엔진)) CPU 9% · GPU 24%"])
    assert V.expect_game(tl(g))[0]
    assert not V.expect_game(tl(dg(keys=["normal"])))[0]


def test_carried_is_not_counted_as_comparison():
    c = dg(Level.WARN, keys=["not_steady", "carried"])
    assert V.compared(tl(c)) == []


def test_scenario_list():
    assert [s.key for s in V.SCENARIOS] == ["idle", "cpu", "defender", "battery", "silent", "game"]
    assert all(s.seconds >= 180 for s in V.SCENARIOS)
