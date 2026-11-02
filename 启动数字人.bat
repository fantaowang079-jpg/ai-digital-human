@echo off
cd /d "%~dp0"
echo Starting AI Digital Human server...
start "AI Digital Human Server" cmd /k "cd /d "%~dp0" && .venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
timeout /t 5 /nobreak >nul
start "" http://localhost:8000
echo.
echo ==========================================
echo   Server started. Browser will open.
echo   Close the black window to stop server.
echo ==========================================
echo.
pause
