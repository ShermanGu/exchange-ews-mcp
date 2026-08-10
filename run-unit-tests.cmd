@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.cmd first.
  exit /b 1
)
set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
set PYTHONWARNINGS=default
".venv\Scripts\python.exe" -m pip install -e ".[test]"
if errorlevel 1 exit /b %errorlevel%
".venv\Scripts\python.exe" -m pytest -W error::ResourceWarning
exit /b %errorlevel%
