@echo off
setlocal
pushd "%~dp0"
set "TOOL_TEMP=%CD%\.tmp"
if not exist "%TOOL_TEMP%" mkdir "%TOOL_TEMP%"
set "TEMP=%TOOL_TEMP%"
set "TMP=%TOOL_TEMP%"

if not exist ".venv\Scripts\pythonw.exe" (
  echo Сначала запустите setup.bat и дождитесь сообщения "Готово".
  pause
  exit /b 1
)

start "" .venv\Scripts\pythonw.exe transcribe_gui.py
