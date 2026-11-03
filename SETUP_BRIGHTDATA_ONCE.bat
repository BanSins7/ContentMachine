@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo ContentMachine / Bright Data - first-time local setup
python -m intelligence.market_intelligence.brightdata_setup
echo.
pause
