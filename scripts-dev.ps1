param(
  [int]$Port = 2026
)

$entry = Join-Path $PSScriptRoot "scripts/dev/api.ps1"
& $entry -Port $Port
exit $LASTEXITCODE

