"""기준 측정용 CPU 부하. 프로세스 n 개가 멈추라고 할 때까지 바쁘게 돈다."""

from __future__ import annotations

import multiprocessing as mp


def _burn(stop) -> None:
    x = 0
    while not stop.is_set():
        for _ in range(200_000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF


class CpuLoad:
    def __init__(self, n: int) -> None:
        self.n = n
        self._stop = mp.Event()
        self._procs = [mp.Process(target=_burn, args=(self._stop,), daemon=True) for _ in range(n)]

    def start(self) -> CpuLoad:
        for p in self._procs:
            p.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for p in self._procs:
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()

    def __enter__(self) -> CpuLoad:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
