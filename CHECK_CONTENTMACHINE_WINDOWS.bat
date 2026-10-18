@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo.
echo ===== ContentMachine local diagnostic =====
echo.
echo This Git checkout: %CD%
git branch --show-current 2>nul
git rev-parse --short HEAD 2>nul
echo.
echo Auto-start checkout: %LOCALAPPDATA%\ContentMachine
if exist "%LOCALAPPDATA%\ContentMachine\.git" (
    git -C "%LOCALAPPDATA%\ContentMachine" rev-parse --short HEAD 2>nul
) else (
    echo No checkout found at default auto-start location.
)
echo.
echo PostgreSQL container (read-only status):
docker ps --filter "name=contentmachine-postgres" --format "{{.Names}} : {{.Status}}" 2>nul
echo.
echo This checkout local PostgreSQL health:
python -m intelligence.health
echo.
echo Running local ContentMachine /api/state and /api/intelligence/health:
python -c "import json,urllib.request; endpoints=['/api/state','/api/intelligence/health']; [(lambda p: print(p, json.dumps({k:v for k,v in json.load(urllib.request.urlopen('http://127.0.0.1:8765'+p,timeout=3)).items() if k in ('ok','build_revision','enabled','database','schema_version')},ensure_ascii=False)))(p) for p in endpoints]"
if errorlevel 1 echo Web server could not be reached; run RUN_AUTO.bat in the intended checkout.
echo.
echo Do not send your .env or API key. This screen contains no passwords.
pause
