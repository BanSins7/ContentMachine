# Dedicated Windows launcher for the TikTok Carousel / Market Intelligence Studio.
# ASCII source for compatibility with Windows PowerShell 5.1 (UTF-8 without BOM).
# Opens the backend in a visible CMD, and the UI in an Edge/Chrome app window.
# Paid collection/LLM calls are initiated ONLY by a user action in the Studio.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root
$baseUrl = 'http://127.0.0.1:8765'
$studioUrl = $baseUrl + '/carousel-studio/'

function Get-StudioReadiness {
  try {
    return Invoke-RestMethod -Method Get -Uri ($baseUrl + '/api/studio/readiness') -TimeoutSec 3
  } catch {
    return $null
  }
}

function Test-StudioPortBusy {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $connection = $client.ConnectAsync('127.0.0.1', 8765)
    if (-not $connection.Wait(400)) { return $false }
    $connection.GetAwaiter().GetResult()
    return $true
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Assert-StudioBackend($state) {
  if ($null -eq $state) {
    throw 'Backend did not respond with Carousel Studio readiness.'
  }
  $reportedRoot = [string]$state.server_worktree_root
  if ([string]::IsNullOrWhiteSpace($reportedRoot)) {
    throw 'Backend is old or incompatible: no server_worktree_root. Stop it before launching Studio.'
  }
  $expectedRoot = [IO.Path]::GetFullPath($root).TrimEnd('\')
  $actualRoot = [IO.Path]::GetFullPath($reportedRoot).TrimEnd('\')
  if ($actualRoot -ine $expectedRoot) {
    throw ('Port 8765 belongs to another ContentMachine copy: ' + $actualRoot +
           '. Stop that server instead of mixing its local SQLite data with this worktree.')
  }
  if ($state.max_carousels_supported -ne $true -or
      $state.account_master_supported -ne $true -or
      $state.resume_and_dedup_supported -ne $true -or
      $state.safe_resume_revision -ne 2 -or
      $state.retry_failed_supported -ne $true) {
    throw 'An outdated ContentMachine backend is running. Stop it and relaunch this Studio.'
  }
}

function Find-AppBrowser {
  foreach ($name in @('msedge.exe', 'chrome.exe')) {
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found -and $found.Source) { return $found.Source }
  }
  $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
  $bases = @($env:ProgramFiles, $programFilesX86, $env:LOCALAPPDATA)
  foreach ($base in $bases) {
    if ([string]::IsNullOrWhiteSpace($base)) { continue }
    foreach ($relative in @('Microsoft\Edge\Application\msedge.exe',
                            'Google\Chrome\Application\chrome.exe')) {
      $candidate = Join-Path $base $relative
      if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
  }
  return $null
}

Write-Host ''
Write-Host 'CONTENTMACHINE / CAROUSEL ANALYSIS' -ForegroundColor Cyan
Write-Host ('Project folder: ' + $root)
Write-Host ('Studio: ' + $studioUrl)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw 'Python not found. Install Python 3.11+ and add it to PATH.'
}

# Never connect to an old or foreign server, even if it answers HTTP 200.
$existing = Get-StudioReadiness
if ($null -ne $existing) {
  Assert-StudioBackend $existing
} elseif (Test-StudioPortBusy) {
  throw 'Port 8765 is occupied but the Carousel API did not respond. Stop that process first.'
}

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw 'Node.js/npm not found. Install Node.js 22+ to build the Studio.'
}

Write-Host '[1/4] Checking native React Carousel Studio build...'
& python (Join-Path $root 'scripts\ensure_frontend.py') --studio-only --strict
if ($LASTEXITCODE -ne 0 -or
    -not (Test-Path -LiteralPath (Join-Path $root 'site\carousel-studio\index.html') -PathType Leaf)) {
  throw 'Carousel Studio build failed. Check npm and TypeScript output above.'
}

Write-Host '[2/4] Checking Python dependencies...'
& python -c 'import PIL, gallery_dl, psycopg, yt_dlp, faster_whisper, imageio_ffmpeg'
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Installing missing ContentMachine Python dependencies...'
  & python -m pip install -r (Join-Path $root 'worker\requirements.txt') -r (Join-Path $root 'intelligence\requirements.txt')
  if ($LASTEXITCODE -ne 0) {
    throw 'Python dependencies could not be installed.'
  }
  & python -c 'import PIL, gallery_dl, psycopg, yt_dlp, faster_whisper, imageio_ffmpeg'
  if ($LASTEXITCODE -ne 0) {
    throw 'Required Carousel Studio / ContentMachine Python packages are still unavailable.'
  }
}

Write-Host '[3/4] Connecting to local backend...'
$existing = Get-StudioReadiness
if ($null -ne $existing) {
  Assert-StudioBackend $existing
  Write-Host 'Reusing the already running ContentMachine backend; no duplicate worker started.' -ForegroundColor Green
} else {
  if (Test-StudioPortBusy) {
    throw 'Port 8765 became occupied by a non-Studio process. Stop it and retry.'
  }
  $serverScript = Join-Path $root 'scripts\CAROUSEL_BACKEND_CMD.bat'
  if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
    throw 'Missing scripts\CAROUSEL_BACKEND_CMD.bat.'
  }
  Write-Host 'Launching backend in its own visible CMD window...' -ForegroundColor Cyan
  # Relative BAT path intentionally avoids CMD quoting issues with spaces in the
  # repository directory. WorkingDirectory always points to this exact checkout.
  Start-Process -FilePath $env:ComSpec -ArgumentList '/d /k call scripts\CAROUSEL_BACKEND_CMD.bat' -WorkingDirectory $root -WindowStyle Normal

  $ready = $false
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    Start-Sleep -Milliseconds 500
    $existing = Get-StudioReadiness
    if ($null -ne $existing) {
      Assert-StudioBackend $existing
      $ready = $true
      break
    }
  }
  if (-not $ready) {
    throw 'Backend did not start on 127.0.0.1:8765. Read the separate CMD window for the error.'
  }
}

Write-Host '[4/4] Opening independent Studio window...'
$browser = Find-AppBrowser
if ($browser) {
  # An isolated browser profile reliably creates a separate app-style window,
  # even when Edge or Chrome is already running with a normal profile.
  $localData = [Environment]::GetFolderPath('LocalApplicationData')
  $browserProfile = Join-Path $localData 'ContentMachine\CarouselAppProfile'
  New-Item -ItemType Directory -Path $browserProfile -Force | Out-Null
  $arguments = '--no-first-run --window-size=1440,930 --user-data-dir="' +
               $browserProfile + '" --app="' + $studioUrl + '"'
  try {
    Start-Process -FilePath $browser -ArgumentList $arguments -WorkingDirectory $root
    Write-Host ('Opened Carousel Studio as a separate app window using ' + (Split-Path $browser -Leaf)) -ForegroundColor Green
  } catch {
    Write-Host 'App-window launch unavailable; opening with the default browser instead.'
    Start-Process $studioUrl
  }
} else {
  Write-Host 'Edge/Chrome not found; opening a normal browser window.'
  Start-Process $studioUrl
}
Write-Host 'Paste a TikTok carousel/profile link in Studio and press Start Analysis.'
Write-Host 'The launcher does not start paid Bright Data or LLM requests.'
