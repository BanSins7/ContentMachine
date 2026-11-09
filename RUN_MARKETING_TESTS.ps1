# Run Marketing A/B/C tests from the repository root on Windows.
# Example: .\RUN_MARKETING_TESTS.ps1 -Full -Video "D:\Videos\sample.mp4"
param(
    [switch]$Full,
    [switch]$WithDatabase,
    [string]$Video,
    [switch]$InstallDependencies
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonExe = 'python'
    $pythonPrefix = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -c 'import sys; assert sys.version_info >= (3,11)'
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = 'py'
            $pythonPrefix = @('-3')
        }
    }
    if ($pythonExe -eq 'python') {
        & python -c 'import sys; assert sys.version_info >= (3,11)'
        if ($LASTEXITCODE -ne 0) {
            throw 'Python 3.11+ is required. Run: python --version'
        }
    }
    if ($InstallDependencies) {
        Write-Host '[SETUP] Installing Python dependencies for the local checks...'
        & $pythonExe @pythonPrefix -m pip install -r intelligence/requirements.txt
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not install intelligence/requirements.txt'
        }
    }
    $arguments = @('scripts/run_market_tests.py')
    if ($Full) { $arguments += '--full' }
    if ($WithDatabase) { $arguments += '--db' }
    if ($Video) {
        if (!(Test-Path -LiteralPath $Video -PathType Leaf)) {
            throw "Video file does not exist: $Video"
        }
        $arguments += @('--video', (Resolve-Path -LiteralPath $Video).Path)
    }
    Write-Host '[START] Marketing Group A + B + C tests (offline, no paid APIs)'
    & $pythonExe @pythonPrefix @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
