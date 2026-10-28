"""설치 파일 만들기: 아이콘 → PyInstaller(dist\\thermo) → NSIS(dist\\thermo-setup-<버전>.exe).

사용: .venv\\Scripts\\python tools\\build_app.py
필요: 빌드용 pyinstaller·pillow (pip install -e ".[build]"), NSIS 의 makensis.exe
      (PATH, THERMO_MAKENSIS 환경변수, 또는 electron-builder 가 받아 둔 %LOCALAPPDATA%\\electron-builder\\Cache\\nsis-*)
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from thermo import __version__
    return __version__


def version_info(v: str) -> Path:
    """PyInstaller 가 exe 에 넣는 버전 정보(파일 속성에 보임)."""
    nums = (v.split(".") + ["0"] * 4)[:4]
    t = ", ".join(nums)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({t}), prodvers=({t}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[StringFileInfo([StringTable('041204B0', [
    StringStruct('CompanyName', 'pyk-kentech'),
    StringStruct('FileDescription', 'thermo 온도 진단'),
    StringStruct('FileVersion', '{v}'),
    StringStruct('ProductName', 'thermo'),
    StringStruct('ProductVersion', '{v}'),
    StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [0x0412, 1200])])])
"""
    BUILD.mkdir(exist_ok=True)
    out = BUILD / "version_info.txt"
    out.write_text(text, encoding="utf-8")
    return out


def find_makensis() -> str | None:
    env = os.environ.get("THERMO_MAKENSIS")
    if env and Path(env).exists():
        return env
    if shutil.which("makensis"):
        return shutil.which("makensis")
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "electron-builder" / "Cache"
    hits = sorted(glob.glob(str(cache / "nsis-*" / "*" / "makensis.exe")))
    return hits[-1] if hits else None


def run(cmd: list[str]) -> None:
    print("> " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> int:
    v = version()
    print(f"thermo {v} 설치 파일을 만듭니다")
    run([sys.executable, str(PKG / "make_icon.py"), str(PKG / "thermo.ico")])
    version_info(v)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(DIST),
         "--workpath", str(BUILD / "pyi"), str(PKG / "thermo.spec")])
    app = DIST / "thermo"
    for exe in ("thermo.exe", "thermo-cli.exe"):
        if not (app / exe).exists():
            print(f"{exe} 이 만들어지지 않았습니다", file=sys.stderr)
            return 1
    nsis = find_makensis()
    if not nsis:
        print("makensis.exe 를 찾지 못했습니다. NSIS 를 설치하거나 THERMO_MAKENSIS 에 경로를 주세요. "
              f"실행 파일만 만들었습니다: {app}", file=sys.stderr)
        return 1
    out = DIST / f"thermo-setup-{v}.exe"
    run([nsis, "/INPUTCHARSET", "UTF8", f"/DVERSION={v}", f"/DSRC={app}", f"/DOUT={out}",
         f"/DICON={PKG / 'thermo.ico'}", f"/DLICENSE={ROOT / 'LICENSE'}", str(PKG / "installer.nsi")])
    size = out.stat().st_size / 2**20
    print(f"만들었습니다: {out} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
