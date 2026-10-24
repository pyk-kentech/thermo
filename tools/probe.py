"""M1 센서 확인: 이 노트북에서 각 출처가 실제로 무엇을 돌려주는지 적는다.

사용: python tools/probe.py [--out 파일]  (관리자 권한이면 ASUS WMI·LHM 도 본다)
읽기만 한다. 아무 설정도 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LHM_DIR = ROOT / "vendor" / "lhm"

report: dict[str, object] = {}
ONLY: set[str] = set()


def section(name):
    def wrap(fn):
        if ONLY and name not in ONLY and name != "dotnet":
            return fn
        try:
            report[name] = fn()
        except Exception as e:  # 확인용이므로 무엇이 실패했는지 그대로 남긴다
            report[name] = {"오류": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)}
        return fn
    return wrap


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def run_probe() -> None:
    report["관리자"] = is_admin()

    @section("nvml")
    def _():
        import pynvml as n
        n.nvmlInit()
        h = n.nvmlDeviceGetHandleByIndex(0)
        out = {"이름": n.nvmlDeviceGetName(h), "드라이버": n.nvmlSystemGetDriverVersion()}

        def g(key, f):
            try:
                out[key] = f()
            except n.NVMLError as e:
                out[key] = f"읽기 실패: {e}"
        g("온도", lambda: n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU))
        g("한계까지_남은_온도", lambda: n.nvmlDeviceGetMarginTemperature(h))
        g("목표온도_slowdown", lambda: n.nvmlDeviceGetTemperatureThreshold(h, n.NVML_TEMPERATURE_THRESHOLD_SLOWDOWN))
        g("목표온도_gpu_max", lambda: n.nvmlDeviceGetTemperatureThreshold(h, n.NVML_TEMPERATURE_THRESHOLD_GPU_MAX))
        g("사용률", lambda: n.nvmlDeviceGetUtilizationRates(h).gpu)
        g("전력_W", lambda: n.nvmlDeviceGetPowerUsage(h) / 1000)
        g("전력한도_W", lambda: n.nvmlDeviceGetEnforcedPowerLimit(h) / 1000)
        g("SM클럭", lambda: n.nvmlDeviceGetClockInfo(h, n.NVML_CLOCK_SM))
        g("최대SM클럭", lambda: n.nvmlDeviceGetMaxClockInfo(h, n.NVML_CLOCK_SM))
        g("스로틀이유", lambda: hex(n.nvmlDeviceGetCurrentClocksEventReasons(h)))
        g("팬", lambda: n.nvmlDeviceGetFanSpeed(h))
        g("P상태", lambda: n.nvmlDeviceGetPerformanceState(h))
        g("그래픽_프로세스", lambda: [(p.pid, p.usedGpuMemory) for p in n.nvmlDeviceGetGraphicsRunningProcesses(h)][:10])
        g("프로세스_사용률", lambda: [(s.pid, s.smUtil) for s in n.nvmlDeviceGetProcessUtilization(h, 0)][:10])
        n.nvmlShutdown()
        return out

    @section("psutil")
    def _():
        import psutil
        procs = list(psutil.process_iter(["name"]))
        for p in procs:
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(1.0)
        rows = []
        for p in procs:
            try:
                rows.append((p.cpu_percent(None) / psutil.cpu_count(), p.info["name"], p.pid))
            except psutil.Error:
                pass
        rows.sort(reverse=True)
        b = psutil.sensors_battery()
        return {
            "전체CPU": psutil.cpu_percent(0.5),
            "상위": [f"{n} ({pid}) {c:.1f}%" for c, n, pid in rows[:8]],
            "배터리": None if b is None else {"퍼센트": b.percent, "어댑터": b.power_plugged},
            "cpu_freq": psutil.cpu_freq()._asdict(),
        }

    @section("dotnet")
    def _():
        import clr  # pythonnet 이 .NET Framework 를 띄운다
        clr.AddReference("System.Management")
        import System
        return {"런타임": str(System.Environment.Version)}

    @section("perf_counter")
    def _():
        from System.Diagnostics import PerformanceCounter, PerformanceCounterCategory
        out = {}
        for cat, ctr, inst in [
            ("Processor Information", "% Performance Limit", "_Total"),
            ("Processor Information", "% Processor Performance", "_Total"),
            ("Processor Information", "% Processor Utility", "_Total"),
        ]:
            pc = PerformanceCounter(cat, ctr, inst)
            pc.NextValue()
            time.sleep(0.5)
            out[ctr] = round(pc.NextValue(), 1)
        names = list(PerformanceCounterCategory("GPU Engine").GetInstanceNames())
        out["GPU_Engine_인스턴스_수"] = len(names)
        out["GPU_Engine_예"] = names[:3]
        return out

    @section("wmi_battery")
    def _():
        from System.Management import ManagementObjectSearcher
        s = ManagementObjectSearcher(r"root\wmi", "SELECT PowerOnline, Charging, Discharging FROM BatteryStatus")
        return [{p.Name: str(p.Value) for p in o.Properties} for o in s.Get()]

    @section("powercfg")
    def _():
        r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True)
        return r.stdout.decode("mbcs", "replace").strip()

    @section("asus_wmi")
    def _():
        import System
        from System.Management import ManagementObjectSearcher
        s = ManagementObjectSearcher(r"root\wmi", "SELECT * FROM AsusAtkWmi_WMNB")
        objs = list(s.Get())
        if not objs:
            return "AsusAtkWmi_WMNB 인스턴스 없음 (관리자 권한 필요할 수 있음)"
        o = objs[0]
        ids = {
            "CPU팬": 0x00110013, "GPU팬": 0x00110014, "가운데팬": 0x00110031,
            "성능모드": 0x00120075, "성능모드_vivo": 0x00110019,
            "CPU온도": 0x00120094, "GPU온도": 0x00120097,
            "충전한도": 0x00120057,
        }
        out = {}
        for name, dev in ids.items():
            vals = []
            try:
                for _ in range(3):
                    args = o.GetMethodParameters("DSTS")
                    args["Device_ID"] = System.UInt32(dev)  # 파이썬 int 로는 '일반 오류'가 난다
                    res = o.InvokeMethod("DSTS", args, None)
                    vals.append(int(res["device_status"]))
                    time.sleep(0.3)
                out[name] = {"raw": [hex(v) for v in vals], "하위16": [v & 0xFFFF for v in vals]}
            except Exception as e:
                out[name] = f"읽기 실패: {type(e).__name__}: {str(e).splitlines()[0]}"
        return out

    @section("lhm")
    def _():
        import clr
        sys.path.insert(0, str(LHM_DIR))
        clr.AddReference(str(LHM_DIR / "LibreHardwareMonitorLib.dll"))
        from LibreHardwareMonitor.Hardware import Computer
        c = Computer()
        c.IsCpuEnabled = True
        c.IsGpuEnabled = True
        c.IsMotherboardEnabled = True
        c.IsControllerEnabled = True
        c.IsBatteryEnabled = True
        c.Open()
        out = {}
        for _ in range(2):  # 첫 갱신은 값이 비는 센서가 있다
            for hw in c.Hardware:
                hw.Update()
                for sub in hw.SubHardware:
                    sub.Update()
            time.sleep(1.0)
        for hw in c.Hardware:
            items = list(hw.Sensors) + [s for sub in hw.SubHardware for s in sub.Sensors]
            out[f"{hw.HardwareType}: {hw.Name}"] = [
                f"{s.SensorType} | {s.Name} = {None if s.Value is None else round(float(s.Value), 2)}"
                for s in items
            ]
        c.Close()
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", action="append", default=[], help="이 절만 실행 (여러 번 가능)")
    a = ap.parse_args()
    ONLY.update(a.only)
    run_probe()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
