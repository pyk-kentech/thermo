# thermo 설계

ASUS TUF Gaming F17(FX707VU) 한 대 전용 온도 진단 도구.
HWMonitor 처럼 숫자만 보여 주지 않고, **지금 온도가 정상인지(정상/주의/이상)** 와 **원인이 무엇인지** 를 한국어 한두 문장으로 알려 준다.

## 1. 범위

- 이 노트북 한 대만 지원한다. 다른 하드웨어 감지·분기는 만들지 않는다.
- 상주하지 않는다. 켜면 작은 창이 뜨고, 창을 닫으면 프로세스가 끝난다. 시작 프로그램·서비스·트레이 등록 없음.
- **읽기 전용**이다. 팬 곡선, 성능 모드, 전원 계획 같은 설정은 절대 바꾸지 않는다.
- 처음 판정은 규칙 기반이다. 학습 모델은 쓰지 않는다.

## 2. 이 노트북에서 확인한 사실 (2026-10-23 측정)

| 항목 | 값 | 확인 방법 |
|---|---|---|
| 모델 | ASUS TUF Gaming F17 FX707VU, BIOS FX707VU.340 | `Win32_ComputerSystem`, `Win32_BIOS` |
| CPU | Intel Core i7-13620H (6P+4E, 16스레드), TjMax 100°C | `Win32_Processor`, 인텔 사양 |
| dGPU | NVIDIA GeForce RTX 4050 Laptop, 드라이버 610.88 | `nvidia-smi` |
| GPU 온도 목표(Target) | 87°C. NVML 문턱값: slowdown 91°C, GPU max 105°C | `nvidia-smi -q -d TEMPERATURE`, NVML |
| GPU 한계까지 남은 온도 | `T.Limit` 값(측정 시 31°C 여유)으로 직접 나옴. NVML `nvmlDeviceGetMarginTemperature` 도 같은 값(정수) | 같은 명령, NVML |
| GPU 전력 한도 | 기본 80W, 실제 적용 102.6–112.6W(Dynamic Boost 로 움직임) | `nvidia-smi -q -d POWER`, NVML |
| 팬 | 2개(CPU·GPU). ASUS 도 `fanCounts = 2` 로 기록 | ASUS WMI, 레지스트리 |
| GPU 스로틀 이유 | 비트마스크로 나옴(Idle, SW Power Cap, HW Thermal 등) | `clocks_event_reasons.active` |
| GPU 팬 | `nvidia-smi` 로는 N/A (팬은 노트북 EC 가 관리) | 같은 명령 |
| iGPU | Intel UHD Graphics | `Win32_VideoController` |
| ASUS 소프트웨어 | Armoury Crate(서비스 `ArmouryCrateControlInterface`), MyASUS 실행 중 | `Get-Service` |
| 전원 | 어댑터 연결, 충전 안 함(80%, 충전 한도로 보임), 전원 계획 '균형 조정' | `root\wmi BatteryStatus`, `powercfg` |
| 실행 환경 | Python 3.11.9, .NET 런타임 6/8/10 (SDK 없음), 관리자 아님. pythonnet 은 .NET Framework 4.x(4.0.30319) 를 띄운다 | |

## 3. 센서 읽기 방법

M1(2026-10-23)에서 `tools/probe.py` 로 실제로 읽어 보고 정했다. 열 '확인' 은 그때 나온 값이다.

