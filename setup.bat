@echo off
setlocal
pushd "%~dp0"
set "TOOL_TEMP=%CD%\.tmp"
if not exist "%TOOL_TEMP%" mkdir "%TOOL_TEMP%"
set "TEMP=%TOOL_TEMP%"
set "TMP=%TOOL_TEMP%"

echo.
echo === Whisper setup ===
echo.

set "PYTHON_CMD="
py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.13"
if not defined PYTHON_CMD (
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.12"
)
if not defined PYTHON_CMD (
  py -3.11 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.11"
)
if not defined PYTHON_CMD (
  py -3.10 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.10"
)

if not defined PYTHON_CMD (
  echo Python 3.10-3.13 was not found.
  echo Install Python from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

set "VENV_READY="
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -m pip --version >nul 2>nul
  if not errorlevel 1 set "VENV_READY=1"
)

if not defined VENV_READY (
  if exist ".venv" rmdir /s /q ".venv"
  echo Creating an isolated environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing Whisper and dependencies. This can take several minutes...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed

where nvidia-smi >nul 2>nul
if errorlevel 1 goto :cpu_ready

echo NVIDIA GPU detected. Installing CUDA libraries...
.venv\Scripts\python.exe -m pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 "nvidia-cudnn-cu12>=9,<10"
if errorlevel 1 (
  echo.
  echo CUDA libraries were not installed. The app can still use the CPU.
  echo Check the NVIDIA driver and run setup.bat again for RTX support.
)
goto :ready

:cpu_ready
echo No NVIDIA GPU detected. The app will use the CPU.

:ready
echo.
echo Done. Start the app with start.bat
pause
exit /b 0

:failed
echo.
echo Setup did not finish. Check the internet connection and run setup.bat again.
pause
exit /b 1
