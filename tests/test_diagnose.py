"""M8 판단 2판: 사실 → 기준과 비교 → 원인."""

import json
from pathlib import Path

import pytest

from thermo import text
from thermo.diagnose import Diagnoser, diagnose
from thermo.model import Level, Proc, summarize
from thermo.reference import Reference
from thermo.store import Steady, snapshot_from_row

from fake import make, reference

STEADY = Steady(True, "")
REF = reference()


def cpu_pred(c, g):
    return 44.1 + 1.08 * c + 0.15 * g


def gpu_pred(g, c):
    return 46.1 + 0.37 * g + 0.12 * c


def d(stats, ref=REF, steady=STEADY, ambient=None):
    return diagnose(stats, ref, steady, ambient)


# --- 기준이 없을 때 / 정상 상태가 아닐 때 -------------------------------------------
def test_no_reference_reports_facts_only():
    r = d(make(cpu={"thermal_frac": 0.5, "temp": 95, "power": 45}), ref=None)
    assert r.level == Level.OK
    assert "기준 측정 전" in r.notes[0]
    assert "cpu_throttle" in r.keys


def test_not_steady_defers_comparison():
    hot = make(cpu={"temp": 90, "power": 12})          # 기준 기대 57°C 보다 훨씬 뜨겁지만
    r = d(hot, steady=Steady(False, "CPU 전력 평균이 바뀌는 중입니다(10W → 30W)"))
    assert r.keys[0] == "not_steady" and r.level == Level.OK
    assert "비교를 미룹니다" in r.judgement[0]


# --- 6.2 비교 -----------------------------------------------------------------------
def test_normal_idle():
    c, g = 9.0, 3.5
    r = d(make(cpu={"temp": round(cpu_pred(c, g)), "power": c}, gpu={"power": g, "temp": round(gpu_pred(g, c))}))
    assert r.level == Level.OK and r.keys == ["normal"]
    assert any("CPU 기대" in e for e in r.evidence)


def test_cpu_hotter_warn_and_bad():
    c, g = 20.0, 4.0
    base = cpu_pred(c, g)
    warn = d(make(cpu={"temp": base + 7, "power": c}, gpu={"power": g, "temp": gpu_pred(g, c)},
                  fans={"cpu_rpm": 3400}))
    assert warn.level == Level.WARN and "cpu_hotter" in warn.keys
    assert "열이 평소만큼 빠지지 않습니다" in " ".join(warn.judgement)
    bad = d(make(cpu={"temp": base + 13, "power": c}, gpu={"power": g, "temp": gpu_pred(g, c)},
                 fans={"cpu_rpm": 3700}))
    assert bad.level == Level.BAD
    assert "평소보다 13°C" in bad.headline


def test_within_three_sigma_is_normal():
    c, g = 20.0, 4.0
    r = d(make(cpu={"temp": cpu_pred(c, g) + 5, "power": c}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 3400}))
    assert r.level == Level.OK


def test_hot_and_fan_slow_and_power_limited_means_other_mode():
    c, g = 20.0, 4.0
    r = d(make(cpu={"temp": cpu_pred(c, g) + 8, "power": c, "load": 95}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 2000}))
    assert {"cpu_hotter", "fan_slow", "cpu_power_limited"} <= set(r.keys)
    assert "다른 성능 모드" in " ".join(r.judgement)


def test_hot_and_fan_slow_without_power_limit_means_fan():
    c, g = 20.0, 4.0
    r = d(make(cpu={"temp": cpu_pred(c, g) + 8, "power": c, "load": 30}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 2000}))
    assert "팬 설정이나 팬 자체" in " ".join(r.judgement)


def test_gpu_hotter():
    g, c = 40.0, 18.0
    r = d(make(cpu={"temp": cpu_pred(c, g), "power": c}, gpu={"power": g, "temp": gpu_pred(g, c) + 6},
               fans={"cpu_rpm": 3300}))
    assert "gpu_hotter" in r.keys and r.level == Level.WARN


# --- 95°C 스로틀: 몇 W 에서 묶였나 ---------------------------------------------------
def test_throttle_at_reference_power_is_design_limit():
    r = d(make(cpu={"temp": 95, "power": 47, "load": 100, "thermal_frac": 0.5}, fans={"cpu_rpm": 3700}))
    assert r.level == Level.OK and "cpu_design_limit" in r.keys
    assert "냉각 설계 한계" in r.headline and "고장은 아닙니다" in " ".join(r.judgement)


def test_throttle_at_low_power_is_cooling_problem():
    warn = d(make(cpu={"temp": 95, "power": 28, "load": 60, "thermal_frac": 0.4}, fans={"cpu_rpm": 3700}))
    assert warn.level == Level.WARN and "cpu_throttle_low_power" in warn.keys
    bad = d(make(cpu={"temp": 95, "power": 18, "load": 40, "thermal_frac": 0.4}, fans={"cpu_rpm": 3700}))
    assert bad.level == Level.BAD


