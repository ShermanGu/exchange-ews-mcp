@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.cmd first.
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -e ".[test]"
if errorlevel 1 exit /b %errorlevel%
".venv\Scripts\python.exe" -m pytest -q
exit /b %errorlevel%