| 값 | 출처 | 권한 | 확인 |
|---|---|---|---|
| CPU 패키지·코어 온도, 'TjMax 까지 거리', 코어 클럭, 패키지 전력 | **LibreHardwareMonitorLib 0.9.6** (pythonnet). 커널 접근은 **PawnIO** 드라이버로 한다(사용자가 직접 설치, 아래) | 관리자 + PawnIO | PawnIO 설치 뒤: 패키지 76°C, 코어 58–79°C, TjMax 까지 거리 21–42°C(=TjMax 100°C), 코어 클럭 1.1–4.5GHz, 패키지 전력 28W. PawnIO 없이는 온도·클럭 `None`, 전력 0 |
| CPU 온도 (대체) | ASUS ATK WMI `DSTS 0x00120094`, 결과 `& 0xFFFF` = °C (EC 가 읽는 값 하나, 1°C 단위) | 관리자 | 79°C, 76°C(같은 때 LHM 패키지 76°C 와 일치) |
| CPU 사용률 | LHM `CPU Total` 또는 `psutil` | 일반 | 됨 |
| CPU 실효 클럭 (대체) | 성능 카운터 `% Processor Performance` × 기준 클럭 2.4GHz | 일반 | 113–139% |
| CPU 스로틀 여부(원인 구분 없이) | 성능 카운터 `\Processor Information(_Total)\% Performance Limit` (100 미만이면 제한 중) | 일반 | 100 |
| GPU 온도, 부하, 전력, 전력 한도, 클럭, 스로틀 이유, 한계까지 남은 온도 | NVML (`nvidia-ml-py`) | 일반 | 모두 됨 |
| GPU 핫스팟·메모리 접합 온도 | LHM (NVAPI) | 일반 | 62°C / 62°C (근거 칸 보조값) |
| CPU 팬·GPU 팬 RPM | ASUS ATK WMI `DSTS` — CPU 팬 `0x00110013`, GPU 팬 `0x00110014`, 결과 `& 0xFFFF` × 100 = RPM | 관리자 | 3300 / 3600–3800 rpm |
| 프로세스별 CPU 사용량 | `psutil` | 일반 | 됨 |
| 프로세스별 GPU 사용량 | NVML `nvmlDeviceGetProcessUtilization` (pid, SM%) | 일반 | 됨 (게임 pid 27%) |
| 어댑터 연결·충전 여부 | `root\wmi BatteryStatus` (PowerOnline, Charging) | 일반 | 됨 |
| 활성 전원 계획 | `powercfg /getactivescheme` | 일반 | 됨 |
| Armoury Crate 성능 모드 | **아직 모름** — 아래 | | |

WMI 와 성능 카운터는 pythonnet 으로 .NET Framework 의 `System.Management`, `System.Diagnostics.PerformanceCounter` 를 불러 읽는다(LHM 을 위해 어차피 .NET 을 띄우므로 다리는 하나). WMI 메서드 인자는 `System.UInt32` 로 넘겨야 한다(파이썬 int 를 넘기면 '일반 오류'). 외부 명령은 `powercfg` 하나뿐이다.

**PawnIO (2026-10-23 사용자 결정: 설치한다, 같은 날 설치 확인 — 서비스 `PawnIO` RUNNING)**
- LHM 0.9.6 은 옛 WinRing0 대신 PawnIO(namazso, 서명된 커널 드라이버) 로 MSR 을 읽는다. 설치는 사용자가 공식 설치 파일(`github.com/namazso/PawnIO.Setup` 릴리스)로 직접 한다. thermo 는 드라이버를 설치하거나 건드리지 않는다.
- 시작할 때 PawnIO 가 있으면 LHM 의 CPU 값을 쓰고, 없거나 값이 `None` 이면 ASUS WMI CPU 온도 + 성능 카운터로 돌아간다. 이때 창에 "CPU 코어별 온도·전력은 PawnIO 가 없어 읽지 못합니다" 를 적는다.

**확인한 것 / 남은 것**
- 팬 ID 는 이 모델에서 맞다(값이 나오고 present 비트 `0x00010000` 이 켜져 있음). 가운데 팬 `0x00110031` 은 없음(`0xFFFFFFFE`).
- ASUS WMI GPU 온도 `0x00120097` 은 0 만 준다 → 쓰지 않는다(NVML 로 충분).
- NVML 팬 속도는 'Not Supported' → 팬은 ASUS WMI 만.
- 성능 모드 `0x00120075` 는 PowerShell(CIM)로는 `0x00000000`, pythonnet(System.Management)으로는 '일반 오류' 를 낸다. 이 모델에서 DSTS 로는 읽히지 않는 것으로 보인다. Armoury Crate 에서 모드를 바꿔 가며 다시 읽어야 정해진다 — 사용자 결정으로 **나중(M5)** 에 한다. 그 전까지 성능 모드는 '확인 불가' 로 두고, 원인 규칙 4 는 팬 RPM 만으로 판단한다("온도에 비해 팬이 느립니다. Armoury Crate 가 Silent 모드인지 확인하세요.").
- 열 영역 카운터 `\Thermal Zone Information(\_TZ.TZ00)` 는 28°C(301K) — CPU 와 무관해 쓰지 않는다.

