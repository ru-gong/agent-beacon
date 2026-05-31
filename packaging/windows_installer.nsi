!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif

!define APP_NAME "Agent Beacon"
!define APP_PUBLISHER "ru-gong"
!define APP_EXE "Agent Beacon.exe"
!define APP_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent Beacon"

Name "${APP_NAME}"
OutFile "release\windows\Agent-Beacon-${APP_VERSION}-Windows-Setup.exe"
InstallDir "$LOCALAPPDATA\Agent Beacon"
RequestExecutionLevel user
Icon "assets\agent-beacon.ico"
UninstallIcon "assets\agent-beacon.ico"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /oname="${APP_EXE}" "dist\Agent Beacon.exe"

  CreateDirectory "$SMPROGRAMS\Agent Beacon"
  CreateShortcut "$SMPROGRAMS\Agent Beacon\Agent Beacon.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$DESKTOP\Agent Beacon.lnk" "$INSTDIR\${APP_EXE}"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APP_REG_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${APP_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${APP_REG_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${APP_REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${APP_REG_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${APP_REG_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "${APP_REG_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${APP_REG_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\Agent Beacon.lnk"
  Delete "$SMPROGRAMS\Agent Beacon\Agent Beacon.lnk"
  RMDir "$SMPROGRAMS\Agent Beacon"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${APP_REG_KEY}"
SectionEnd
