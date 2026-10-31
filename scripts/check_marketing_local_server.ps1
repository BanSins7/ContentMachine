# Preflight for START_MARKETING_QA.bat. Never kill a running process.
# The production-style auto updater may already serve a different checkout
# on port 8765; starting another app.py cannot replace it.
$ErrorActionPreference = 'Stop'
$connections = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
if ($connections.Count -eq 0) {
    Write-Host '[PASS] Port 8765 is free.'
    exit 0
}
$serverPid = [int]$connections[0].OwningProcess
$owner = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
$ownerPath = if ($null -ne $owner) { $owner.ExecutablePath } else { 'unknown' }
Write-Host "[BLOCKED] Port 8765 is already served by PID $serverPid."
Write-Host "[INFO] Executable: $ownerPath"
try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/market-intelligence.html' -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    if ($response.StatusCode -eq 200 -and
        $response.Headers['Content-Type'] -match 'text/html' -and
        $response.Content -match 'MARKET INTELLIGENCE') {
        Write-Host '[INFO] Existing server already has a Marketing page; its Git checkout is NOT verified.'
    } else {
        Write-Host '[INFO] Existing server is not the verified Marketing UI.'
    }
} catch {
    Write-Host '[INFO] Existing server did not serve the Marketing HTML page.'
}
Write-Host '[NEXT] Close the old ContentMachine / RUN_AUTO window first.'
Write-Host "[NEXT] If needed, inspect the PID and stop that specific process manually:"
Write-Host "       Get-CimInstance Win32_Process -Filter 'ProcessId = $serverPid' | Select-Object ProcessId,ExecutablePath,CommandLine"
Write-Host "       Stop-Process -Id $serverPid"
Write-Host '[NEXT] Then run START_MARKETING_QA.bat again. Nothing was stopped automatically.'
exit 2
