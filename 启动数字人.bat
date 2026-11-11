@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "python\python.exe" goto missing
if not exist "backend\main.py" goto missing
if not exist "run_server.bat" goto missing
if not exist "frontend\index.html" goto missing

if not exist ".env" copy /y ".env.example" ".env" >nul 2>&1
if not exist "logs" mkdir "logs" >nul 2>&1

set "PORT=8000"
:findport
netstat -ano | findstr /R /C:":%PORT% " | findstr /I "LISTENING" >nul 2>&1
if errorlevel 1 goto portfound
set /a PORT+=1
if %PORT% GTR 8010 goto noport
goto findport

:portfound
echo Starting AI Digital Human on port %PORT% ...
start "AI Digital Human Server" "%~dp0run_server.bat"
timeout /t 4 /nobreak >nul 2>&1
start "" "http://localhost:%PORT%"
echo.
echo ==========================================
echo   AI Digital Human
echo   Browser: http://localhost:%PORT%
echo   Close the server window to stop.
echo ==========================================
echo.
pause
exit /b 0

:missing
echo.
echo ERROR: Required files are missing.
echo Please EXTRACT THE WHOLE ZIP first, then run this file.
echo Do NOT run it directly inside the zip preview.
echo If antivirus removed files, restore them or whitelist this folder.
echo.
echo Current folder: %CD%
if not exist "python\python.exe" echo   MISSING: python\python.exe
if not exist "backend\main.py" echo   MISSING: backend\main.py
if not exist "run_server.bat" echo   MISSING: run_server.bat
if not exist "frontend\index.html" echo   MISSING: frontend\index.html
echo.
echo Run diagnose.bat and send the logs folder to the developer.
echo Opening SOLUTION.txt with download links and setup steps...
start "" "%~dp0SOLUTION.txt"
pause
exit /b 1

:noport
echo.
echo ERROR: Ports 8000-8010 are all in use.
echo Close other programs and try again.
echo Opening SOLUTION.txt with download links and setup steps...
start "" "%~dp0SOLUTION.txt"
pause
exit /b 1
