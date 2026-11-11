@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not defined PORT set "PORT=8000"
if not exist "logs" mkdir "logs" >nul 2>&1
echo AI Digital Human server is starting on port %PORT% ...
echo Keep this window open. Press Ctrl+C to stop.
echo.
"%~dp0python\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
echo.
echo Server stopped. Check logs\app.log for details.
pause
