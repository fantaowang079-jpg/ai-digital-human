@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs" >nul 2>&1
set "OUT=%~dp0logs\diagnose.txt"
> "%OUT%" 2>&1 (
  echo AI Digital Human diagnostic report
  echo Date: %DATE% %TIME%
  echo Folder: %CD%
  echo.
  echo [Files]
  if exist "python\python.exe" (echo python\python.exe: OK) else (echo python\python.exe: MISSING)
  if exist "backend\main.py" (echo backend\main.py: OK) else (echo backend\main.py: MISSING)
  if exist "frontend\index.html" (echo frontend\index.html: OK) else (echo frontend\index.html: MISSING)
  if exist "run_server.bat" (echo run_server.bat: OK) else (echo run_server.bat: MISSING)
  if exist "start.bat" (echo start.bat: OK) else (echo start.bat: MISSING)
  if exist ".env" (echo .env: present) else (echo .env: missing)
  echo.
  echo [Python]
  if exist "python\python.exe" ("python\python.exe" --version) else (echo Python: MISSING)
  echo.
  echo [Listening ports 8000-8010]
  for %%P in (8000 8001 8002 8003 8004 8005 8006 8007 8008 8009 8010) do (
    netstat -ano | findstr /R /C:":%%P " | findstr /I "LISTENING"
  )
  echo.
  echo [App log]
  if exist "logs\app.log" (
    echo logs\app.log is present. Please send the whole logs folder.
  ) else (
    echo app.log: not found
  )
)
type "%OUT%"
echo.
echo Report saved to: %OUT%
echo Send the whole logs folder to the developer.
pause
