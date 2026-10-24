from thermo import rules, text
from thermo.model import (
    GPU_HW_THERMAL, GPU_SW_POWER_CAP, CpuReading, FanReading, GpuReading, Level,
    PowerReading, Proc, Snapshot, WindowStats, summarize,
)
from thermo.rules import BaselineRef


def make(cpu=None, gpu=None, fans=None, power=None, procs=None) -> WindowStats:
    return WindowStats(
        n=10,
        cpu=CpuReading(**{"temp": 60, "load": 20, "tj_distance": 40, "perf_limit": 100, **(cpu or {})}),
        gpu=GpuReading(**{"temp": 50, "margin": 37, "load": 5, **(gpu or {})}),
        fans=FanReading(**{"cpu_rpm": 3300, "gpu_rpm": 3600, **(fans or {})}),
        power=PowerReading(**{"ac": True, "scheme": "균형 조정", **(power or {})}),
        procs=procs or [],
        notes=[],
    )


def keys(v):
    return [c.key for c in v.causes]


def test_normal():
    v = rules.diagnose(make())
    assert v.level == Level.OK
    assert v.causes == []
    assert "부하에 맞는 온도" in text.summary(v)[0]


# 6.1 절대 한계
def test_cpu_absolute_thresholds():
    assert rules.diagnose(make(cpu={"temp": 91, "load": 80})).level == Level.WARN
    assert rules.diagnose(make(cpu={"temp": 97, "load": 80})).level == Level.BAD


def test_cpu_tj_distance_with_throttle_is_bad():
    v = rules.diagnose(make(cpu={"temp": 88, "load": 90, "tj_distance": 2, "perf_limit": 80}))
    assert v.level == Level.BAD


def test_gpu_uses_driver_margin_not_fixed_temp():
    # 온도는 80 이지만 드라이버가 여유 20 이라고 하면 정상
    assert rules.diagnose(make(gpu={"temp": 80, "margin": 20, "load": 90})).level == Level.OK
    assert rules.diagnose(make(gpu={"temp": 80, "margin": 8, "load": 90})).level == Level.WARN
    assert rules.diagnose(make(gpu={"temp": 80, "margin": 2, "load": 90})).level == Level.BAD


def test_gpu_margin_fallback_to_target():
    assert rules.diagnose(make(gpu={"temp": 85, "margin": None, "load": 90})).level == Level.BAD


def test_gpu_thermal_reason_is_bad_and_named():
    v = rules.diagnose(make(gpu={"temp": 84, "margin": 12, "load": 40, "reasons": GPU_HW_THERMAL}))
    assert v.level == Level.BAD
    assert "thermal_throttle" in keys(v)


# 6.2 부하 대비
def test_cpu_hot_while_idle():
    assert rules.diagnose(make(cpu={"temp": 78, "load": 5})).level == Level.WARN
    assert rules.diagnose(make(cpu={"temp": 86, "load": 5})).level == Level.BAD


def test_gpu_idle_rule_skipped_when_cpu_busy():
    hot_idle_gpu = {"temp": 72, "load": 2}
    assert rules.diagnose(make(gpu=hot_idle_gpu)).level == Level.WARN
    assert rules.diagnose(make(gpu=hot_idle_gpu, cpu={"load": 80, "temp": 80})).level == Level.OK


# 6.3 평소값
def test_baseline_over_p95():
    look = lambda dev, s: BaselineRef(p95=70, n=50) if dev == "CPU" else None  # noqa: E731
    assert rules.diagnose(make(cpu={"temp": 80, "load": 50}), baseline=look).level == Level.WARN
    assert rules.diagnose(make(cpu={"temp": 86, "load": 50}), baseline=look).level == Level.BAD
    assert rules.diagnose(make(cpu={"temp": 77, "load": 50}), baseline=look).level == Level.OK


def test_baseline_ignored_with_few_samples():
    look = lambda dev, s: BaselineRef(p95=50, n=5)  # noqa: E731
    assert rules.diagnose(make(cpu={"temp": 80, "load": 50}), baseline=look).level == Level.OK


# 7장 원인
def test_high_load_names_process():
    v = rules.diagnose(make(cpu={"temp": 92, "load": 85},
                            procs=[Proc("blender.exe", 10, cpu=70), Proc("chrome.exe", 11, cpu=8)]))
    assert keys(v) == ["high_load"]
    assert text.summary(v)[0].startswith("blender.exe 가 CPU 를 70% 쓰고 있어")


