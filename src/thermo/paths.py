"""사용자 데이터 위치: %LOCALAPPDATA%\thermo (THERMO_HOME 으로 바꿀 수 있음, 테스트용)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("THERMO_HOME")
    base = Path(env) if env else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thermo"
    base.mkdir(parents=True, exist_ok=True)
    return base
