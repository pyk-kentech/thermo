# 제3자 구성 요소

thermo 는 아래 구성 요소를 수정하지 않고 함께 둔다(`vendor/lhm/`, `tools/fetch_lhm.py` 가 NuGet 에서 받음). 원본 소스는 각 프로젝트 주소에서 받을 수 있다.

| 구성 요소 | 버전 | 라이선스 | 출처 |
|---|---|---|---|
| LibreHardwareMonitorLib | 0.9.6 | MPL-2.0 | https://github.com/LibreHardwareMonitor/LibreHardwareMonitor |
| DiskInfoToolkit | 1.1.2 | MPL-2.0 | https://github.com/Blacktempel/DiskInfoToolkit |
| RAMSPDToolkit-NDD | 1.4.2 | MPL-2.0 | https://github.com/Blacktempel/RAMSPDToolkit |
| BlackSharp.Core | 1.0.4 | MPL-2.0 | https://github.com/Blacktempel/BlackSharp |
| HidSharp | 2.6.4 | Apache-2.0 | https://software.seekye.com/hidsharp |
| System.Memory | 4.6.3 | MIT | https://github.com/dotnet/maintenance-packages |
| System.Buffers | 4.6.1 | MIT | https://github.com/dotnet/maintenance-packages |
| System.Numerics.Vectors | 4.6.1 | MIT | https://github.com/dotnet/maintenance-packages |
| System.Runtime.CompilerServices.Unsafe | 6.1.2 | MIT | https://github.com/dotnet/maintenance-packages |
| System.Threading.AccessControl | 10.0.3 | MIT | https://github.com/dotnet/runtime |
| System.Security.AccessControl | 6.0.0 | MIT | https://github.com/dotnet/runtime |
| System.Security.Principal.Windows | 5.0.0 | MIT | https://github.com/dotnet/runtime |

파이썬 의존성(pip 로 설치, 함께 두지 않음): pythonnet(MIT), nvidia-ml-py(BSD-3-Clause), psutil(BSD-3-Clause).

참고만 한 것(코드를 가져오지 않음):
- G-Helper (GPL-3.0, https://github.com/seerge/g-helper) — ASUS ATK WMI 장치 ID(팬 `0x00110013`/`0x00110014`, CPU 온도 `0x00120094`)라는 사실만 참고.
- Linux `asus-wmi` 드라이버 (GPL-2.0) — DSTS present 비트 등 사실만 참고.

PawnIO 드라이버는 함께 두지 않는다. 사용자가 https://github.com/namazso/PawnIO.Setup 에서 직접 설치한다.
