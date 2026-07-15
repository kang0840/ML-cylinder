@echo off
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 server.py --host 127.0.0.1 --port 8000
) else (
  where python >nul 2>nul
  if %ERRORLEVEL% EQU 0 (
    python server.py --host 127.0.0.1 --port 8000
  ) else (
    echo Python was not found. Install Python 3 and try again.
    pause
    exit /b 1
  )
)

pause
