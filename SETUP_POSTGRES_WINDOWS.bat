@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo === ContentMachine: PostgreSQL infrastructure setup ===
echo.
where docker >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not installed or not in PATH.
  echo Install Docker Desktop for Windows, enable WSL2 backend, start Docker, then rerun.
  pause
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not running. Start it first.
  pause
  exit /b 1
)
python -m intelligence.bootstrap
if errorlevel 1 goto fail
echo [SETUP] Starting local PostgreSQL on 127.0.0.1:54329...
docker compose -f docker-compose.yml up -d --wait
if errorlevel 1 goto fail
echo [SETUP] Installing database driver...
python -m pip install -r intelligence\requirements.txt
if errorlevel 1 goto fail
echo [SETUP] Applying versioned schema migrations...
python -m intelligence.migrate
if errorlevel 1 goto fail
echo.
echo [OK] PostgreSQL is ready. ContentMachine remains at http://127.0.0.1:8765
python -m intelligence.health
echo.
pause
exit /b 0
:fail
echo.
echo [ERROR] PostgreSQL setup did not complete. See messages above.
pause
exit /b 1
