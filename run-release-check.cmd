@echo off
setlocal
cd /d "%~dp0"

rem Use the same Python command that works in the current shell first.
rem This avoids forcing .venv\Scripts\python.exe when pytest is installed
rem in another Python environment.
python -c "import pytest" >nul 2>nul
if errorlevel 1 goto try_project_venv
python scripts\release_check.py %*
exit /b %errorlevel%

:try_project_venv
rem Fall back to the project virtual environment when it contains pytest.
if not exist ".venv\Scripts\python.exe" goto try_python_launcher
".venv\Scripts\python.exe" -c "import pytest" >nul 2>nul
if errorlevel 1 goto try_python_launcher
".venv\Scripts\python.exe" scripts\release_check.py %*
exit /b %errorlevel%

:try_python_launcher
rem Finally try the Windows Python launcher.
py -3 -c "import pytest" >nul 2>nul
if errorlevel 1 goto no_pytest
py -3 scripts\release_check.py %*
exit /b %errorlevel%

:no_pytest
echo No usable Python interpreter with pytest was found.
echo Install test dependencies with:
echo   python -m pip install -e ".[test]"
echo Then verify with:
echo   python -m pytest --version
exit /b 1
