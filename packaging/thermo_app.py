"""PyInstaller 시작점. thermo.exe(창)·thermo-cli.exe(콘솔) 둘 다 이 파일로 만든다."""

import sys

from thermo.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