# --- 6.1 하드웨어 사실 -----------------------------------------------------------------
def test_external_prochot():
    r = d(make(cpu={"temp": 70, "peak_temp": 74, "prochot_frac": 0.4}))
    assert r.level == Level.BAD and "external_prochot" in r.keys
    assert "충전기" in r.judgement[0]


def test_gpu_thermal_limit():
    r = d(make(gpu={"temp": 86, "margin": 2, "power": 60, "limit_frac": {"thermal": 0.3}}))
    assert "gpu_thermal" in r.keys and r.level >= Level.WARN


def test_battery_power_limited():
    r = d(make(cpu={"temp": cpu_pred(25, 4), "power": 25, "load": 95}, power={"ac": False}, fans={"cpu_rpm": 3300}))
    assert {"battery", "cpu_power_limited"} <= set(r.keys)
    assert "배터리라 전력 한도가 낮습니다" in " ".join(r.judgement)
    assert any("간접 판단" in e for e in r.evidence)


# --- 원인: 범인 프로세스, 히트파이프, 범위 밖 ---------------------------------------------
def test_names_culprits_with_watts_and_background():
    c, g = 30.0, 48.0
    procs = [Proc("Client-Win64-Shipping.exe", 1, cpu=9.0, gpu=24.0),
             Proc("LosslessScaling.exe", 2, cpu=1.0, gpu=13.0),
             Proc("MsMpEng.exe", 3, cpu=4.0, background=True)]
    r = d(make(cpu={"temp": cpu_pred(c, g), "power": c, "load": 20}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 3400}, procs=procs))
    j = " ".join(r.judgement)
    assert "Client-Win64-Shipping.exe(게임(언리얼 엔진))(CPU 9%·약 14W, GPU 24%)" in j
    assert "LosslessScaling.exe(Lossless Scaling 프레임 생성)" in j
    assert "MsMpEng.exe(Windows Defender 검사)(CPU 4%·약 6W) — 창 없는 작업" in j
    assert "평소 범위" in j
    assert r.situation[0].startswith("프로그램  Client-Win64-Shipping.exe")


def test_shared_heatpipe():
    c, g = 8.0, 50.0
    r = d(make(cpu={"temp": cpu_pred(c, g), "power": c}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 3300}))
    assert "shared_heatpipe" in r.keys and "같은 히트파이프" in " ".join(r.judgement)


def test_out_of_range_note():
    c, g = 15.0, 90.0
    r = d(make(cpu={"temp": cpu_pred(c, g), "power": c}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 3300}))
    assert any("GPU 90W 는 기준 측정 범위(3–50W) 밖" in n for n in r.notes)


def test_report_text():
    r = d(make(cpu={"temp": 95, "power": 47, "load": 100, "thermal_frac": 0.5}, fans={"cpu_rpm": 3700}))
    out = text.report(r, "기준 측정 #5")
    assert out.startswith("● 정상 — 냉각 설계 한계")
    assert "지금 상황" in out and "판단" in out and "기준: 기준 측정 #5" in out


# --- 방 온도 보정 (Diagnoser) ---------------------------------------------------------
def test_ambient_offset_from_idle_applies_to_load():
    dg = Diagnoser(REF)
    t = 0.0
    idle = make(cpu={"temp": cpu_pred(9, 3.5) + 4, "power": 9.0}, gpu={"power": 3.5, "temp": gpu_pred(3.5, 9) + 4})
    for t in range(130):
        dg.add(float(t), 9.0, 3.5, idle.gpu.temp)
    r = dg.diagnose(idle, 129.0)
    assert dg.ambient["CPU"] == pytest.approx(4, abs=0.2)
    assert r.level == Level.OK
    # 부하에서 기대보다 +7°C — 그중 4°C 는 방 온도 → 남는 3°C 는 3σ 안
    load = make(cpu={"temp": cpu_pred(22, 4) + 7, "power": 22.0}, gpu={"power": 4.0, "temp": gpu_pred(4, 22) + 4},
                fans={"cpu_rpm": 3300})
    for t in range(130, 260):
        dg.add(float(t), 22.0, 4.0, load.gpu.temp)
    r2 = dg.diagnose(load, 259.0)
    assert r2.level == Level.OK
    assert any("방 온도 보정 +4°C" in e for e in r2.evidence)


def test_ambient_clipped():
    dg = Diagnoser(REF)
    idle = make(cpu={"temp": cpu_pred(9, 3.5) + 15, "power": 9.0}, gpu={"power": 3.5, "temp": 60})
    for t in range(130):
        dg.add(float(t), 9.0, 3.5, 60.0)
    dg.diagnose(idle, 129.0)
    assert dg.ambient["CPU"] == 6.0


# --- 실제 기록 재생 (M7 기준 측정 #1·#2 일부, tests/data/real_checkup.json) -----------------
REAL = json.loads((Path(__file__).parent / "data" / "real_checkup.json").read_text(encoding="utf-8"))
REAL_REF = Reference.from_json(REAL["reference"])


