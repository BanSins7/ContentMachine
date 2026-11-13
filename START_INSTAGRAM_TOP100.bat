@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo ContentMachine / Instagram Reels Top-100
echo http://127.0.0.1:8767
where python >nul 2>nul
if errorlevel 1 goto fail
rem Do not rebuild a new UI while an older server still owns the same port.
rem The browser would then call new routes on old Python code and show HTTP 404.
python -c "import os,socket,sys; port=int(os.environ.get('CM_INSTAGRAM_TOP100_PORT','8767')); s=socket.socket(); result=s.connect_ex(('127.0.0.1',port)); s.close(); sys.exit(1 if result==0 else 0)"
if errorlevel 1 (
  echo.
  echo [ERROR] The Instagram Top100 port is already occupied.
  echo Close the previous CMD/PowerShell running START_INSTAGRAM_TOP100.bat.
  echo In PowerShell, check: Get-NetTCPConnection -LocalPort 8767 -State Listen
  echo Restart this script only after stopping that older server.
  pause
  exit /b 1
)
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
