; thermo 설치 파일 (NSIS 3, 유니코드). tools\build_app.py 가 /DVERSION=... /DSRC=dist\thermo /DOUT=... 로 부른다.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APPNAME "thermo"
!define APPTITLE "thermo 온도 진단"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\thermo"

Name "${APPTITLE}"
OutFile "${OUT}"
InstallDir "$PROGRAMFILES64\thermo"
InstallDirRegKey HKLM "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "thermo ${VERSION}"

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\thermo.exe"
!define MUI_FINISHPAGE_RUN_TEXT "thermo 실행"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=${LANG_KOREAN} "ProductName" "${APPTITLE}"
VIAddVersionKey /LANG=${LANG_KOREAN} "FileDescription" "${APPTITLE} 설치"
VIAddVersionKey /LANG=${LANG_KOREAN} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_KOREAN} "LegalCopyright" "MIT, pyk-kentech"

Section "thermo (필수)" SecMain
  SectionIn RO
  ${If} ${RunningX64}
    SetRegView 64
  ${EndIf}
  SetOutPath "$INSTDIR"
  ; 이전 판의 파일이 남지 않게 지우고 새로 넣는다 (사용자 기록은 %LOCALAPPDATA%\thermo 에 있어 건드리지 않는다)
  RMDir /r "$INSTDIR\_internal"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$SMPROGRAMS\thermo"
  CreateShortCut "$SMPROGRAMS\thermo\${APPTITLE}.lnk" "$INSTDIR\thermo.exe" "" "$INSTDIR\thermo.exe" 0
  CreateShortCut "$SMPROGRAMS\thermo\thermo 제거.lnk" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APPTITLE}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "pyk-kentech"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\thermo.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "URLInfoAbout" "https://github.com/pyk-kentech/thermo"
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "바탕화면 바로가기" SecDesktop
  CreateShortCut "$DESKTOP\${APPTITLE}.lnk" "$INSTDIR\thermo.exe" "" "$INSTDIR\thermo.exe" 0
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "온도 진단 앱(창)과 기준 측정용 콘솔 도구."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "바탕화면에 '${APPTITLE}' 바로가기를 만듭니다."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInstSuccess
  ; CPU 온도·전력·스로틀은 PawnIO 드라이버로 읽는다. 없으면 알려 준다(설치는 사용자가 직접).
  ${If} ${RunningX64}
    SetRegView 64
  ${EndIf}
  ReadRegStr $0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO" "DisplayName"
  ${If} $0 == ""
    MessageBox MB_YESNO|MB_ICONINFORMATION "CPU 온도·전력을 읽으려면 PawnIO 드라이버가 필요합니다.$\n지금 PawnIO 받는 곳을 열까요?$\n(없어도 thermo 는 켜지지만 판단은 하지 못합니다)" IDNO +2
      ExecShell "open" "https://github.com/namazso/PawnIO.Setup/releases"
  ${EndIf}
FunctionEnd

Section "Uninstall"
  ${If} ${RunningX64}
    SetRegView 64
  ${EndIf}
  Delete "$DESKTOP\${APPTITLE}.lnk"
  RMDir /r "$SMPROGRAMS\thermo"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
  MessageBox MB_YESNO|MB_ICONQUESTION "기록과 기준 측정 결과(%LOCALAPPDATA%\thermo)도 지울까요?$\n지우면 기준 측정을 다시 해야 합니다." /SD IDNO IDNO +2
    RMDir /r "$LOCALAPPDATA\thermo"
SectionEnd