**쓰지 않는 것:** HWMonitor(공개 API 없음), HWiNFO 공유 메모리(무료판 12시간 제한), `nvidia-smi` 프로세스 반복 실행(느림 — NVML 로 대신), 성능 카운터 `GPU Engine`(인스턴스 700개 이상이라 느림 — NVML 프로세스 사용률로 대신).

## 4. 언어와 형태

- **Python 3.11 + tkinter** (표준 라이브러리 GUI). 이미 설치된 파이썬을 쓴다.
- 실행하면 관리자 권한이 없을 때 UAC 로 스스로 다시 띄운다(`ShellExecuteW ... "runas"`). 거절하면 관리자 없이 읽을 수 있는 값만으로 판정하고 그 사실을 창에 적는다.
- 판정·원인 규칙은 GUI 와 분리된 순수 함수로 두고, 같은 결과를 터미널로 내는 `thermo --once` 도 둔다(테스트·디버그용).

### 런타임 의존성과 이유

| 의존성 | 라이선스 | 이유 |
|---|---|---|
| pythonnet | MIT | LHM(.NET)과 WMI·성능 카운터를 파이썬에서 부르기 위해 |
| LibreHardwareMonitorLib 0.9.6 (net472 빌드) | MPL-2.0 | CPU 온도·전력은 커널 접근이 필요하고, 이를 무료·오픈소스로 하는 현실적 선택지 |
| LHM 의존 DLL: DiskInfoToolkit 1.1.2, RAMSPDToolkit-NDD 1.4.2, BlackSharp.Core 1.0.4 | MPL-2.0 | LHM 이 불러옴 |
| LHM 의존 DLL: HidSharp 2.6.4 | Apache-2.0 | LHM 이 불러옴 |
| LHM 의존 DLL: System.Memory, System.Buffers, System.Numerics.Vectors, System.Runtime.CompilerServices.Unsafe, System.Threading.AccessControl, System.Security.AccessControl, System.Security.Principal.Windows | MIT | LHM 이 불러옴 (.NET Framework 용 보충 라이브러리) |
| nvidia-ml-py | BSD-3-Clause | NVML 로 GPU 값을 빠르게 읽기 위해 |
| psutil | BSD-3-Clause | 프로세스별 CPU 사용량 |

LHM DLL 은 `tools/fetch_lhm.py` 가 NuGet 에서 받은 그대로(수정 없이) `vendor/lhm/` 에 풀고, 출처·라이선스를 `THIRD_PARTY_NOTICES.md` 에 적는다. G-Helper(GPL-3.0)에서는 **코드를 가져오지 않고** WMI 장치 ID 같은 사실만 참고한다.

## 5. 측정 방식

- 1초마다 한 번 읽어 최근 10초를 창(window)으로 둔다. 판정은 순간값이 아니라 10초 창의 값(온도는 중앙값과 최댓값, 부하는 평균)으로 한다 — 순간 튐을 '이상'으로 보지 않기 위해.
- 창은 2초마다 갱신한다.
- 한 번의 읽기 결과는 `Snapshot` dataclass(CPU, GPU, 팬, 전원, 상위 프로세스)로 묶는다. 읽지 못한 값은 `None`.

## 6. 정상 판단 — 세 기준

세 기준을 각각 평가하고, 가장 나쁜 것을 최종 판정으로 한다. CPU 와 GPU 는 따로 평가한 뒤 합친다.

### 6.1 절대 한계

| | 주의 | 이상 |
|---|---|---|
| CPU 패키지 | 10초 중앙값 ≥ 90°C | 중앙값 ≥ 97°C, 또는 TjMax 까지 거리 ≤ 3°C 에서 스로틀 중 |
| GPU | 한계까지 남은 온도(T.Limit) ≤ 10°C | ≤ 3°C, 또는 HW/SW Thermal Slowdown 이유가 켜짐 |

