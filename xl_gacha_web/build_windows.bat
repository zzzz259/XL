@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Windows x64 / Python 3.11+ / internet access to PyPI are required to BUILD.
rem End users do not need Python once the package is built.
if exist ".venv\Scripts\python.exe" goto found_venv

where py.exe >nul 2>&1
if errorlevel 1 goto try_python
py -3 -c "import sys,struct; assert sys.version_info >= (3,11) and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 goto try_python
py -3 -m venv ".venv"
if errorlevel 1 goto fail_venv
goto found_venv

:try_python
where python.exe >nul 2>&1
if errorlevel 1 goto no_python
python -c "import sys,struct; assert sys.version_info >= (3,11) and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 goto no_python
python -m venv ".venv"
if errorlevel 1 goto fail_venv
goto found_venv

:found_venv
set "PYTHON=%CD%\.venv\Scripts\python.exe"
"%PYTHON%" -c "import sys,struct; assert sys.version_info >= (3,11) and struct.calcsize('P') == 8"
if errorlevel 1 goto bad_venv
"%PYTHON%" -m pip install --disable-pip-version-check "pyinstaller>=6.12,<7"
if errorlevel 1 goto fail_deps
"%PYTHON%" -B "scripts\build_portable.py" --output "output"
if errorlevel 1 goto fail_build

echo.
echo SUCCESS. The Windows portable ZIP is under: %CD%\output
echo Extract that ZIP completely, then run StarfallGacha.exe.
echo.
pause
exit /b 0

:no_python
echo ERROR: Python 3.11+ (64-bit) is required to build this project.
echo Install Python from python.org and enable Python Launcher (py.exe).
goto failed
:fail_venv
echo ERROR: Could not create Python virtual environment.
goto failed
:bad_venv
echo ERROR: .venv is not using 64-bit Python 3.11+.
echo Close running Python processes, rename .venv and run again.
goto failed
:fail_deps
echo ERROR: Could not install PyInstaller. Check network access to PyPI.
goto failed
:fail_build
echo ERROR: Windows package build failed. See the errors above.
goto failed
:failed
echo.
pause
exit /b 1