def replay(stage: str) -> list:
    dg = Diagnoser(REAL_REF)
    snaps = [snapshot_from_row(r) for r in REAL["stages"][stage]]
    out = []
    for i, s in enumerate(snaps):
        dg.add(s.t, s.cpu.power, s.gpu.power, s.gpu.temp, s.fans.cpu_rpm)
        if i >= 9 and i % 10 == 9:
            out.append(dg.diagnose(summarize(snaps[i - 9:i + 1]), s.t))
    return out


def test_real_idle_is_normal():
    rs = replay("idle#1")
    compared = [r for r in rs if "not_steady" not in r.keys]
    assert compared, "정상 상태 판단이 한 번도 없음"
    assert all(r.level == Level.OK for r in compared), [(r.headline, r.evidence) for r in compared if r.level]


def test_real_full_cpu_load_is_design_limit_not_fault():
    rs = [r for r in replay("cpu16#1") if "not_steady" not in r.keys]
    assert rs and all(r.level == Level.OK and "cpu_design_limit" in r.keys for r in rs), \
        [(r.headline, r.evidence) for r in rs]


@pytest.mark.parametrize("stage", ["gpu#1", "gpu#2", "both#2"])
def test_real_game_is_normal_and_names_game(stage):
    rs = [r for r in replay(stage) if "not_steady" not in r.keys]
    assert rs
    assert all(r.level == Level.OK for r in rs), [(r.headline, r.evidence) for r in rs if r.level]
    assert any("Client-Win64-Shipping.exe" in " ".join(r.situation) for r in rs)


# --- M8 보강: 코어 평균 모델, 실제 판단 방식 σ, 시스템 프로세스 -----------------------------
def test_core_average_model_is_used_when_recorded():
    from thermo import reference as R
    rows = []
    for k, (p, avg) in enumerate([(9, 50), (15, 56), (20, 61), (25, 66)]):
        for i in range(70):
            rows.append({"ts": k * 1000 + i, "cpu_power": p, "gpu_power": 4.0 + (k % 2) * 10,
                         "cpu_temp": avg + 10, "cpu_temp_avg": avg, "gpu_temp": 50, "fan_cpu": 3000, "fan_gpu": 3000,
                         "cpu_thermal_frac": 0.0, "gpu_limits": None, "procs": "[]"})
    stages = [(f"s{k}", rows[k * 70:(k + 1) * 70], True) for k in range(4)]
    ref = R.build("Performance", None, stages)
    assert ref.cpu_model.target == "cpu_temp_avg"
    assert ref.cpu_model.coef["cpu_power"] == pytest.approx(1.0, abs=0.01)
    # 판단: 패키지는 15°C 높아도(한 코어 몰림) 코어 평균이 기대대로면 정상
    s = make(cpu={"temp": 85, "temp_avg": 61, "power": 20}, gpu={"power": 4, "temp": 50}, fans={"cpu_rpm": 3300})
    r = diagnose(s, ref, STEADY)
    assert "cpu_hotter" not in r.keys
    assert any(e.startswith("CPU 코어 평균 기대") for e in r.evidence)


def test_runtime_sigma_widens_but_never_narrows():
    from thermo import reference as R
    ref = reference()
    R.apply_runtime_sigma(ref, [3.0, -3.0, 2.0, -2.0, 3.0], [0.1] * 5)
    assert ref.cpu_model.sigma == pytest.approx(7 ** 0.5)
    assert ref.gpu_model.sigma == 1.6
    assert any("넓혔습니다" in n for n in ref.notes)


def test_dwm_is_not_called_background_work():
    procs = [Proc("dwm.exe", 1, cpu=1, gpu=10, background=True), Proc("MsMpEng.exe", 2, cpu=4, background=True)]
    r = d(make(cpu={"temp": cpu_pred(12, 4), "power": 12}, gpu={"power": 4, "temp": gpu_pred(4, 12)}, procs=procs))
    text_all = " ".join(r.judgement) + " ".join(r.situation)
    assert "dwm.exe(Windows 화면 그리기)(CPU 1%·약 1W, GPU 10%)," in text_all
    assert "GPU 10%) — 창 없는" not in text_all
    assert "MsMpEng.exe(Windows Defender 검사)(CPU 4%" in text_all and "창 없는 작업" in text_all


def test_last_comparison_carried_while_load_changes():
    dg = Diagnoser(REF)
    c, g = 20.0, 4.0
    hot = make(cpu={"temp": cpu_pred(c, g) + 9, "power": c}, gpu={"power": g, "temp": gpu_pred(g, c)},
               fans={"cpu_rpm": 3400})
    for t in range(130):
        dg.add(float(t), c, g, hot.gpu.temp, 3400.0)
    first = dg.diagnose(hot, 129.0)
    assert first.level == Level.WARN
    for t in range(130, 150):                      # 부하가 바뀐다
        dg.add(float(t), 45.0, g, hot.gpu.temp, 3400.0)
    later = dg.diagnose(hot, 149.0)
    assert "not_steady" in later.keys and "carried" in later.keys
    assert later.level == Level.WARN and "20초 전" in later.headline
    assert later.judgement[-1].startswith("20초 전 마지막 비교")
