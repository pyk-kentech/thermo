"""Armoury Crate 성능 모드 값 찾기 (M5). 관리자로 실행.

1초마다 ASUS WMI 후보 장치 값과 팬·CPU 전력을 쓴다. 실행해 둔 채로 Armoury Crate 에서
Silent → Performance → Turbo 순서로 모드를 바꾸면, 어느 값이 모드를 따라 바뀌는지 보인다.
읽기만 한다.

사용: pythonw tools\watch_mode.py --out 로그.txt [--seconds 120]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thermo.sensors.asus import AsusWmi  # noqa: E402
from thermo.sensors.lhm import LhmCpu  # noqa: E402

CANDIDATES = {
    "throttle_policy": 0x00120075,
    "throttle_policy_vivo": 0x00110019,
    "fan_boost": 0x00110018,
    "ppt_pl1": 0x001200A0,
    "ppt_pl2": 0x001200A1,
    "gpu_boost": 0x00120098,
    "gpu_temp_target": 0x00120099,
}


def raw(a: AsusWmi, dev: int) -> str:
    args = a._o.GetMethodParameters("DSTS")
    args["Device_ID"] = a._uint(dev)
    try:
        return f"{int(a._o.InvokeMethod('DSTS', args, None)['device_status']):#010x}"
    except Exception as e:
        return f"오류({type(e).__name__})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=120)
    a = ap.parse_args()
    asus, cpu = AsusWmi(), LhmCpu()
    with a.out.open("w", encoding="utf-8") as f:
        f.write("시각 " + " ".join(CANDIDATES) + " cpu_fan gpu_fan cpu_W cpu_C\n")
        for _ in range(a.seconds):
            r = cpu.read()
            fans = asus.fans()
            vals = " ".join(raw(asus, d) for d in CANDIDATES.values())
            f.write(f"{time.strftime('%H:%M:%S')} {vals} {fans[0]} {fans[1]} {r.power} {r.temp}\n")
            f.flush()
            time.sleep(1)
    cpu.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