GPU 는 87°C 같은 숫자를 박아 두지 않고 드라이버가 알려 주는 '한계까지 남은 온도'를 쓴다.

### 6.2 부하 대비 온도

부하가 낮은데 뜨거우면 이상 신호다.

| | 조건 | 판정 |
|---|---|---|
| CPU | 평균 사용률 < 15% 이고 패키지 온도 ≥ 75°C | 주의 |
| CPU | 평균 사용률 < 15% 이고 패키지 온도 ≥ 85°C | 이상 |
| GPU | 사용률 < 10% 이고 온도 ≥ 70°C | 주의 |

(GPU 가 쉬어도 바로 옆 CPU 열로 데워질 수 있으므로, CPU 가 고부하이면 GPU 쪽 이 규칙은 쓰지 않는다.)

### 6.3 이 PC 의 평소값

- 프로그램이 켜져 있는 동안의 10초 창 결과를 SQLite(`%LOCALAPPDATA%\thermo\baseline.db`, 표준 라이브러리)에 쌓는다.
- 묶음(bucket) 키: (장치, 부하 구간 0–10/10–30/30–60/60–100%, 전원 연결 여부, 성능 모드).
- 묶음마다 온도의 중앙값과 95번째 백분위를 둔다. 표본이 30개 미만인 묶음은 이 기준을 쓰지 않는다("아직 평소값을 배우는 중"으로 표시).
- 판정: 95백분위보다 8°C 넘게 높으면 주의, 15°C 넘게 높으면 이상.
- 이상으로 판정된 순간의 값은 평소값에 넣지 않는다(나쁜 상태를 평소로 배우지 않게).
- (M3) 기록 단위는 판정 한 번(10초 창) = 표본 하나. `--once` 는 실행마다 하나, 창 화면(M4)은 10초마다 하나를 넣는다. 묶음마다 최근 2000개만 둔다.
- (M3) 성능 모드를 아직 못 읽으므로(3장) 묶음 키의 성능 모드는 지금은 늘 '?' 다. 모드를 읽게 되면 묶음이 새로 시작된다.
- (M3) 팬 최고 RPM 도 같은 DB 에 쌓는다(팬마다 최근 20000개). 표본 30개 이상, 99번째 백분위가 4500rpm 이상일 때만 '최고 RPM' 으로 쓴다 — 팬을 세게 돌린 적이 없으면 지금 속도가 곧 최고가 되어 '먼지' 를 잘못 말하기 때문.
- (M3) 7장 규칙 1 의 낮추기: 그 장치의 판정이 절대 한계 '주의' 뿐이고, 원인이 고부하이고, 온도가 평소 이 부하의 95%값 이하이면 '정상' 으로 낮추고 "평소 범위 안입니다" 를 덧붙인다. '이상' 은 낮추지 않는다.

## 7. 원인 가르기

판정이 주의 이상일 때만 원인을 찾는다. 위에서부터 맞는 규칙을 고르고, 최대 두 개까지 문장으로 보인다.

