# Run from your Windows PowerShell machine after checking out the
# integration/full-pipeline-to-pie-audit-20261029 branch.
# Never runs GitHub Actions, paid provider calls, or automatic production migrations.
[CmdletBinding()]
param(
  [switch]$WithTests,
  [switch]$WithDisposablePostgres
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONDONTWRITEBYTECODE = '1'
Write-Host '[1/4] Compile Python source locally'
& python -m compileall -q app.py intelligence scripts
if ($LASTEXITCODE -ne 0) { throw 'Python syntax/compile failed' }

Write-Host '[2/4] Dependency imports and immutable contracts'
& python -m unittest discover -s tests -p 'test_uae_modern_sre_qdp_bridges.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Modern SRE/QDP/UAE contract tests failed' }

Write-Host '[3/4] Read-only end-to-end source readiness audit'
$args = @('-m','scripts.pipeline_to_pie_preflight')
if ($WithTests) { $args += '--contract-tests' }
if ($WithDisposablePostgres) {
  if ($env:CM_TEST_DISPOSABLE_DB -ne 'YES' -or [string]::IsNullOrWhiteSpace($env:CM_TEST_PG_DSN)) {
    throw 'Set CM_TEST_DISPOSABLE_DB=YES and CM_TEST_PG_DSN to an EMPTY DISPOSABLE PG17 DB before running --database'
  }
  $args += '--database'
}
& python @args
$preflight = $LASTEXITCODE
Write-Host ('Preflight exit code: ' + $preflight)
Write-Host 'A nonzero status is EXPECTED until UAE effective approval and PIE live certification are independently implemented and verified.'

Write-Host '[4/4] NEXT: review docs/FULL_PIPELINE_TO_PIE_CODE_FIXES_20261029.md'
Write-Host 'NO production migration, no paid Opus, no fake qualified UAE/PIE output was executed.'
if ($preflight -ne 0) {
  Write-Warning 'Production end-to-end pipeline to PIE is NOT certified.'
} else {
  Write-Warning 'The preflight never certifies real pipeline success. Use the separate live E2E protocol.'
}
