"""CPU 부하 시험: 모든 논리 코어를 정해진 시간 동안 바쁘게 돌린다 (M5 실사용 확인용).

사용: .venv\Scripts\python tools\stress.py [초=90] [프로세스 수=논리 코어 수]
Ctrl+C 로 언제든 멈춘다.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time


def burn(until: float) -> None:
    x = 0
    while time.time() < until:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 90
    n = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count() or 1
    until = time.time() + secs
    print(f"CPU 부하 시작: 프로세스 {n}개, {secs:.0f}초", flush=True)
    procs = [mp.Process(target=burn, args=(until,), daemon=True) for _ in range(n)]
    for p in procs:
        p.start()
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
    print("CPU 부하 끝", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
