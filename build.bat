@echo off
REM Builds dist\TheMover.exe on Windows.  Requires Python 3.10+ on PATH.
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pyinstaller --noconfirm TheMover.spec
echo.
echo Done. Your executable is dist\TheMover.exe
pause