def test_gpu_hot_from_game():
    v = rules.diagnose(make(gpu={"temp": 82, "margin": 5, "load": 95},
                            procs=[Proc("League of Legends.exe", 9896, cpu=10, gpu=90)]))
    assert keys(v) == ["high_load"]
    assert "League of Legends.exe 가 GPU 를 90%" in text.summary(v)[0]


def test_background_process_when_idle():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 10},
                            procs=[Proc("MsMpEng.exe", 6076, cpu=9, background=True)]))
    assert keys(v)[0] == "background"
    assert "Windows Defender 검사" in text.summary(v)[0]


def test_foreground_process_is_not_background_cause():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 10},
                            procs=[Proc("notepad.exe", 1, cpu=9, background=False)]))
    assert "background" not in keys(v)


def test_fan_at_max_means_dust():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 5}, fans={"cpu_rpm": 5900}),
                       fan_max={"CPU": 6200})
    assert "fan_max" in keys(v)
    assert "먼지" in text.cause_sentence(next(c for c in v.causes if c.key == "fan_max"))


def test_slow_fan():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 5}, fans={"cpu_rpm": 1800}))
    assert "fan_slow" in keys(v)
    assert "Silent" in text.cause_sentence(next(c for c in v.causes if c.key == "fan_slow"))


def test_silent_mode_sentence():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 5}, fans={"cpu_rpm": 1800},
                            power={"perf_mode": "Silent"}))
    c = next(c for c in v.causes if c.key == "fan_slow")
    assert text.cause_sentence(c) == "Armoury Crate 가 Silent 모드라 팬이 천천히 돕니다."


def test_cpu_thermal_vs_power_limit():
    hot = rules.diagnose(make(cpu={"temp": 96, "load": 20, "tj_distance": 4, "perf_limit": 70}))
    assert "thermal_throttle" in keys(hot)
    cool = rules.diagnose(make(cpu={"temp": 80, "load": 10, "tj_distance": 20, "perf_limit": 70, "power": 45}))
    assert "power_limit" in keys(cool)


def test_gpu_power_cap_is_not_thermal():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 5},
                            gpu={"temp": 70, "margin": 17, "load": 30, "reasons": GPU_SW_POWER_CAP}))
    assert "thermal_throttle" not in keys(v)


def test_unknown_cause():
    v = rules.diagnose(make(cpu={"temp": 80, "load": 10}, fans={"cpu_rpm": None, "gpu_rpm": None}))
    assert keys(v) == ["unknown"]


def test_at_most_two_causes():
    v = rules.diagnose(make(cpu={"temp": 88, "load": 10, "tj_distance": 20, "perf_limit": 60},
                            fans={"cpu_rpm": 1500},
                            procs=[Proc("MsMpEng.exe", 1, cpu=9, background=True)]))
    assert len(v.causes) == 2


def test_battery_extra():
    v = rules.diagnose(make(power={"ac": False}))
    assert "배터리로 동작 중" in text.summary(v)[-1]


# 창 요약
def test_summarize_median_ignores_spike():
    snaps = [Snapshot(t=i, cpu=CpuReading(temp=60, load=20)) for i in range(9)]
    snaps.append(Snapshot(t=9, cpu=CpuReading(temp=99, load=20)))
    s = summarize(snaps)
    assert s.cpu.temp == 60
    assert s.cpu.temp_max == 99
    assert rules.diagnose(s).level == Level.OK


def test_summarize_reasons_need_half_window():
    snaps = [Snapshot(t=i, gpu=GpuReading(temp=60, reasons=GPU_HW_THERMAL if i == 0 else 0)) for i in range(10)]
    assert summarize(snaps).gpu.reasons == 0


def test_summarize_procs_averaged():
    snaps = [Snapshot(t=i, procs=[Proc("a.exe", 1, cpu=20)] if i < 5 else []) for i in range(10)]
    s = summarize(snaps)
    assert s.procs[0].cpu == 10


def test_report_renders():
    v = rules.diagnose(make(cpu={"temp": 92, "load": 85, "power": 60, "clock": 3900},
                            procs=[Proc("blender.exe", 10, cpu=70)]))
    out = text.report(v)
    assert out.startswith("● 주의")
    assert "근거" in out and "3.9GHz" in out
