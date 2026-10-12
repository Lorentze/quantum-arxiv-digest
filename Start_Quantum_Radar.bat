@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto python_required
py -3 -m qradar serve --open %*
goto finished
:use_python
where python >nul 2>nul
if errorlevel 1 goto python_required
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto python_required
python -m qradar serve --open %*
goto finished
:python_required
echo Python 3.11 or newer is required: https://www.python.org/downloads/
:finished
pause