| 순서 | 조건 | 원인 문장(예) |
|---|---|---|
| 1 | 고온 + 고부하(CPU ≥ 60% 또는 GPU ≥ 60%) | "`chrome.exe` 가 CPU 를 45% 쓰고 있어 온도가 올라갔습니다. 부하에 맞는 정상 발열입니다." (평소값 안이면 판정을 '정상'으로 낮춘다) |
| 2 | 고온 + 저부하 + 상위 프로세스 중 백그라운드(창 없는) 프로세스가 눈에 띔 | "보이지 않는 작업 `MsMpEng.exe`(Windows Defender 검사)가 CPU 를 쓰고 있습니다." |
| 3 | 고온 + 팬 최고속 근처(이 PC 에서 관측한 최고 RPM 의 90% 이상) | "팬이 최고 속도인데도 온도가 높습니다. 흡·배기구 먼지나 서멀 열화를 의심하세요." |
| 4 | 고온 + 팬 저속 | 성능 모드가 Silent 면: "Armoury Crate 가 Silent 모드라 팬이 천천히 돕니다." / 아니면: "온도에 비해 팬이 느립니다. 팬 고장이나 팬 설정을 확인하세요." |
| 5 | 클럭 하락 + 온도 한계 근처 | "온도 한계에 닿아 클럭을 낮추고 있습니다(열 스로틀)." |
| 6 | 클럭 하락 + 온도 여유 있음 + (CPU 전력이 평소 최대치에 붙음 / GPU 'SW Power Cap') | "온도가 아니라 전력 한도 때문에 클럭이 제한되고 있습니다." |
| 7 | 배터리 사용 중 | 판정에 붙는 보조 문장: "배터리로 동작 중이라 성능이 제한됩니다." |
| 8 | 전원 계획·성능 모드 | 보조 문장: "성능 모드: Turbo — 평소보다 높은 온도가 정상입니다." |
| 9 | 아무것도 맞지 않음 | "뚜렷한 원인을 찾지 못했습니다. 주변 온도나 바닥 통풍을 확인하세요." |

'고온' 은 6장에서 주의 이상을 받은 장치를 말한다. 원인마다 근거(어떤 값이 어떤 조건을 넘었는지)를 같이 남겨 화면의 '근거' 칸에 보인다.

**M2 에서 정한 세부 (모두 `rules.py` 상수, 첫 추정)**
- '저부하' 는 그 장치 사용률 < 30%, '눈에 띄는' 프로세스는 전체 CPU(또는 GPU SM)의 5% 이상.
- '백그라운드' 는 제목 있는 보이는 창이 하나도 없는 프로세스다(`EnumWindows`). 잘 알려진 것은 이름을 붙인다(MsMpEng.exe → Windows Defender 검사 등).
- 규칙 1 이 맞으면(부하로 설명되면) 그 장치는 다른 원인을 보지 않는다. 같은 원인이 CPU·GPU 둘 다에 걸리면 한 번만 말한다.
- 클럭 하락: CPU 는 `% Performance Limit` < 95. 그때 TjMax 까지 ≤ 5°C 면 열 스로틀, ≥ 10°C(또는 모름)면 전력 한도. GPU 는 이유 비트로 가른다(Thermal → 열, SW Power Cap + 부하 ≥ 60% + 여유 > 10°C → 전력).
- 팬 최고 RPM 은 M3 에서 평소값과 함께 쌓는다. 그 전에는 '최고속' 규칙은 쓰지 않고, '저속' 은 2500rpm 미만으로 본다(M1 에서 가벼운 부하일 때 3300–3800rpm).
- 원인 규칙 1 의 '평소값 안이면 정상으로 낮춘다' 는 평소값이 생기는 M3 에서 붙인다.
- 창 요약: GPU 이유 비트는 창의 절반 이상에서 켜진 것만 쓴다. 프로세스 사용률은 pid 별 창 평균.

## 8. 화면

작은 창 하나(대략 440×520).

```
┌──────────────────────────────────┐
│  ● 주의                            │  ← 정상(초록)/주의(주황)/이상(빨강)
│  chrome.exe 가 CPU 를 45% 쓰고 있어 │  ← 원인 한두 문장
│  CPU 온도가 평소보다 9°C 높습니다.    │
├──────────────────────────────────┤
│        온도   부하   전력   클럭    │
│  CPU   88°C   47%   62W  3.9GHz  │
│  GPU   61°C    3%   12W   0.6GHz │
│  팬    CPU 4800rpm · GPU 4600rpm  │
│  전원  어댑터 · Performance 모드   │
├──────────────────────────────────┤
│  근거 ▾ (접고 펼치기)               │
│  · CPU 중앙값 88°C ≥ 평소 95%값 79°C │
│  · 상위 프로세스: chrome.exe 45%    │
└──────────────────────────────────┘
```

