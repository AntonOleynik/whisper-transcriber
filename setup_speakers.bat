@echo off
setlocal
pushd "%~dp0"
set "TOOL_TEMP=%CD%\.tmp"
if not exist "%TOOL_TEMP%" mkdir "%TOOL_TEMP%"
set "TEMP=%TOOL_TEMP%"
set "TMP=%TOOL_TEMP%"

if not exist ".venv\Scripts\python.exe" (
  echo First run setup.bat and wait for the Done message.
  pause
  exit /b 1
)

where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo No NVIDIA GPU detected. The CPU build will be used.
  goto :install_cpu_torch
)

echo NVIDIA GPU detected. Installing the CUDA-enabled PyTorch build...
echo This download is large and may take several minutes.
.venv\Scripts\python.exe -m pip install --upgrade torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
  echo CUDA PyTorch could not be installed. Falling back to the CPU build.
  goto :install_cpu_torch
)
goto :torch_ready

:install_cpu_torch
echo Installing the CPU PyTorch build...
.venv\Scripts\python.exe -m pip install --upgrade torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :failed

:torch_ready
echo Installing the local speaker-diarization component. This may take several minutes.
.venv\Scripts\python.exe -m pip install --upgrade pyannote.audio==4.0.7
if errorlevel 1 goto :failed

echo Installing secure Windows token storage...
.venv\Scripts\python.exe -m pip install keyring
if errorlevel 1 (
  echo Secure token storage could not be installed. The app can still run, but the token will not be remembered.
  goto :failed
)

echo Done. Start Whisper with start.bat.
pause
exit /b 0

:failed
echo.
echo Speaker setup did not finish. Check the internet connection and run this file again.
pause
exit /b 1
