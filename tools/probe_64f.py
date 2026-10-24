"""0x64F(제한 이유)를 코어마다 지정해서 읽어 본다. 관리자 + PawnIO. 읽기만 한다.

사용: pythonw tools\\probe_64f.py --out 결과.txt [--seconds 15]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thermo.sensors import dotnet  # noqa: E402
from thermo.sensors.msr import limit_names  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=int, default=15)
    a = ap.parse_args()
    dotnet.load()
    import System
    from LibreHardwareMonitor.Hardware import GroupAffinity
    from LibreHardwareMonitor.PawnIo import IntelMsr
    m = IntelMsr()
    lines = []

    def read(idx, cpu):
        ok, lo, hi = m.ReadMsr(System.UInt32(idx), System.UInt32(0), System.UInt32(0), GroupAffinity.Single(0, cpu))
        return (int(hi) << 32 | int(lo)) if ok else None

    for t in range(a.seconds):
        row = []
        for cpu in range(16):
            v = read(0x64F, cpu)
            th = read(0x19C, cpu)
            if v is None:
                row.append(f"{cpu}:실패")
                continue
            now = limit_names(v & 0xFFFF)
            log = limit_names((v >> 16) & 0xFFFF)
            row.append(f"{cpu}:{v & 0xFFFFFFFF:#010x} 지금{now} 기록{log} 19C={th and hex(th & 0xFFFFFFFF)}")
        lines.append(time.strftime("%H:%M:%S") + "\n  " + "\n  ".join(row))
        a.out.write_text("\n".join(lines), encoding="utf-8")
        time.sleep(1)
    m.Close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
