@echo off
setlocal

REM ScriptGen - run from SOURCE, no build/PyInstaller step needed.
REM Double-click this any time to try the latest code changes. It never
REM touches the data folder - your saved connection, password, and web
REM logins live there and are not affected by this script or by code
REM edits.
REM
REM Uses cmd's own venv activation, not PowerShell's - so this works
REM even if PowerShell script execution is disabled on this machine,
REM since double-clicking a .bat always runs under cmd.exe.
REM
REM First run: creates .venv and installs requirements.txt - takes a
REM minute or two. Every run after that: just checks/updates
REM requirements.txt (fast) and starts the app.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto have_venv

echo [1/3] Creating virtual environment .venv ...
python -m venv .venv
if errorlevel 1 goto venv_failed
goto install_deps

:venv_failed
echo Failed to create virtual environment. Is Python installed and on PATH?
pause
exit /b 1

:have_venv
echo [1/3] Virtual environment already exists - skipping creation.

:install_deps
echo [2/3] Installing/updating dependencies...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if errorlevel 1 goto deps_failed
goto start_app

:deps_failed
echo Dependency install failed.
pause
exit /b 1

:start_app
echo [3/3] Starting ScriptGen...
echo.
python run_web.py

REM run_web.py only returns after Ctrl+C, or right away if it just
REM opened your browser to an already-running instance - pause so a
REM double-click launch doesn't instantly close the window.
pause
endlocal
