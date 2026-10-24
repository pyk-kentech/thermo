"""thermo 자신이 얼마나 무거운지 잰다: 센서별 읽기 시간, 프로세스 CPU 사용률, 메모리. 관리자로 실행.

사용: pythonw tools\\overhead.py --out 결과.txt [--seconds 60]
창은 띄우지 않고, 창의 작업 스레드와 같은 일(1초 읽기·기록·2초마다 판단)을 한다.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psutil  # noqa: E402

from thermo.collect import Collector  # noqa: E402
from thermo.diagnose import Diagnoser, load_reference  # noqa: E402
from thermo.model import FanReading  # noqa: E402
from thermo.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    me = psutil.Process()
    col = Collector()
    store = Store()
    ref, _ = load_reference(store)
    dg = Diagnoser(ref)
    t_parts: dict[str, list[float]] = defaultdict(list)

    def timed(name, f):
        t0 = time.perf_counter()
        r = f()
        t_parts[name].append((time.perf_counter() - t0) * 1000)
        return r

    # Collector.sample() 을 부분별로 나눠 잰다 (같은 순서)
    cpu0, wall0 = sum(me.cpu_times()[:2]), time.perf_counter()
    for i in range(a.seconds):
        t0 = time.monotonic()
        s = timed("전체 sample()", col.sample)
        timed("기록(store.add)", lambda: store.add(s))
        dg.add(s.t, s.cpu.power, s.gpu.power, s.gpu.temp, s.fans.cpu_rpm)
        if i % 2 == 1 and len(col.window) >= 10:
            timed("판단(diagnose)", lambda: dg.diagnose(col.stats(), s.t))
        time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
    cpu1, wall1 = sum(me.cpu_times()[:2]), time.perf_counter()
    # 센서별 (따로 몇 번 더)
    for _ in range(10):
        if col.fast:
            def cores():
                with col.fast.lock:
                    return col.fast.msr.core_temps(100)
            timed("MSR 코어별 온도 10개", cores)
        if col.gpu:
            timed("NVML GPU", col.gpu.read)
            timed("NVML 프로세스", col.gpu.process_use)
        if col.asus:
            timed("ASUS WMI 팬(2회 호출, 2초마다)", lambda: FanReading(*col.asus.fans()))
        if col.perf:
            timed("성능 카운터(제한·클럭)", col.perf.read)
        if col.power:
            timed("전원", col.power.read)
        timed("프로세스 목록(psutil)", col.procs.top)
        time.sleep(0.2)
    lines = [f"{a.seconds}초 동안 (창 없이 작업 스레드와 같은 일)"]
    cpu_pct = (cpu1 - cpu0) / (wall1 - wall0) * 100
    lines.append(f"thermo 프로세스 CPU: 코어 1개의 {cpu_pct:.1f}% = 전체(16스레드)의 {cpu_pct / psutil.cpu_count():.2f}%")
    lines.append(f"메모리(RSS): {me.memory_info().rss / 2**20:.0f} MB, 스레드 {me.num_threads()}개")
    lines.append("부분별 시간 (ms, 중앙값 / 최대):")
    for k, v in t_parts.items():
        lines.append(f"  {k:<26} {statistics.median(v):7.1f} / {max(v):7.1f}  (n={len(v)})")
    if col.fast:
        lines.append("MSR 10Hz 고리: 별도 스레드 (위 CPU 사용률에 포함)")
    # LHM 과 대조: 같은 순간 LHM 의 'CPU Package'·'Core Average' 와 thermo 가 MSR 로 읽은 값
    try:
        from LibreHardwareMonitor.Hardware import Computer, HardwareType
        pc = Computer()
        pc.IsCpuEnabled = True
        pc.Open()
        cpu = [h for h in pc.Hardware if h.HardwareType == HardwareType.Cpu][0]
        lines.append("LHM 과 대조 (패키지 / 코어 평균, °C):")
        for _ in range(8):
            cpu.Update()
            v = {x.Name: x.Value for x in cpu.Sensors if str(x.SensorType) == "Temperature"}
            s = col.sample()
            lines.append(f"  LHM {float(v['CPU Package']):.0f} / {float(v['Core Average']):.1f}   "
                         f"thermo {s.cpu.temp} / {s.cpu.temp_avg and round(s.cpu.temp_avg, 1)}")
            time.sleep(1)
        pc.Close()
    except Exception as e:
        lines.append(f"LHM 대조 실패: {type(e).__name__}: {e}")
    a.out.write_text("\n".join(lines), encoding="utf-8")
    store.close()
    col.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
