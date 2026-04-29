@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Allow running without pip install -e . (package lives under src\).
set "PYTHONPATH=%~dp0src"

echo.
echo T9FOX — starting server with /api (not plain http.server)
echo Open: http://127.0.0.1:8765/
echo Health: http://127.0.0.1:8765/api/health
echo Press Ctrl+C to stop.
echo.

py -3 -m t9fox.cli serve %*
if errorlevel 1 (
  echo.
  echo Failed. Try: py -3 -m pip install -e .
  pause
)
