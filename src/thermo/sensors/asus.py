"""ASUS ATK WMI (root\\wmi AsusAtkWmi_WMNB.DSTS): 팬 RPM, EC 의 CPU 온도. 관리자 권한 필요.

장치 ID 와 값 해석은 M1 에서 이 노트북으로 확인했다 (DESIGN 3장).
"""

from __future__ import annotations

from . import dotnet

CPU_FAN = 0x00110013
GPU_FAN = 0x00110014
CPU_TEMP = 0x00120094
PRESENT = 0x00010000


class AsusWmi:
    def __init__(self) -> None:
        dotnet.load()
        import System
        from System.Management import ManagementObjectSearcher
        self._uint = System.UInt32
        objs = list(ManagementObjectSearcher(r"root\wmi", "SELECT * FROM AsusAtkWmi_WMNB").Get())
        if not objs:
            raise RuntimeError("AsusAtkWmi_WMNB 없음")
        self._o = objs[0]

    def dsts(self, dev: int) -> int | None:
        """값의 하위 16비트. 장치가 없으면 None."""
        args = self._o.GetMethodParameters("DSTS")
        args["Device_ID"] = self._uint(dev)  # 파이썬 int 로 넘기면 '일반 오류'
        try:
            v = int(self._o.InvokeMethod("DSTS", args, None)["device_status"])
        except Exception:
            return None
        if v == 0xFFFFFFFE or not v & PRESENT:
            return None
        return v & 0xFFFF

    def fans(self) -> tuple[float | None, float | None]:
        c, g = self.dsts(CPU_FAN), self.dsts(GPU_FAN)
        return (None if c is None else c * 100.0, None if g is None else g * 100.0)

    def cpu_temp(self) -> float | None:
        v = self.dsts(CPU_TEMP)
        return None if not v else float(v)
