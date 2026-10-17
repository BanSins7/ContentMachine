@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "CZ_LANGUAGE=ru"
set "CZ_WHISPER_MODEL=small"
python -u auto_update.py
if errorlevel 1 (
  echo.
  echo [ERROR] ContentMachine could not start.
  pause
  exit /b 1
)