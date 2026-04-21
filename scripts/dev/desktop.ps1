param(
  [string]$ApiHost = "127.0.0.1",
  [int]$ApiPort = 2026,
  [string]$FrontendShellDir = "",
  [bool]$RunSmokeCheck = $true,
  [bool]$RequireModel = $true,
  [bool]$StopApiOnExit = $false
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$entry = Join-Path $repoRoot "scripts/dev.py"
$args = @(
  $entry,
  "desktop",
  "--api-host", $ApiHost,
  "--api-port", "$ApiPort"
)
if ($FrontendShellDir) {
  $args += @("--frontend-shell-dir", $FrontendShellDir)
}
if (-not $RunSmokeCheck) {
  $args += "--no-smoke-check"
}
if (-not $RequireModel) {
  $args += "--no-require-model"
}
if ($StopApiOnExit) {
  $args += "--stop-api-on-exit"
}
python @args
