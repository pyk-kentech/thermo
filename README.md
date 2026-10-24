# thermo

ASUS TUF Gaming F17 (FX707VU) 전용 온도 진단 도구.
켜 두면 지금 온도가 **정상 / 주의 / 이상** 인지와 **원인**을 한국어 한두 문장으로 알려 준다.
상주하지 않는다. 창을 닫으면 끝난다. 팬·성능 모드·전원 설정은 읽기만 하고 절대 바꾸지 않는다.

설계와 판정 기준은 [DESIGN.md](DESIGN.md) 에 있다.

## 준비 (한 번만)

1. Python 3.11 이상.
2. 설치

   ```
   cd D:\thermo
   python -m venv .venv
   .venv\Scripts\python -m pip install -e ".[dev]"
   .venv\Scripts\python tools\fetch_lhm.py     # vendor\lhm 이 비어 있을 때만
   ```

3. (권장) **PawnIO** 드라이버 — CPU 코어별 온도·TjMax 까지 거리·패키지 전력을 읽는 데 필요하다.
   공식 설치 파일: https://github.com/namazso/PawnIO.Setup/releases (사용자가 직접 설치).
   없어도 thermo 는 돈다. 그때는 ASUS 가 알려 주는 CPU 온도 하나로 판정한다.

4. (선택) 바탕화면 바로가기: `.venv\Scripts\python tools\make_shortcut.py`

## 쓰기

| 명령 | 하는 일 |
|---|---|
| 바로가기 `thermo` 또는 `.venv\Scripts\thermo.exe` | 창을 띄운다. 관리자 권한을 UAC 로 요청한다(거절해도 뜬다). |
| `.venv\Scripts\python -m thermo --once` | 10초 재고 결과를 터미널에 쓴다. |
| `... -m thermo --no-elevate` | 관리자 권한을 요청하지 않고 창을 띄운다(팬·CPU 코어 온도 없음). |

관리자 권한이 있어야 팬 속도와 CPU 온도를 읽는다(ASUS WMI, LHM).

## 화면 읽는 법

- **맨 위**: 판정(초록 정상 / 주황 주의 / 빨강 이상)과 원인 문장. 첫 10초는 "측정 중".
- **표**: CPU·GPU 의 온도, 부하, 전력, 클럭. 팬 RPM, 전원(어댑터·전원 계획·성능 모드).
- **평소값**: 이 PC 가 같은 부하에서 보통 몇 도였는지. 처음에는 "배우는 중 (n/30)" — 같은 부하로 5분쯤 켜 두면 채워진다.
- **근거**: 어떤 값이 어떤 기준을 넘었는지. 눌러서 펼친다.
- **맨 아래 ※**: 읽지 못한 센서와 그 이유.

판정은 10초 동안의 값(온도는 중앙값)으로 한다. 순간적으로 튄 온도로는 '이상'이 되지 않는다.

## 데이터

- 평소값: `%LOCALAPPDATA%\thermo\baseline.db` (SQLite). 지우면 처음부터 다시 배운다.
- 창이 켜져 있는 동안에만 쌓는다. 다른 곳으로 보내지 않는다.

## 개발

```
.venv\Scripts\python -m pytest -q
.venv\Scripts\python tools\probe.py          # 센서별로 무엇이 읽히는지 (관리자로 실행하면 전부)
```

테스트는 `THERMO_HOME` 을 임시 폴더로 바꿔 실제 평소값을 건드리지 않는다.

## 제3자 구성 요소

[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 참고. LibreHardwareMonitorLib(MPL-2.0) 등을 수정 없이 `vendor\lhm` 에 둔다.
