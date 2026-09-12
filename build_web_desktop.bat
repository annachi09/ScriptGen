@echo off
REM ============================================================
REM  ScriptGen (web+desktop) build script - run this ON WINDOWS,
REM  in this folder. Produces dist\ScriptGen.exe as a single
REM  standalone file that opens the web UI in a native window
REM  (desktop_launcher.py) instead of the legacy Tkinter app.
REM
REM  This is a SEPARATE script from build.bat on purpose: build.bat
REM  still builds the old Tkinter desktop app (app/ui/*, main.py),
REM  which keeps working and has more features right now (Dashboard,
REM  AI Assist, Tools tab - not yet ported to the web UI, see
REM  README's "Web + desktop, one codebase" section). Use THIS
REM  script once you're ready to switch over to the new UI; use
REM  build.bat for as long as you still need the Tkinter-only
REM  features.
REM
REM  Requires: Python 3.11+ installed on THIS machine (build-time
REM  only - the resulting .exe does NOT require Python to be
REM  installed on any machine that runs it). UNTESTED ON WINDOWS -
REM  written but not run, since the sandbox this was built in has
REM  no Windows GUI (same caveat as build.bat's own header always
REM  had, and as pywebview itself - see desktop_launcher.py).
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
pytest tests\test_diff_and_script.py tests\test_web_api.py tests\test_sql_pretty.py -q

echo [4/4] Building standalone ScriptGen.exe with PyInstaller...
REM --add-data bundles web/static (the frontend itself - index.html/
REM styles.css/app.js) into the .exe; PyInstaller's import analysis only
REM follows Python imports, so without this the window opens to a blank/
REM missing-file error. --hidden-import is needed because pywebview picks
REM its Windows backend (WebView2 via pythonnet) with a runtime
REM importlib.import_module() call based on sys.platform, which
REM PyInstaller's static analysis can't see coming - without it the .exe
REM fails at startup looking for a module it never bundled.
pyinstaller --noconfirm --onefile --windowed --name ScriptGen ^
    --add-data "web\static;web\static" ^
    --hidden-import webview.platforms.edgechromium ^
    --collect-data webview ^
    desktop_launcher.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete: dist\ScriptGen.exe
echo  Copy dist\ScriptGen.exe anywhere and double-click to run.
echo  A "data" folder (config + web logins + internal db) will be
echo  created next to the .exe the first time it runs.
echo ============================================================

endlocal
