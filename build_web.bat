@echo off
REM ============================================================
REM  ScriptGen (web-only) build script - run this ON WINDOWS, in
REM  this folder. Produces dist\ScriptGen-Web.exe: a single
REM  standalone file that starts the web server and opens it in
REM  your actual default browser (run_web.py) - a real browser
REM  tab, not a native app window and not the old Tkinter app.
REM
REM  This is a THIRD build script, distinct from the other two on
REM  purpose - all three package the exact same web/server.py
REM  backend, just with a different launch shell:
REM    build.bat            -> the legacy Tkinter desktop app (app/ui/*)
REM    build_web_desktop.bat -> web UI in a chrome-less native window (pywebview)
REM    build_web.bat (this one) -> web UI in your normal browser tab
REM  Use whichever launch style you actually want; nothing else
REM  about the app (routes, script generation, the database logic)
REM  differs between them.
REM
REM  Requires: Python 3.11+ installed on THIS machine (build-time
REM  only - the resulting .exe does NOT require Python to be
REM  installed on any machine that runs it). UNTESTED ON WINDOWS -
REM  written but not run: this was built in a Linux sandbox with no
REM  Windows GUI and, this session, no working shell at all - please
REM  run this yourself and let me know if anything needs fixing.
REM ============================================================

setlocal

echo [1/4] Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo Failed to create virtual environment. Is Python installed and on PATH?
    exit /b 1
)

echo [2/4] Installing dependencies...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency install failed.
    exit /b 1
)

echo [3/4] Running tests (safe to ignore failures here and continue if you're iterating)...
pytest tests\test_diff_and_script.py tests\test_web_api.py tests\test_sql_pretty.py tests\test_date_anomaly.py -q

echo [4/4] Building standalone ScriptGen-Web.exe with PyInstaller...
REM --add-data bundles web/static (index.html/styles.css/app.js) into
REM the .exe - PyInstaller's import analysis only follows Python
REM imports, so without this the browser would load to a 404/blank
REM page. No --windowed here (unlike build_web_desktop.bat) and no
REM pywebview-related flags - run_web.py never imports pywebview at
REM all, and the console window is kept ON PURPOSE so there's a visible
REM "how do I stop this" (Ctrl+C) - see run_web.py's own docstring.
pyinstaller --noconfirm --onefile --name ScriptGen-Web ^
    --add-data "web\static;web\static" ^
    run_web.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete: dist\ScriptGen-Web.exe
echo  Double-click it - it starts the server, opens your default
echo  browser to http://127.0.0.1:8420/, and keeps a console window
echo  open (Ctrl+C there to stop). A "data" folder (config + web
echo  logins + internal db) is created next to the .exe on first run.
echo ============================================================

endlocal
