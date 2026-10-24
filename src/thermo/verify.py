"""M10 검증 (DESIGN 2판 10장): 상황을 실제로 만들고, 진단이 맞는지 자동으로 채점한다.

사용: python -m thermo --verify [시나리오,...]   (관리자 콘솔, 사용자가 안내를 따라 상황을 만든다)
결과는 thermo.db 의 verify 표와 %LOCALAPPDATA%\\thermo\\verify.log 에 남는다. 1초 표본은 thermo.db 에 그대로 있으므로
나중에 같은 구간을 재생해 다시 채점할 수 있다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .diagnose import Diagnoser, Diagnosis, load_reference

ROOT = Path(__file__).resolve().parents[2]
Result = tuple[bool, str]
Timeline = list[tuple[float, Diagnosis]]     # (시작부터 초, 판단)


def compared(tl: Timeline) -> list[Diagnosis]:
    """기준과 실제로 비교한 판단만 (보류·이어 간 것 제외)."""
    return [d for _, d in tl if "not_steady" not in d.keys and "carried" not in d.keys]


def _has(d: Diagnosis, text: str) -> bool:
    return text in " ".join(d.situation + d.judgement)


def _levels(ds: list[Diagnosis]) -> str:
    return ", ".join(sorted({d.headline for d in ds}))


def expect_idle(tl: Timeline) -> Result:
    c = compared(tl)
    if not c:
        return False, "기준과 비교한 판단이 한 번도 없었습니다(계속 보류)"
    bad = [d for d in c if d.level or {"cpu_hotter", "gpu_hotter"} & set(d.keys)]
    return (not bad), f"비교 {len(c)}번, 판정: {_levels(c)}"


def expect_cpu_full(tl: Timeline) -> Result:
    early = [d for t, d in tl if t <= 30]
    c = compared(tl)
    problems = []
    if early and not all("not_steady" in d.keys for d in early):
        problems.append("부하를 켠 직후 30초 안에 비교를 미루지 않았습니다")
    if not c:
        problems.append("기준과 비교한 판단이 없었습니다")
    else:
        if sum("cpu_design_limit" in d.keys for d in c) * 2 < len(c):
            problems.append("'냉각 설계 한계'가 비교의 절반 이상에서 나오지 않았습니다")
        if any(d.level for d in c):
            problems.append(f"정상이 아닌 판정: {_levels([d for d in c if d.level])}")
    if not any(_has(d, "python.exe") for _, d in tl):
        problems.append("부하를 낸 python.exe 를 짚지 못했습니다")
    return (not problems), "; ".join(problems) or f"비교 {len(c)}번, 판정: {_levels(c)}, 첫 30초 보류 확인"


def expect_defender(tl: Timeline) -> Result:
    hit = [d for _, d in tl if _has(d, "MsMpEng.exe")]
    bg = [d for d in hit if _has(d, "창 없")]
    bad = [d for d in compared(tl) if d.level]
    problems = []
    if not hit:
        problems.append("MsMpEng.exe(Windows Defender)를 짚지 못했습니다")
    elif not bg:
        problems.append("MsMpEng.exe 를 '창 없는 작업'으로 표시하지 않았습니다")
    if bad:
        problems.append(f"정상이 아닌 판정: {_levels(bad)}")
    return (not problems), "; ".join(problems) or f"Defender 지목 {len(hit)}번(창 없음 {len(bg)}번)"


def expect_battery(tl: Timeline) -> Result:
    ds = [d for _, d in tl]
    problems = []
    if sum("battery" in d.keys for d in ds) * 2 < len(ds):
        problems.append("배터리 동작을 절반 이상의 판단에서 말하지 않았습니다")
    if not any("cpu_power_limited" in d.keys or _has(d, "배터리라") for d in ds):
        problems.append("배터리 때문에 전력이 묶였다고 말하지 않았습니다")
    cool = [d for d in compared(tl) if {"cpu_hotter", "cpu_throttle_low_power"} & set(d.keys)]
    if cool:
        problems.append(f"배터리 상황을 냉각 문제로 잘못 말했습니다: {_levels(cool)}")
    return (not problems), "; ".join(problems) or "배터리·전력 한도로 설명함"


def expect_other_mode(tl: Timeline) -> Result:
    c = compared(tl)
    said = [d for d in c if {"fan_slow", "cpu_power_limited"} & set(d.keys) or _has(d, "다른 성능 모드")]
    cooling = [d for d in c if "cpu_hotter" in d.keys and "fan_slow" not in d.keys]
    problems = []
    if not c:
        problems.append("기준과 비교한 판단이 없었습니다")
    elif not said:
        problems.append(f"기준과 다른 모드(팬·전력 한도)라고 말하지 않았습니다. 판정: {_levels(c)}")
    if cooling:
        problems.append(f"모드 차이를 냉각 저하로 잘못 말했습니다: {_levels(cooling)}")
    return (not problems), "; ".join(problems) or f"비교 {len(c)}번 중 {len(said)}번 '다른 모드/팬·전력 한도'"


def expect_game(tl: Timeline) -> Result:
    c = compared(tl)
    problems = []
    if not c:
        problems.append("기준과 비교한 판단이 없었습니다")
    elif any(d.level for d in c):
        problems.append(f"정상이 아닌 판정: {_levels([d for d in c if d.level])}")
    if not any(_has(d, "Client-Win64-Shipping.exe") for _, d in tl):
        problems.append("게임 프로세스를 짚지 못했습니다")
    return (not problems), "; ".join(problems) or f"비교 {len(c)}번, 판정: {_levels(c)}"


@dataclass
class Scenario:
    key: str
    title: str
    prepare: str             # 시작 전에 사용자가 할 일 ("" 이면 없음)
    seconds: int
    stress: bool             # 시작과 함께 tools/stress.py 로 CPU 100% (사용자 프로그램처럼 python.exe 로 보이게)
    expect: Callable[[Timeline], Result]
    restore: str = ""        # 끝나고 되돌릴 일


SCENARIOS = [
    Scenario("idle", "공회전", "게임·무거운 프로그램을 끄고 어댑터를 연결한 채 Enter", 180, False, expect_idle),
    Scenario("cpu", "CPU 100% (+부하 직후 30초 보류)", "", 240, True, expect_cpu_full),
    Scenario("defender", "Windows Defender 빠른 검사",
             "'Windows 보안 → 바이러스 및 위협 방지 → 빠른 검사'를 누른 뒤 Enter", 240, False, expect_defender),
    Scenario("battery", "배터리 + CPU 100%", "어댑터를 뽑고 Enter", 240, True, expect_battery,
             "어댑터를 다시 연결하고 Enter"),
    Scenario("silent", "Silent 모드 + CPU 100%", "Armoury Crate 에서 성능 모드를 Silent 로 바꾸고 Enter", 240, True,
             expect_other_mode, "성능 모드를 원래(Performance)로 되돌리고 Enter"),
    Scenario("game", "게임(명조)", "명조를 켜고 평소 하는 장면에 들어간 뒤 Enter", 300, False, expect_game,
             "게임을 끄고 Enter"),
]


def run(keys: list[str] | None = None) -> int:
    from .checkup import ConsoleUI
    from .collect import Collector
    from .paths import home
    from .store import Store
    sys.stdout.reconfigure(encoding="utf-8")
    ui = ConsoleUI(home() / "verify.log")
    col = Collector()
    store = Store()
    store.db.execute("""CREATE TABLE IF NOT EXISTS verify (ts REAL, scenario TEXT, passed INTEGER, reason TEXT,
                        start REAL, end REAL, headlines TEXT)""")
    results = []
    try:
        ref, label = load_reference(store)
        ui.say(f"검증을 시작합니다. 기준: {label or '없음'}")
        for n in col.notes:
            ui.say(f"※ {n}")
        for sc in [s for s in SCENARIOS if not keys or s.key in keys]:
            if sc.prepare and not ui.ask(f"[{sc.title}] {sc.prepare} (건너뛰려면 q)"):
                ui.say(f"[{sc.title}] 건너뜀")
                continue
            ui.say(f"[{sc.title}] {sc.seconds}초 동안 잽니다")
            stress = None
            if sc.stress:
                stress = subprocess.Popen([sys.executable, str(ROOT / "tools" / "stress.py"), str(sc.seconds)],
                                          stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            dg = Diagnoser(ref)
            tl: Timeline = []
            start = None
            for i in range(sc.seconds):
                t0 = time.monotonic()
                s = col.sample()
                store.add(s)
                start = start or s.t
                dg.add(s.t, s.cpu.power, s.gpu.power, s.gpu.temp, s.fans.cpu_rpm)
                if i % 10 == 9:
                    d = dg.diagnose(col.stats(), s.t)
                    tl.append((s.t - start, d))
                    ui.say(f"  {s.t - start:4.0f}초 {d.headline}")
                time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
            if stress is not None:
                stress.wait(timeout=30)
            ok, why = sc.expect(tl)
            results.append((sc, ok, why))
            store.db.execute("INSERT INTO verify VALUES (?,?,?,?,?,?,?)",
                             (time.time(), sc.key, int(ok), why, start, s.t,
                              json.dumps([d.headline for _, d in tl], ensure_ascii=False)))
            store.db.commit()
            ui.say(f"[{sc.title}] {'통과' if ok else '실패'} — {why}")
            if sc.restore:
                ui.ask(sc.restore)
        ui.say("== 검증 결과 ==")
        for sc, ok, why in results:
            ui.say(f"  {'통과' if ok else '실패'}  {sc.title}: {why}")
        return 0 if results and all(ok for _, ok, _ in results) else 1
    finally:
        store.close()
        col.close()
        try:
            input("창을 닫으려면 Enter")
        except EOFError:
            pass