- 관리자 권한 없이 떴거나 센서를 못 읽으면 맨 아래에 한국어로 알린다("팬 속도를 읽지 못했습니다 — 관리자 권한이 필요합니다").
- 첫 10초는 "측정 중…" 을 보인다.
- (M4) 센서 읽기·판정·평소값 기록은 작업 스레드(`gui.Monitor`)가 하고 큐로 화면에 넘긴다. 1초마다 읽고, 2초마다 판정, 10초마다 평소값에 하나 넣는다. 창을 닫으면 스레드를 멈추고 센서·DB 를 닫은 뒤 끝난다.
- (M4) `python -m thermo`(또는 venv 의 `thermo.exe`)는 관리자 권한이 없으면 `ShellExecuteW(runas)` 로 창 없는 `pythonw.exe -m thermo --elevated` 를 띄우고 자신은 끝난다. UAC 를 거절하면 그대로 관리자 없이 창을 띄운다. `--no-elevate` 로 요청을 끌 수 있다.
- (M4) 창을 두 개 띄우면 둘 다 같은 `baseline.db` 에 기록한다(SQLite 라 깨지지는 않지만 표본이 두 배로 쌓인다). 막지는 않는다.

## 9. 코드 구조

```
D:\thermo\
  DESIGN.md  README.md  THIRD_PARTY_NOTICES.md  pyproject.toml
  vendor\lhm\            LibreHardwareMonitorLib.dll 과 의존 DLL
  src\thermo\
    model.py             Snapshot, Window, Verdict, Cause dataclass
    sensors\lhm.py       CPU 온도·클럭·전력
    sensors\nvidia.py    NVML
    sensors\asus.py      ATK WMI: 팬, 성능 모드
    sensors\system.py    프로세스, 성능 카운터, 전원
    collect.py           1초 읽기 + 10초 창
    rules.py             판정·원인 (순수 함수, 센서 의존 없음)
    baseline.py          SQLite 평소값
    text.py              한국어 문장 만들기
    gui.py               tkinter 창
    __main__.py          관리자 권한 재실행, --once
  tests\                 가짜 Snapshot 으로 rules·baseline·text 테스트 (pytest)
```

실행: `python -m thermo` (창), `python -m thermo --once` (터미널 한 번). 바탕화면 바로가기는 M5 에서 만든다.

## 10. 마일스톤

1. **M1 센서 확인** — 각 센서를 실제로 읽어 보는 `tools/probe.py`. LHM 드라이버 방식, ASUS 팬 ID·성능 모드 값, 성능 카운터를 확인하고 결과를 이 문서 2·3장에 적는다. (완료. PawnIO 설치 후 LHM CPU 값 확인. 성능 모드 값은 M5 로 미룸)
2. **M2 판정·원인 규칙** — `model`, `rules`, `text` 와 단위 테스트. `--once` 로 터미널 출력. (완료. 센서 모듈·`collect` 도 이때 만듦. 관리자 `--once` 실측: 정상, CPU 74°C/28%/27W, GPU 53°C, 팬 3690/3990rpm)
3. **M3 평소값** — `baseline` 과 테스트, 판정에 연결. (완료. 실측: `baseline.db` 생성, 'CPU 배우는 중 (0/30)' 표시)
4. **M4 창** — tkinter 화면, 관리자 재실행. (완료. UAC 재실행 → 관리자 창, 관리자 없는 창 캡처로 배치 확인, 닫으면 프로세스 종료)
5. **M5 마무리** — README, THIRD_PARTY_NOTICES, 바로가기. 실제로 부하를 줘서(예: 게임 실행) 판정이 맞게 바뀌는지 사용자와 확인. Armoury Crate 모드를 바꿔 가며 `0x00120075` 값의 뜻을 정한다.

## 11. 위험과 열린 질문

- PawnIO 는 사용자가 설치한 커널 드라이버다. 지울 때도 사용자가 PawnIO 제거 프로그램으로 한다. thermo 는 없어도 돈다.
- Armoury Crate 가 켜진 채로 ASUS WMI DSTS 를 1초마다 읽어도 문제가 없었다(M1, 3회씩 연속 읽기). 오래 돌릴 때도 괜찮은지 M5 에서 본다.
- 6·7장의 문턱값은 첫 추정이다. M5 에서 실제 사용으로 다듬는다.
