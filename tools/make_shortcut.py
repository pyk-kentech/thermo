"""바탕화면에 thermo 바로가기를 만든다 (이미 있으면 덮어씀).

사용: .venv\\Scripts\\python tools\\make_shortcut.py [--remove]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "thermo 온도 진단.lnk"


def desktop() -> Path:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('Desktop')"],
                       capture_output=True, check=True)
    return Path(r.stdout.decode("utf-8").strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true", help="바로가기를 지운다")
    a = ap.parse_args()
    lnk = desktop() / NAME
    if a.remove:
        lnk.unlink(missing_ok=True)
        print(f"지웠습니다: {lnk}")
        return 0
    target = ROOT / ".venv" / "Scripts" / "thermo.exe"
    if not target.exists():
        print(f"{target} 이 없습니다. 먼저 pip install -e . 를 하세요.", file=sys.stderr)
        return 1
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:THERMO_LNK);"
        "$s.TargetPath = $env:THERMO_TARGET; $s.WorkingDirectory = $env:THERMO_ROOT;"
        "$s.Description = 'FX707VU 온도 진단'; $s.Save()"
    )
    env = {"THERMO_LNK": str(lnk), "THERMO_TARGET": str(target), "THERMO_ROOT": str(ROOT)}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env={**os.environ, **env}, check=True)
    print(f"만들었습니다: {lnk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
