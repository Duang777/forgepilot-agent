param(
  [ValidateSet("api", "desktop", "verify", "smoke")]
  [string]$Task = "api",
  [int]$Port = 2026,
  [string]$ApiHost = "127.0.0.1",
  [switch]$NoRequireModel
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$requireModel = -not $NoRequireModel.IsPresent

switch ($Task) {
  "api" {
    & (Join-Path $repoRoot "scripts/dev/api.ps1") -Port $Port
  }
  "desktop" {
    & (Join-Path $repoRoot "scripts/dev/desktop.ps1") `
      -ApiHost $ApiHost `
      -ApiPort $Port `
      -RequireModel $requireModel
  }
  "verify" {
    & (Join-Path $repoRoot "scripts/verify_local.ps1")
  }
  "smoke" {
    $smokeArgs = @(
      (Join-Path $repoRoot "scripts/smoke_api_chain.py"),
      "--base-url",
      ("http://{0}:{1}" -f $ApiHost, $Port),
      "--require-plan"
    )
    if ($requireModel) {
      $smokeArgs += "--require-model"
    }
    python @smokeArgs
  }
}

exit $LASTEXITCODE
