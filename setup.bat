@echo off
setlocal EnableExtensions
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo === WhisperTranscriber full setup ===
echo.

rem Step 1: find a supported Python, then install it through winget if needed.
call :find_python
if defined PYTHON_CMD goto :python_ready

echo Python 3.10-3.13 was not found.
echo Installing Python 3.13 with Windows Package Manager...
where winget >nul 2>nul
if errorlevel 1 goto :winget_missing

winget install --id Python.Python.3.13 --exact --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :python_install_failed

call :find_python
if defined PYTHON_CMD goto :python_ready

echo Python was installed but is not visible in this window yet.
echo Close this window and run setup.bat again.
pause
exit /b 1

:winget_missing
echo Windows Package Manager is unavailable, so Python cannot be installed automatically.
echo The official Python download page will open now. Install Python 3.13, then run setup.bat again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:python_install_failed
echo Python installation did not finish. Check your internet connection and run setup.bat again.
pause
exit /b 1

:python_ready
echo Using %PYTHON_CMD%.
set "TOOL_TEMP=%CD%\.tmp"
if not exist "%TOOL_TEMP%" mkdir "%TOOL_TEMP%"
set "TEMP=%TOOL_TEMP%"
set "TMP=%TOOL_TEMP%"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

set "VENV_READY="
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -m pip --version >nul 2>nul
  if not errorlevel 1 set "VENV_READY=1"
)

if not defined VENV_READY (
  if exist ".venv" rmdir /s /q ".venv"
  if exist ".venv" (
    echo The existing virtual environment is locked. Close Whisper and run setup.bat again.
    pause
    exit /b 1
  )
  echo Creating an isolated environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
  .venv\Scripts\python.exe -m pip --version >nul 2>nul
  if errorlevel 1 goto :failed
)

echo.
echo Installing transcription, converter, and speaker-recognition components...
echo This can take several minutes. NVIDIA speaker support downloads several GB.
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed

where nvidia-smi >nul 2>nul
if errorlevel 1 goto :install_cpu_components

echo NVIDIA GPU detected. Installing CUDA libraries and CUDA PyTorch...
.venv\Scripts\python.exe -m pip install --upgrade nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 "nvidia-cudnn-cu12>=9,<10"
if errorlevel 1 (
  echo CUDA libraries could not be installed. Falling back to CPU components.
  goto :install_cpu_components
)
.venv\Scripts\python.exe -m pip install --upgrade torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
  echo CUDA PyTorch could not be installed. Falling back to CPU components.
  goto :install_cpu_components
)
goto :install_speaker_component

:install_cpu_components
echo Installing CPU components. NVIDIA acceleration will not be used.
.venv\Scripts\python.exe -m pip install --upgrade torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :failed

:install_speaker_component
.venv\Scripts\python.exe -m pip install --upgrade pyannote.audio==4.0.7
if errorlevel 1 goto :failed

echo.
echo Done. Start the app with start.bat
echo The first transcription downloads the selected Whisper model.
echo The first speaker analysis also requires your Hugging Face token and downloads its model.
pause
exit /b 0

:find_python
set "PYTHON_CMD="
for %%V in (3.13 3.12 3.11 3.10) do (
  py -%%V -c "import sys" >nul 2>nul
  if not errorlevel 1 if not defined PYTHON_CMD set "PYTHON_CMD=py -%%V"
)
if defined PYTHON_CMD exit /b 0

if not defined PYTHON_CMD (
  python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if defined PYTHON_CMD exit /b 0

for %%P in (
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%LocalAppData%\Programs\Python\Python310\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
  "%ProgramFiles%\Python310\python.exe"
) do (
  if not defined PYTHON_CMD if exist "%%~P" (
    "%%~P" -c "import sys" >nul 2>nul
    if not errorlevel 1 set PYTHON_CMD="%%~P"
  )
)
exit /b 0

:failed
echo.
echo Setup did not finish.
echo If the message mentions WinError 32 or a file being used by another process,
echo close WhisperTranscriber and any command window running it, then run setup.bat again.
echo Otherwise, check the internet connection and run setup.bat again.
pause
exit /b 1
