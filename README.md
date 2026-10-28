# thermo

ASUS TUF Gaming F17 (FX707VU) 한 대 전용 온도 진단 도구.

지금 돌아가는 프로그램, 전력, 온도, 팬 속도, CPU·GPU 가 스스로 남긴 '속도를 낮춘 이유', 전원 상태를 한꺼번에 보고,
**이 노트북을 직접 재서 만든 기준**과 비교해 "문제가 있는가, 있다면 어디인가"를 근거와 함께 말한다.
상주하지 않는다. 창을 닫으면 끝난다. 팬·성능 모드·전원 설정·CPU 레지스터는 읽기만 하고 바꾸지 않는다.

설계·측정 기록·판단 기준의 근거는 [DESIGN.md](DESIGN.md).

## 설치 (앱)

1. [Releases](https://github.com/pyk-kentech/thermo/releases) 에서 `thermo-setup-<버전>.exe` 를 받아 실행한다
   (또는 직접 만들기: 아래 '개발'). 서명이 없어 Windows SmartScreen 경고가 뜰 수 있다 — '추가 정보 → 실행'.
2. 설치가 끝나면 PawnIO 가 없을 때 받는 곳을 열어 준다. **PawnIO** 는 CPU 온도·전력·95°C 스로틀 기록(MSR)을 읽는 데
   쓰는 드라이버로, 사용자가 직접 설치한다: https://github.com/namazso/PawnIO.Setup/releases (없으면 판단은 하지 않고 숫자만 보인다).
3. 시작 메뉴·바탕화면의 **thermo 온도 진단** 을 켠다(관리자 권한을 묻는다).
4. **기준 측정(건강검진)** — 창의 '기준 측정' 탭에서 지금 성능 모드를 고르고 시작(약 25분, 중간에 게임을 켜고 끄는 안내가 나온다).
   기준이 없으면 '평소보다 뜨거운가'는 판단하지 못한다.

설치 폴더에는 `thermo.exe`(창)와 `thermo-cli.exe`(콘솔: `--once`, `--checkup` 등)가 있다.
지울 때는 Windows '설치된 앱' 또는 시작 메뉴의 'thermo 제거'. 기록(`%LOCALAPPDATA%	hermo`)은 물어보고 지운다.
글꼴은 [Pretendard](https://github.com/orioncactus/pretendard)(OFL-1.1)가 설치돼 있으면 쓰고, 없으면 맑은 고딕을 쓴다.

## 소스에서 돌리기

1. Python 3.11 이상.
2. 설치

   ```
   cd D:	hermo
   python -m venv .venv
   .venv\Scripts\python -m pip install -e ".[dev]"
   .venv\Scripts\python toolsetch_lhm.py     # vendor\lhm 이 비어 있을 때만
   ```

3. PawnIO (위와 같음), 바탕화면 바로가기(선택): `.venv\Scripts\python tools\make_shortcut.py`

## 쓰기

| 명령 | 하는 일 |
|---|---|
| 바로가기 `thermo 온도 진단` 또는 `.venv\Scripts\thermo.exe` | 창을 띄운다. 관리자 권한을 UAC 로 요청한다. |
| `thermo-cli --once --seconds 130` (소스: `python -m thermo ...`) | 130초 재고 판단을 터미널에 쓴다. |
| `... -m thermo --checkup Performance` | 기준 측정을 콘솔에서 한다. |
| `... -m thermo --rebuild 1,2 --mode Performance` | 저장된 기준 측정을 합쳐 다시 계산한다. |
| `... -m thermo --verify` | 검증 시나리오를 진행한다(DESIGN 10장). |

## 화면

- **맨 위**: 판정(초록 정상 / 주황 주의 / 빨강 이상)과 한 줄 요약. 오른쪽에 지금 기준, 전원(어댑터·배터리), 마지막 갱신 시각, '항상 위'(게임 위에 띄우기).
- **카드**: CPU·GPU·팬의 큰 숫자, 전력·사용률, 95°C 스로틀·GPU 제한 표시, 최근 2분 미니 그래프.
- **전력을 쓰는 프로그램**: 이름, CPU·GPU 사용률, 추정 W, 창 없는 작업 표시.
- **판단**: 사실 → 기준과 비교 → 원인 순서의 문장.
  예: "CPU 가 47W 를 쓰며 95°C 에 닿아 10초 중 50% 동안 속도를 낮췄습니다. 기준 측정 때도 이 전력에서 똑같이 묶였습니다 — 이 모드의 냉각 설계 한계이며 고장은 아닙니다."
- **근거 탭**: 기대 온도·실제 온도·차이(σ 배수), 팬 기대값, 레지스터 비율.
- **기록 탭**: 최근 10분 온도·전력·팬 그래프.
- **기준 측정 탭**: 지금 기준의 요약, 다시 재기.

부하가 바뀌는 동안(최근 2분 동안 전력이 안정되지 않음, 팬이 따라오는 중)에는 비교를 미루고, 마지막 비교 결과와 그 시각을 함께 보인다.

## 무엇을 근거로 판단하나

- **하드웨어 사실**: CPU 95°C 스로틀(MSR 0x1B1, 1초에 10번 읽음), 외부 PROCHOT, GPU 제한 이유별 시간(NVML), 배터리.
- **기준과 비교**: 기준 측정으로 만든 "이 노트북은 CPU·GPU 가 몇 W 쓰면 몇 도인가" 모델과 지금 온도의 차이를,
  같은 방식으로 잰 흩어짐(σ)의 몇 배인지로 본다(3σ 주의, 6σ 이상). 95°C 에 묶여 있으면 '몇 W 에서 묶였나'로 비교한다.
  팬은 같은 온도에서 기준 측정 때만큼 도는지 본다.
- **원인**: 부하에 맞는 발열(범인 프로그램과 추정 W) / 냉각 설계 한계 / 열이 잘 빠지지 않음(먼지·서멀구리스·통풍) /
  팬이 덜 돎·다른 성능 모드 / 전력 한도(간접) / 그래픽카드 열이 히트파이프로 CPU 를 데움 / 바깥에서 CPU 를 늦춤.

## 알고 쓰셔야 할 한계 (2026-10-24 기준)

- **실기 검증을 하지 않았다.** 확인된 것은 기준 측정의 실제 기록 5구간(공회전·CPU 최대 부하·게임)을 다시 돌린 결과와 공회전 실측뿐이다.
  배터리·Silent 모드·Defender 검사·냉각 저하 진단은 합성 데이터 테스트로만 확인했다.
- **기준은 Performance 모드 하나.** 다른 모드로 쓰면 "기준과 다른 모드로 보입니다"라고만 말한다.
- **그래픽카드 50W 넘는 부하는 기준 범위 밖**(기준 측정 게임이 30–50W). 그때는 범위 밖이라고 표시한다.
- **한 코어에 몰린 부하(1스레드)**: 코어 평균 온도 기준이 평소 기록으로 모이기 전(30구간)에는 CPU 가 실제보다 뜨겁게 판단될 수 있다. 그때는 판단 문장에 그 단서를 붙인다.
- **방 온도 센서가 없다.** 공회전 때의 차이를 방 온도 차이로 보고 최대 ±6°C 보정한다.
- 먼지와 서멀구리스 열화는 구분하지 못한다.
- CPU '전력 한도' 판단은 간접이다(이 노트북에서 제한 이유 레지스터 0x64F 가 늘 0).

## 데이터

`%LOCALAPPDATA%\thermo\` — `thermo.db`(1초 기록 30일, 기준 측정 구간은 계속 보관, 기준 측정 결과), `checkup.log`, `verify.log`.
창이 켜져 있는 동안에만 쌓고, 다른 곳으로 보내지 않는다.

## 개발

```
.venv\Scripts\python -m pip install -e ".[dev,build]"
.venv\Scripts\python toolsuild_app.py     # dist	hermo-setup-<버전>.exe (NSIS 의 makensis 필요)
.venv\Scripts\python -m pytest -q
.venv\Scripts\python tools\probe.py          # 센서별로 무엇이 읽히는지 (관리자로 실행하면 전부)
```

테스트는 `THERMO_HOME` 을 임시 폴더로 바꿔 실제 기록을 건드리지 않는다.

## 라이선스

MIT — [LICENSE](LICENSE). 단, `vendor\lhm` 의 DLL 은 각자의 라이선스를 따른다(아래).

## 제3자 구성 요소

[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 참고. LibreHardwareMonitorLib(MPL-2.0) 등을 수정 없이 `vendor\lhm` 에 둔다.
