@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Project virtual environment was not found.
    echo Create .venv and install xl_updata_tool\requirements.txt first.
    pause
    exit /b 1
)
"%PYTHON%" main.py %*
pause
