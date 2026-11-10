@echo off
REM Start Homebase on Windows. First run creates .venv and installs packages (needs internet once).
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
  echo Setting up Homebase, first run only...
  %PY% -m venv .venv || (echo Could not create .venv. Install Python from python.org with "tcl/tk" checked. & pause & exit /b 1)
)
if not exist ".venv\installed.txt" (
  ".venv\Scripts\python.exe" -m pip install --quiet "cryptography>=42" "Pillow>=10" certifi || (echo Could not install packages. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet "qrcode>=7" tkinterdnd2-universal
  echo ok> ".venv\installed.txt"
)
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
