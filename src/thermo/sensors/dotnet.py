"""pythonnet 으로 .NET Framework 를 한 번만 띄운다 (DESIGN 3장)."""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path

# 얼린 앱(PyInstaller)에서는 vendor\lhm 이 풀린 폴더(_MEIPASS) 아래에 있다
LHM_DIR = (Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parents[3]) / "vendor" / "lhm"


@cache
def load() -> None:
    import clr
    clr.AddReference("System.Management")
    sys.path.insert(0, str(LHM_DIR))
    clr.AddReference(str(LHM_DIR / "LibreHardwareMonitorLib.dll"))
