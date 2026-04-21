param(
  [ValidateSet("api", "desktop", "verify", "smoke")]
  [string]$Task = "api",
  [int]$Port = 2026,
  [string]$ApiHost = "127.0.0.1",
  [switch]$NoRequireModel
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$entry = Join-Path $repoRoot "scripts/dev.py"
$requireModel = -not $NoRequireModel.IsPresent

switch ($Task) {
  "api" {
    python $entry api --host $ApiHost --port $Port
  }
  "desktop" {
    $desktopArgs = @(
      $entry,
      "desktop",
      "--api-host", $ApiHost,
      "--api-port", "$Port"
    )
    if (-not $requireModel) {
      $desktopArgs += "--no-require-model"
    }
    python @desktopArgs
  }
  "verify" {
    python $entry verify
  }
  "smoke" {
    $smokeArgs = @(
      $entry,
      "smoke",
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
