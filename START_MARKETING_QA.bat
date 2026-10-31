@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo.
echo [MARKETING QA] Checking checkout and localhost port 8765...
where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git is not available on PATH.
  exit /b 1
)
for /f "delims=" %%B in ('git branch --show-current') do set "CURRENT_BRANCH=%%B"
if /I not "%CURRENT_BRANCH%"=="feature/rtb-market-intelligence-20261030" (
  echo [ERROR] This launcher must run from feature/rtb-market-intelligence-20261030.
  echo [INFO] Current branch: %CURRENT_BRANCH%
  echo [INFO] From C:\ContentMachine run:
  echo        git switch feature/rtb-market-intelligence-20261030
  exit /b 1
)
if not exist "site\market-intelligence.html" (
  echo [ERROR] Missing site\market-intelligence.html in the checkout.
  exit /b 1
)
if not exist "site\market-intelligence.js" (
  echo [ERROR] Missing site\market-intelligence.js in the checkout.
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check_marketing_local_server.ps1"
if errorlevel 1 exit /b 1
echo [MARKETING QA] Starting ContentMachine from %CD%
call "%~dp0START_CONTENTMACHINE.bat"
exit /b %ERRORLEVEL%
