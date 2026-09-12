@echo off
REM ============================================================
REM  ScriptGen build script - run this ON WINDOWS, in this folder.
REM  Produces dist\ScriptGen.exe as a single standalone file.
REM
REM  Requires: Python 3.11+ installed on THIS machine (build-time
REM  only - the resulting .exe does NOT require Python to be
REM  installed on any machine that runs it).
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
pytest tests\test_diff_and_script.py tests\test_stats.py -q

echo [4/4] Building standalone ScriptGen.exe with PyInstaller...
REM --collect-data pulls in each package's non-Python files (ttkbootstrap's
REM icon/font assets, matplotlib's font/style data) - PyInstaller's default
REM analysis only follows Python imports, so without this the .exe launches
REM but crashes immediately looking for files that were never bundled.
pyinstaller --noconfirm --onefile --windowed --name ScriptGen ^
    --collect-data ttkbootstrap ^
    --collect-data matplotlib ^
    --hidden-import PIL._tkinter_finder ^
    main.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete: dist\ScriptGen.exe
echo  Copy dist\ScriptGen.exe anywhere and double-click to run.
echo  A "data" folder (config + internal db) will be created next
echo  to the .exe the first time it runs.
echo ============================================================

endlocal
