"""CPU 온도·클럭·전력 (LibreHardwareMonitorLib + PawnIO)."""

from __future__ import annotations

from ..model import CpuReading
from . import dotnet


class LhmCpu:
    def __init__(self) -> None:
        dotnet.load()
        from LibreHardwareMonitor.Hardware import Computer, HardwareType
        self._pc = Computer()
        self._pc.IsCpuEnabled = True
        self._pc.Open()
        self._cpus = [h for h in self._pc.Hardware if h.HardwareType == HardwareType.Cpu]

    def read(self) -> CpuReading:
        r = CpuReading(source="lhm")
        temps, dists, clocks = [], [], []
        for hw in self._cpus:
            hw.Update()
            for s in hw.Sensors:
                v = None if s.Value is None else float(s.Value)
                if v is None:
                    continue
                kind, name = str(s.SensorType), s.Name
                if kind == "Temperature":
                    if name == "CPU Package":
                        r.temp = v
                    elif name == "Core Average":
                        r.temp_avg = v
                    elif name.endswith("Distance to TjMax"):
                        dists.append(v)
                    elif "Core #" in name:
                        temps.append(v)
                elif kind == "Load" and name == "CPU Total":
                    r.load = v
                elif kind == "Power" and name == "CPU Package":
                    r.power = v
                elif kind == "Clock" and "Core #" in name:
                    clocks.append(v)
        r.temp_max = max(temps, default=None)
        r.tj_distance = min(dists, default=None)
        r.clock = max(clocks, default=None)
        if r.temp is None:
            r.source = ""
        if r.power == 0:  # PawnIO 없이 읽으면 0 이 나온다
            r.power = None
        return r

    def close(self) -> None:
        self._pc.Close()
