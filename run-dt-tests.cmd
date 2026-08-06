@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\exchange-ews-mcp.exe" (
  echo Virtual environment not found. Run install.cmd first.
  exit /b 1
)
if /I "%~1"=="--full" (
  echo Running live Exchange DT including unsent draft creation.
  ".venv\Scripts\exchange-ews-mcp.exe" dt-test
) else (
  echo Running read-only live Exchange DT. Pass --full to include unsent draft creation.
  ".venv\Scripts\exchange-ews-mcp.exe" dt-test --read-only
)
exit /b %errorlevel%
