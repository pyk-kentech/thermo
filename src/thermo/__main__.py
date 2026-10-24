"""python -m thermo        : 창을 띄운다 (관리자 권한이 없으면 UAC 로 다시 띄움)
python -m thermo --once : 10초 재고 판정을 터미널에 쓴다."""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

from . import rules, text
from .baseline import Baseline
from .collect import WINDOW, Collector


def once(seconds: int = WINDOW) -> str:
    col = Collector(window=seconds)
    base = Baseline()
    try:
        for i in range(seconds):
            t0 = time.monotonic()
            col.sample()
            if i < seconds - 1:
                time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
        v = rules.diagnose(col.stats(), base.lookup, base.fan_max())
        base.record(v)
        return text.report(v)
    finally:
        base.close()
        col.close()


def relaunch_as_admin() -> bool:
    """같은 파이썬(창 없는 pythonw)으로 관리자 권한 재실행을 요청한다. 사용자가 거절하면 False."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), "-m thermo --elevated", None, 1)
    return rc > 32


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="thermo", description="FX707VU 온도 진단")
    ap.add_argument("--once", action="store_true", help="한 번 재고 터미널에 결과를 쓴다")
    ap.add_argument("--seconds", type=int, default=WINDOW, help="잴 시간(초)")
    ap.add_argument("--out", help="결과를 이 파일에도 쓴다 (UTF-8)")
    ap.add_argument("--no-elevate", action="store_true", help="관리자 권한을 요청하지 않는다")
    ap.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)  # 재실행 표시
    a = ap.parse_args(argv)
    if not a.once:
        from .sensors import is_admin
        if not (is_admin() or a.no_elevate or a.elevated) and relaunch_as_admin():
            return 0
        from .gui import run
        run()
        return 0
    print(f"{a.seconds}초 동안 재는 중…", file=sys.stderr)
    result = once(a.seconds)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(result + "\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
