@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
chcp 65001 >nul
title ContentMachine - Carousel Backend [127.0.0.1:8765]
set "CM_NO_BROWSER=1"
echo.
echo ============================================================
echo   CONTENTMACHINE  /  CAROUSEL ANALYSIS BACKEND
echo ============================================================
echo   URL:  http://127.0.0.1:8765/carousel-studio/
echo   This CMD window shows the running server logs.
echo   Press Ctrl+C to stop the server when analysis is complete.
echo   Closing the browser does not automatically stop the backend.
echo ============================================================
echo.
python -u app.py
set "backend_exit=%errorlevel%"
echo.
if not "%backend_exit%"=="0" (
  echo [ERROR] Backend exited with code %backend_exit%.
  echo         Check Python dependencies and whether port 8765 is busy.
) else (
  echo [INFO] ContentMachine backend stopped.
)
echo You may now close this CMD window.
exit /b %backend_exit%
