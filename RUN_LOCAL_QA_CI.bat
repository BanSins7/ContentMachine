@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo ===== CONTENTMACHINE / LOCAL QA + CI =====
echo [SAFE] Production contentmachine database is NEVER a test target.
echo [SAFE] Full mode creates and cleans isolated PostgreSQL17 databases.
echo [INFO] Stop the running ContentMachine server before starting.
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [FAIL] Python not found in PATH.
  pause
  exit /b 1
)
python -u -m scripts.local_qa_ci %*
set "QA_EXIT=%ERRORLEVEL%"
echo.
if "%QA_EXIT%"=="0" (
  echo [OK] Local technical checks passed. Read report.json for limitations.
) else (
  echo [NOT READY] One or more local QA checks failed, skipped or were not run.
)
echo [INFO] Find the QA_REPORT path in output. No PostgreSQL volumes are deleted.
echo.
pause
exit /b %QA_EXIT%
