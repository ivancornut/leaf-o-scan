@echo off
REM Build a standalone Windows version of LeafScan (folder dist\LeafScan with LeafScan.exe).
REM Usage:  build_exe.bat [path\to\venv]      (run on the Windows PC, after installing requirements.txt)
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
"%PY%" -c "import PySide6, skimage, pandas" || (echo The requirements are not installed in this Python. & goto :error)
"%PY%" -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib || goto :error
"%PY%" -m PyInstaller --noconfirm --clean --windowed --onedir --name LeafScan ^
  --collect-submodules skimage ^
  --hidden-import openpyxl --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes ^
  --exclude-module tkinter --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module pytest ^
  run_leafscan.py || goto :error
echo.
echo Done: dist\LeafScan\LeafScan.exe
echo Copy the whole dist\LeafScan folder; the .exe needs the files next to it.
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
