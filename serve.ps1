# T9FOX — same as t9fox serve; run from repo root (double-click or: powershell -File serve.ps1)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONPATH = (Join-Path $PSScriptRoot "src")

Write-Host ""
Write-Host "T9FOX — starting server with /api (not plain http.server)"
Write-Host "Open: http://127.0.0.1:8765/"
Write-Host "Health: http://127.0.0.1:8765/api/health"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

$cliArgs = @("serve") + @($args)
& py -3 -m t9fox.cli @cliArgs
