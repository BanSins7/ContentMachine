@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo ===== ContentMachine / RTB Hierarchical Release Check =====
echo [INFO] Safe offline/static verification. NO OpenRouter or Apify paid requests.
echo [INFO] Current branch and revision:
git branch --show-current
git rev-parse --short=12 HEAD
echo.
where python >nul 2>nul
if errorlevel 1 goto no_python
echo [1/4] Python syntax check...
python -m compileall -q app.py intelligence
if errorlevel 1 goto fail
echo [2/4] Hierarchical taxonomy contracts...
python -m unittest discover -s tests -p "test_taxonomy_deep_contracts.py" -v
if errorlevel 1 goto fail
echo [3/4] Existing RTB and SRE contract tests...
python -m unittest discover -s tests -p "test_taxonomy_builder_pipeline.py" -v
if errorlevel 1 goto fail
echo [4/4] Optional JS checks if Node.js is installed...
where node >nul 2>nul
if errorlevel 1 goto no_node
node --check site/taxonomy-deep.js
if errorlevel 1 goto fail
node --check site/taxonomy-run.js
if errorlevel 1 goto fail
node tests/test_taxonomy_deep_ui.cjs
if errorlevel 1 goto fail
node tests/test_taxonomy_run_ui.cjs
if errorlevel 1 goto fail
echo.
echo [OK] Offline RTB release checks passed (excluding disposable PostgreSQL E2E).
echo [INFO] PostgreSQL E2E uses a disposable CM_TEST_PG_DSN configured ONLY by CI.
exit /b 0
:no_node
echo [WARN] Node.js not found; JS tests SKIPPED. Install Node.js to complete UI QA.
echo [WARN] This is NOT a full release pass.
exit /b 2
:no_python
echo [ERROR] Python is not available.
pause
exit /b 1
:fail
echo.
echo [FAIL] RTB release checks failed. Do not merge or make paid requests.
pause
exit /b 1
