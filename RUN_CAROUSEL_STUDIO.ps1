# Content Machine Carousel Studio launcher for Windows PowerShell 5.1 and PowerShell 7.
# ASCII-only by design: Windows PowerShell 5.1 reads UTF-8 without BOM as ANSI.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw 'Python was not found. Install Python 3.11 or later and add it to PATH.'
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw 'Node.js/npm were not found. Install Node.js 22 or later.'
}

Write-Host 'Building React / TypeScript Carousel Studio...'
Push-Location (Join-Path $root 'carousel-studio')
try {
  & npm.cmd install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw 'React / TypeScript build failed.' }
} finally {
  Pop-Location
}

Write-Host 'Checking gallery-dl and Pillow...'
& python -c 'import PIL, gallery_dl'
if ($LASTEXITCODE -ne 0) {
  & python -m pip install Pillow gallery-dl
  if ($LASTEXITCODE -ne 0) { throw 'Python carousel dependencies could not be installed.' }
}

$up = $false
try {
  $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/studio/readiness' -TimeoutSec 3 -UseBasicParsing
  $up = $response.StatusCode -eq 200
} catch {
  $up = $false
}

if ($up) {
  $caps = $response.Content | ConvertFrom-Json
  if ($caps.max_carousels_supported -ne $true -or $caps.account_master_supported -ne $true -or $caps.safe_resume_revision -ne 2 -or $caps.retry_failed_supported -ne $true) {
    throw 'Old Python app.py backend is still running on port 8765. Stop its process and rerun RUN_CAROUSEL_STUDIO.ps1. Existing SQLite data will remain intact.'
  }
  $expectedRoot = [IO.Path]::GetFullPath($root).TrimEnd('\')
  $reportedRoot = [string]$caps.server_worktree_root
  if ([string]::IsNullOrWhiteSpace($reportedRoot)) {
    throw 'Backend did not report its root folder. Stop the old python app.py server and rerun.'
  }
  $actualRoot = [IO.Path]::GetFullPath($reportedRoot).TrimEnd('\')
  if ($actualRoot -ine $expectedRoot) {
    throw 'Port 8765 is running ContentMachine from a DIFFERENT worktree. Stop that python app.py server before opening this Studio; otherwise account history may appear empty.'
  }
}

if (-not $up) {
  $busy = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
  if ($busy) {
    throw 'Port 8765 is used by an old server. Stop the previous python app.py process and rerun.'
  }
  Write-Host 'Starting local Content Machine backend...'
  $env:CM_NO_BROWSER = '1'
  Start-Process -FilePath 'python' -ArgumentList 'app.py' -WorkingDirectory $root
  Start-Sleep -Seconds 3
}

$studioUrl = 'http://127.0.0.1:8765/carousel-studio/'
Write-Host ('Carousel Studio: ' + $studioUrl)
Write-Host ('Results: ' + (Join-Path $root 'marketing-qa\carousel-studio'))
Start-Process $studioUrl
