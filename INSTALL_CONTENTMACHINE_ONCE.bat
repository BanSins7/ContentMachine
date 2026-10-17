@echo off
setlocal EnableExtensions
set "DEST=%LOCALAPPDATA%\ContentMachine"
set "REPO=https://github.com/BanSins7/ContentMachine.git"
echo.
echo === ContentMachine - first-time installation ===
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found. Install Python 3.10+ and try again.
  pause
  exit /b 1
)
where git >nul 2>nul
if errorlevel 1 (
  if exist "C:\Program Files\Git\cmd\git.exe" set "PATH=C:\Program Files\Git\cmd;%PATH%"
)
where git >nul 2>nul
if errorlevel 1 (
  echo [SETUP] Git is required. Trying to install it with winget...
  where winget >nul 2>nul
  if errorlevel 1 goto no_git
  winget install --id Git.Git --exact --source winget --accept-package-agreements --accept-source-agreements
  if exist "C:\Program Files\Git\cmd\git.exe" set "PATH=C:\Program Files\Git\cmd;%PATH%"
  where git >nul 2>nul
  if errorlevel 1 goto no_git
)
if exist "%DEST%\.git" goto clone_done
if exist "%DEST%" (
  echo [ERROR] Target folder exists, but is not a Git checkout: %DEST%
  echo Rename this folder first and rerun the installer.
  pause
  exit /b 1
)
echo [SETUP] Cloning private GitHub repository...
echo You may need to sign in to GitHub once in a browser.
git clone --branch main "%REPO%" "%DEST%"
if errorlevel 1 goto fail
:clone_done
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if not exist "%STARTUP%" mkdir "%STARTUP%"
> "%STARTUP%\ContentMachine_AutoUpdate.bat" echo @echo off
>> "%STARTUP%\ContentMachine_AutoUpdate.bat" echo call "%%LOCALAPPDATA%%\ContentMachine\RUN_AUTO.bat"
echo [OK] Startup enabled; ContentMachine will launch after Windows sign-in.
echo.
echo [START] Opening ContentMachine...
call "%DEST%\RUN_AUTO.bat"
if errorlevel 1 goto fail
exit /b 0
:no_git
echo [ERROR] Install Git from https://git-scm.com/download/win and rerun this installer.
pause
exit /b 1
:fail
echo.
echo [ERROR] Installation or server launch failed. See messages above.
pause
exit /b 1