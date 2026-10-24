"""테스트용 창 값과 기준 (실제 기준 #5 의 숫자를 본뜸, DESIGN 10장 M7)."""

from thermo.model import CpuReading, FanReading, GpuReading, PowerReading, WindowStats
from thermo.reference import LinearModel, Reference, StagePoint


def make(cpu=None, gpu=None, fans=None, power=None, procs=None, notes=None) -> WindowStats:
    return WindowStats(
        n=10,
        cpu=CpuReading(**{"temp": 60, "load": 10, "power": 12.0, "thermal_frac": 0.0, "prochot_frac": 0.0, **(cpu or {})}),
        gpu=GpuReading(**{"temp": 52, "margin": 35, "load": 5, "power": 4.0, **(gpu or {})}),
        fans=FanReading(**{"cpu_rpm": 2500, "gpu_rpm": 2800, **(fans or {})}),
        power=PowerReading(**{"ac": True, "scheme": "균형 조정", **(power or {})}),
        procs=procs or [],
        notes=notes or [],
    )


def sp(stage, cw, gw, ct, gt, fc, thr):
    return StagePoint(stage, True, cw, gw, ct, gt, fc, fc + 300, thr, 0.0, 0.0)


def reference() -> Reference:
    return Reference(
        mode="Performance", room=None,
        stages=[sp("idle", 8.7, 3.3, 55, 52, 900, 0.0), sp("cpu4", 36.9, 3.5, 95, 52, 3300, 0.66),
                sp("cpu16", 49.9, 3.6, 94.5, 54, 3700, 0.52), sp("gpu", 19.1, 48.5, 72, 66, 3600, 0.01),
                sp("cpu2", 36.3, 16.8, 91, 56, 3700, 0.26)],
        cpu_model=LinearModel("cpu_temp", 44.1, {"cpu_power": 1.08, "gpu_power": 0.15}, 1.9, 23, ["idle", "gpu"],
                              ranges={"cpu_power": [8.6, 25.4], "gpu_power": [3.1, 50.1]}),
        gpu_model=LinearModel("gpu_temp", 46.1, {"gpu_power": 0.37, "cpu_power": 0.12}, 1.6, 54, ["gpu"],
                              ranges={"gpu_power": [3.1, 52.0], "cpu_power": [8.6, 45.5]}),
        fan_curve={"cpu": [(55, 900), (71, 3300), (75, 3300), (83, 3700), (95, 3700)], "gpu": []},
        cpu_sustained_w=47.0, cpu_thermal_limited=True, gpu_sustained_w=48.0, gpu_power_capped=0.0,
        idle_cpu_temp=55, idle_gpu_temp=52)
