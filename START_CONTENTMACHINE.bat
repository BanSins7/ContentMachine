@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo       CONTENTMACHINE  /  LOCAL STUDIO
echo       http://127.0.0.1:8765
echo.
python --version
if errorlevel 1 goto fail
python -c "import yt_dlp, faster_whisper, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Installing dependencies... This may take several minutes.
    python -m pip install -r "worker\requirements.txt"
    if errorlevel 1 goto fail
)
set "CZ_LANGUAGE=ru"
set "CZ_WHISPER_MODEL=small"
python -u app.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo [ERROR] Something failed. See messages above.
pause
exit /b 1
