"""LibreHardwareMonitorLib 와 의존 DLL 을 NuGet 에서 받아 vendor/lhm 에 푼다.

.NET Framework 4.7.2 용 DLL 을 쓴다(pythonnet 기본 런타임이 .NET Framework 이고
System.Management 가 프레임워크에 들어 있어서). 받은 파일은 수정하지 않는다.
"""

from __future__ import annotations

import io
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "vendor" / "lhm"
CACHE = ROOT / "vendor" / "nupkg"
TOP = ("LibreHardwareMonitorLib", "0.9.6")
# 프레임워크 선호 순서
TFMS = ["net472", "net471", "net47", "net462", "net461", "net46", "net45", "netstandard2.0"]
GROUP_NAMES = {
    "net472": ".NETFramework4.7.2", "net471": ".NETFramework4.7.1", "net47": ".NETFramework4.7",
    "net462": ".NETFramework4.6.2", "net461": ".NETFramework4.6.1", "net46": ".NETFramework4.6",
    "net45": ".NETFramework4.5", "netstandard2.0": ".NETStandard2.0",
}
# .NET Framework 에 이미 들어 있는 것
SKIP = {"system.management", "microsoft.win32.registry", "netstandard.library"}


def fetch(pkg: str, ver: str) -> zipfile.ZipFile:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{pkg.lower()}.{ver}.nupkg"
    if not path.exists():
        url = f"https://www.nuget.org/api/v2/package/{pkg}/{ver}"
        print(f"받는 중: {pkg} {ver}")
        path.write_bytes(urllib.request.urlopen(url).read())
    return zipfile.ZipFile(io.BytesIO(path.read_bytes()))


def deps(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    name = next(n for n in z.namelist() if n.endswith(".nuspec"))
    root = ET.fromstring(z.read(name))
    ns = {"n": root.tag.split("}")[0].strip("{")}
    groups = {g.get("targetFramework", ""): g for g in root.iterfind(".//n:dependencies/n:group", ns)}
    for tfm in TFMS:
        g = groups.get(GROUP_NAMES[tfm])
        if g is not None:
            return [(d.get("id"), re.sub(r"[\[\]\(\) ]", "", d.get("version")).split(",")[0])
                    for d in g.iterfind("n:dependency", ns)]
    return []


def dlls(z: zipfile.ZipFile) -> list[str]:
    names = z.namelist()
    for tfm in TFMS:
        for prefix in (f"runtimes/win-x64/lib/{tfm}/", f"runtimes/win/lib/{tfm}/", f"lib/{tfm}/"):
            hit = [n for n in names if n.startswith(prefix) and n.endswith(".dll")]
            if hit:
                return hit
    return []


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    todo = [TOP]
    while todo:
        pkg, ver = todo.pop()
        if pkg.lower() in seen or pkg.lower() in SKIP:
            continue
        seen.add(pkg.lower())
        z = fetch(pkg, ver)
        for n in dlls(z):
            (OUT / Path(n).name).write_bytes(z.read(n))
            print(f"  {Path(n).name}  ({pkg} {ver})")
        todo.extend(deps(z))
    return 0


if __name__ == "__main__":
    sys.exit(main())
