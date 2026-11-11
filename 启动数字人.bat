@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto noenv
if not exist "backend\main.py" goto nopy
if not exist ".env" copy /y ".env.example" ".env" >nul 2>&1

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
exit /b 0

:noenv
echo.
echo ERROR: .venv was not found.
echo This folder looks like the SOURCE CODE package, not the portable package.
echo Please choose one:
echo.
echo   1. Download the portable Windows package from GitHub Releases:
echo      https://github.com/fantaowang079-jpg/ai-digital-human/releases
echo      Extract ai-digital-human-windows.zip and run start.bat.
echo.
echo   2. Or set up the source environment:
echo      python -m venv .venv
echo      .venv\Scripts\activate
echo      pip install -r requirements.txt
echo      copy .env.example .env
echo      Then run this file again.
echo.
echo Opening SOLUTION.txt with download links and setup steps...
start "" "%~dp0SOLUTION.txt"
pause
exit /b 1

:nopy
echo.
echo ERROR: backend\main.py was not found.
echo Please extract the WHOLE zip and run this file from the project root.
echo.
echo Opening SOLUTION.txt with download links and setup steps...
start "" "%~dp0SOLUTION.txt"
pause
exit /b 1
