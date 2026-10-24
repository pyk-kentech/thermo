"""CPU 레지스터(MSR)와 주변 온도 후보 센서를 읽어 본다. 관리자 + PawnIO 필요. 읽기만 한다.

사용: pythonw tools\\probe_msr.py --out 결과.json [--seconds 10]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thermo.sensors import dotnet  # noqa: E402

MSRS = {
    "PLATFORM_INFO": 0xCE,
    "IA32_THERM_STATUS": 0x19C,
    "TEMPERATURE_TARGET": 0x1A2,
    "TURBO_RATIO_LIMIT": 0x1AD,
    "IA32_PACKAGE_THERM_STATUS": 0x1B1,
    "VR_CURRENT_CONFIG": 0x601,
    "RAPL_POWER_UNIT": 0x606,
    "PKG_POWER_LIMIT": 0x610,
    "PKG_ENERGY_STATUS": 0x611,
    "PKG_POWER_INFO": 0x614,
    "CORE_PERF_LIMIT_REASONS": 0x64F,
}
# 0x64F 비트 (Intel SDM, 12~13세대 공통 배치로 알려진 것)
LIMIT_BITS = {
    0: "PROCHOT", 1: "열(Thermal)", 4: "Residency", 5: "평균 열 한도(RATL)", 6: "VR 열 경보",
    7: "VR 전류(TDC)", 8: "기타", 10: "PL1", 11: "PL2", 12: "최대 터보 한도", 13: "터보 전환 감쇠",
}


def bits(v: int, hi: int, lo: int) -> int:
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=10)
    a = ap.parse_args()
    out: dict = {}
    try:
        dotnet.load()
        import System
        from LibreHardwareMonitor.PawnIo import IntelMsr
        msr = IntelMsr()

        def read(idx: int) -> int | None:
            ok, val = msr.ReadMsr(System.UInt32(idx), System.UInt64(0))
            return int(val) if ok else None

        raw = {k: read(v) for k, v in MSRS.items()}
        out["raw"] = {k: (None if v is None else hex(v)) for k, v in raw.items()}
        dec = {}
        if raw["PLATFORM_INFO"] is not None:
            dec["기준 배수"] = bits(raw["PLATFORM_INFO"], 15, 8)
        if raw["TEMPERATURE_TARGET"] is not None:
            dec["TjMax"] = bits(raw["TEMPERATURE_TARGET"], 23, 16)
            dec["TjMax 오프셋"] = bits(raw["TEMPERATURE_TARGET"], 29, 24)
        if raw["RAPL_POWER_UNIT"] is not None:
            pu = 1 / (1 << bits(raw["RAPL_POWER_UNIT"], 3, 0))
            eu = 1 / (1 << bits(raw["RAPL_POWER_UNIT"], 12, 8))
            dec["전력 단위 W"], dec["에너지 단위 J"] = pu, eu
            if raw["PKG_POWER_LIMIT"] is not None:
                v = raw["PKG_POWER_LIMIT"]
                dec["PL1 W"] = bits(v, 14, 0) * pu
                dec["PL1 켜짐"] = bool(bits(v, 15, 15))
                dec["PL2 W"] = bits(v, 46, 32) * pu
                dec["PL2 켜짐"] = bool(bits(v, 47, 47))
                dec["잠김"] = bool(bits(v, 63, 63))
            if raw["PKG_POWER_INFO"] is not None:
                dec["TDP W"] = bits(raw["PKG_POWER_INFO"], 14, 0) * pu
        if raw["VR_CURRENT_CONFIG"] is not None:
            dec["전류 한도 A"] = bits(raw["VR_CURRENT_CONFIG"], 12, 0) / 8
        out["해석"] = dec

        # 몇 초 동안 제한 이유·온도 상태·패키지 전력(에너지 차)을 본다
        samples = []
        e0, t0 = read(0x611), time.monotonic()
        for _ in range(a.seconds):
            time.sleep(1)
            e1, t1 = read(0x611), time.monotonic()
            lim, pkg = read(0x64F), read(0x1B1)
            s = {"시각": time.strftime("%H:%M:%S")}
            if e0 is not None and e1 is not None and "에너지 단위 J" in dec:
                s["패키지 W"] = round(((e1 - e0) & 0xFFFFFFFF) * dec["에너지 단위 J"] / (t1 - t0), 1)
            if lim is not None:
                s["제한 지금"] = [n for b, n in LIMIT_BITS.items() if lim >> b & 1]
                s["제한 기록"] = [n for b, n in LIMIT_BITS.items() if lim >> (b + 16) & 1]
            if pkg is not None:
                s["TjMax 까지"] = bits(pkg, 22, 16)
                s["열 상태"] = bool(pkg & 1)
                s["PROCHOT"] = bool(pkg >> 2 & 1)
                s["전력 한도 알림"] = bool(pkg >> 10 & 1)
            samples.append(s)
            e0, t0 = e1, t1
        out["표본"] = samples
        msr.Close()
    except Exception as e:
        out["MSR 오류"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc(limit=4)

    # 주변 온도 후보
    try:
        from System.Management import ManagementObjectSearcher
        zones = ManagementObjectSearcher(r"root\wmi", "SELECT InstanceName, CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
        out["ACPI 열 영역"] = {str(z["InstanceName"]): round(int(z["CurrentTemperature"]) / 10 - 273.15, 1)
                            for z in zones.Get()}
    except Exception as e:
        out["ACPI 열 영역"] = f"{type(e).__name__}: {e}"
    try:
        from LibreHardwareMonitor.Hardware import Computer
        c = Computer()
        c.IsMotherboardEnabled = True
        c.IsControllerEnabled = True
        c.IsStorageEnabled = True
        c.IsMemoryEnabled = True
        c.Open()
        temps = {}
        for hw in c.Hardware:
            hw.Update()
            for sub in hw.SubHardware:
                sub.Update()
            for s in list(hw.Sensors) + [x for sub in hw.SubHardware for x in sub.Sensors]:
                if str(s.SensorType) == "Temperature" and s.Value is not None:
                    temps[f"{hw.Name} / {s.Name}"] = round(float(s.Value), 1)
        c.Close()
        out["LHM 그 밖의 온도"] = temps
    except Exception as e:
        out["LHM 그 밖의 온도"] = f"{type(e).__name__}: {e}"

    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
