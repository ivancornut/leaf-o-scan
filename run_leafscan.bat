@echo off
REM Start LeafScan.  Usage:  run_leafscan.bat [path\to\venv]
cd /d "%~dp0"
REM --- choose the Python interpreter -------------------------------------
REM  1. a venv folder given as first argument, e.g.  %~nx0 C:\envs\leafscan
REM  2. the venv folder written on the first line of venv_path.txt (next to this file)
REM  3. the venv that is currently activated (VIRTUAL_ENV)
REM  4. .venv inside the project folder
REM  5. "python" found on the PATH
set "PY="
set "VENVDIR="
if not "%~1"=="" if exist "%~1\Scripts\python.exe" set "PY=%~1\Scripts\python.exe"
if not defined PY if exist "venv_path.txt" (
  set /p VENVDIR=<"venv_path.txt"
)
if not defined PY if defined VENVDIR if exist "%VENVDIR%\Scripts\python.exe" set "PY=%VENVDIR%\Scripts\python.exe"
if not defined PY if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PY=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"
echo Using Python: %PY%
"%PY%" -m leafscan
if errorlevel 1 pause
