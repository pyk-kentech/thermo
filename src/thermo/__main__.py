"""python -m thermo        : 창을 띄운다 (관리자 권한이 없으면 UAC 로 다시 띄움)
python -m thermo --once [--seconds 130] : 재고 판단을 터미널에 쓴다. 기준과의 비교는 정상 상태(120초)가 필요하다.
python -m thermo --record 초 --out 파일 : 1초 표본을 thermo.db 에 기록하고 초마다 한 줄로 쓴다(시험용).
python -m thermo --checkup 모드이름 [--room 24] : 기준 측정(건강검진)을 콘솔 창에서 진행한다(관리자)."""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

from . import text
from .collect import WINDOW, Collector


def once(seconds: int = WINDOW) -> str:
    from .diagnose import Diagnoser, load_reference
    from .store import Store
    col = Collector()
    store = Store()
    try:
        ref, label = load_reference(store)
        dg = Diagnoser(ref)
        for i in range(seconds):
            t0 = time.monotonic()
            s = col.sample()
            store.add(s)
            dg.add(s.t, s.cpu.power, s.gpu.power, s.gpu.temp, s.fans.cpu_rpm)
            if i < seconds - 1:
                time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
        return text.report(dg.diagnose(col.stats(), s.t), label)
    finally:
        store.close()
        col.close()


def _f(v, fmt="{:.0f}", none="–"):
    return none if v is None else fmt.format(v)


def record_line(s) -> str:
    c, g = s.cpu, s.gpu
    lim = ",".join(f"{k}{v:.0%}" for k, v in c.limit_frac.items() if v > 0) or "-"
    glim = ",".join(f"{k}{v:.0%}" for k, v in g.limit_frac.items() if v > 0) or "-"
    return (f"{time.strftime('%H:%M:%S', time.localtime(s.t))} "
            f"CPU {_f(c.temp)}°C 꼭짓점 {_f(c.peak_temp)} {_f(c.power, '{:.1f}')}W {_f(c.load)}% "
            f"{_f(None if c.clock is None else c.clock / 1000, '{:.2f}')}GHz 열 {_f(c.thermal_frac, '{:.0%}')} "
            f"제한[{lim}] 성능제한 {_f(c.perf_limit)}% | "
            f"GPU {_f(g.temp)}°C {_f(g.power, '{:.1f}')}W {_f(g.load)}% 제한[{glim}] | "
            f"팬 {_f(s.fans.cpu_rpm)}/{_f(s.fans.gpu_rpm)}")


def record(seconds: int, out: str | None) -> str:
    from .store import Store
    col = Collector()
    store = Store()
    lines = []
    try:
        if col.fast is not None:
            store.note("cpu_limits", vars(col.fast.limits))
            lines.append(f"CPU 한도(MSR): {vars(col.fast.limits)}")
        lines += [f"※ {n}" for n in col.notes]
        for i in range(seconds):
            t0 = time.monotonic()
            s = col.sample()
            store.add(s)
            lines.append(record_line(s))
            if out:
                with open(out, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
        return "\n".join(lines)
    finally:
        store.close()
        col.close()


def checkup(mode: str, room: float | None) -> int:
    from .paths import home
    from .checkup import Checkup, ConsoleUI
    from .store import Store
    sys.stdout.reconfigure(encoding="utf-8")
    ui = ConsoleUI(home() / "checkup.log")
    col = Collector()
    store = Store()
    try:
        for n in col.notes:
            ui.say(f"※ {n}")
        if col.fast is None or col.lhm is None:
            ui.say("CPU 기록(MSR)이나 CPU 센서를 읽지 못해 기준 측정을 할 수 없습니다. 관리자 권한과 PawnIO 를 확인하세요.")
            return 1
        store.note("cpu_limits", vars(col.fast.limits))
        ref = Checkup(col, store, mode, room, ui).run()
        return 0 if ref else 1
    finally:
        store.close()
        col.close()
        try:
            input("창을 닫으려면 Enter")
        except EOFError:
            pass


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
    ap.add_argument("--record", type=int, metavar="초", help="1초 표본을 기록한다(시험용)")
    ap.add_argument("--checkup", metavar="모드", help="기준 측정을 한다. 지금 Armoury Crate 성능 모드 이름을 준다")
    ap.add_argument("--room", type=float, help="기준 측정 때 방 온도(°C, 선택)")
    ap.add_argument("--rebuild", metavar="번호들", help="저장된 기준 측정(예: 1,2)을 합쳐 다시 계산한다. --mode 와 함께")
    ap.add_argument("--mode", help="--rebuild 때 성능 모드 이름")
    ap.add_argument("--out", help="결과를 이 파일에도 쓴다 (UTF-8)")
    ap.add_argument("--no-elevate", action="store_true", help="관리자 권한을 요청하지 않는다")
    ap.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)  # 재실행 표시
    a = ap.parse_args(argv)
    if a.checkup:
        return checkup(a.checkup, a.room)
    if a.rebuild:
        from .checkup import rebuild
        from .reference import summary
        from .store import Store
        st = Store()
        try:
            ref = rebuild(st, [int(x) for x in a.rebuild.split(",")], a.mode or "?")
        finally:
            st.close()
        sys.stdout.reconfigure(encoding="utf-8")
        print("\n".join(summary(ref)))
        return 0
    if a.record:
        result = record(a.record, a.out)
        if sys.stdout is not None and not a.out:
            sys.stdout.reconfigure(encoding="utf-8")
            print(result)
        return 0
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
