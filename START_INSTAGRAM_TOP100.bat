@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo ContentMachine / Instagram Reels Top-100
echo http://127.0.0.1:8767
where python >nul 2>nul
if errorlevel 1 goto fail
python -c "import yt_dlp, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
  python -m pip install -r "worker\requirements.txt"
  if errorlevel 1 goto fail
)
python -c "import PIL, psycopg" >nul 2>nul
if errorlevel 1 (
  python -m pip install -r "intelligence\requirements.txt"
  if errorlevel 1 goto fail
)
where npm >nul 2>nul
if errorlevel 1 goto fail
pushd instagram-top100-ui
if not exist node_modules (
  call npm install
  if errorlevel 1 (popd & goto fail)
)
call npm run build
if errorlevel 1 (popd & goto fail)
popd
python -m intelligence.instagram_top100.server
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo [ERROR] Python, Node/npm, dependencies, or server failed.
pause
exit /b 1
