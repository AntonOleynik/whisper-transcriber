@echo off
setlocal
pushd "%~dp0"
set "TOOL_TEMP=%CD%\.tmp"
if not exist "%TOOL_TEMP%" mkdir "%TOOL_TEMP%"
set "TEMP=%TOOL_TEMP%"
set "TMP=%TOOL_TEMP%"

if not exist ".venv\Scripts\pythonw.exe" (
  echo First run setup.bat and wait for the Done message.
  pause
  exit /b 1
)

start "" .venv\Scripts\pythonw.exe audio_converter.py
