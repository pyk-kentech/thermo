"""pythonnet 으로 .NET Framework 를 한 번만 띄운다 (DESIGN 3장)."""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path

LHM_DIR = Path(__file__).resolve().parents[3] / "vendor" / "lhm"


@cache
def load() -> None:
    import clr
    clr.AddReference("System.Management")
    sys.path.insert(0, str(LHM_DIR))
    clr.AddReference(str(LHM_DIR / "LibreHardwareMonitorLib.dll"))
