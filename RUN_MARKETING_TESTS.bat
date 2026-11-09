@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_MARKETING_TESTS.ps1" %*
exit /b %ERRORLEVEL%
