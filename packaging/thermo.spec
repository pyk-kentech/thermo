# PyInstaller 설정: 폴더 하나(dist\thermo)에 thermo.exe(창, 관리자) 와 thermo-cli.exe(콘솔, 관리자)
# 사용: tools\build_app.py 가 부른다.
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "thermo_app.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(ROOT / "vendor" / "lhm"), "vendor/lhm"),
           (str(ROOT / "LICENSE"), "."),
           (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")],
    hiddenimports=["thermo.gui", "thermo.checkup", "thermo.verify", "thermo.calibrate", "pynvml", "clr"],
    excludes=["PIL", "pytest", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

common = dict(icon=str(ROOT / "packaging" / "thermo.ico"), uac_admin=True, upx=False,
              version=str(ROOT / "build" / "version_info.txt"))

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="thermo", console=False, **common)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="thermo-cli", console=True, **common)

coll = COLLECT(gui, cli, a.binaries, a.datas, name="thermo", upx=False)
