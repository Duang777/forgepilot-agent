param(
  [string]$ApiHost = "127.0.0.1",
  [int]$ApiPort = 2026,
  [string]$FrontendShellDir = "",
  [bool]$RunSmokeCheck = $true,
  [bool]$RequireModel = $true
)

$entry = Join-Path $PSScriptRoot "scripts/dev/desktop.ps1"
& $entry `
  -ApiHost $ApiHost `
  -ApiPort $ApiPort `
  -FrontendShellDir $FrontendShellDir `
  -RunSmokeCheck $RunSmokeCheck `
  -RequireModel $RequireModel
exit $LASTEXITCODE
