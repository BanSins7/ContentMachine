@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
title ContentMachine - Carousel Analysis Launcher
echo.
echo [CONTENTMACHINE] Launching separate Carousel Analysis...
echo.
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell is not installed.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_CAROUSEL_STUDIO.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Carousel Analysis could not start. See the message above.
  pause
  exit /b 1
)
exit /b 0
