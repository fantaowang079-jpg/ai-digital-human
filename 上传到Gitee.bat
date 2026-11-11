@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv not found. Run this from the project root.
  pause
  exit /b 1
)

set "GITEE_TOKEN="
set /p GITEE_TOKEN=Paste your Gitee personal access token:
if "%GITEE_TOKEN%"=="" (
  echo No token entered.
  pause
  exit /b 1
)

.venv\Scripts\python.exe tools\upload_gitee_release.py
pause
