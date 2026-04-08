param(
  [string]$ApiHost = "127.0.0.1",
  [int]$ApiPort = 2026,
  [string]$FrontendShellDir = "",
  [bool]$RunSmokeCheck = $true,
  [bool]$RequireModel = $true
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$resolveScript = Join-Path $repoRoot "scripts/resolve_frontend_shell.py"
$frontendShellPath = if ($FrontendShellDir) {
  Join-Path $repoRoot $FrontendShellDir
} else {
  (python $resolveScript --repo-root $repoRoot).Trim()
}
$apiUrl = "http://${ApiHost}:${ApiPort}"

if (-not (Test-Path $frontendShellPath)) {
  throw "Frontend shell path not found: $frontendShellPath"
}

Write-Host "[ForgePilot] Starting Python API on $apiUrl ..."
$apiProc = Start-Process -FilePath "python" `
  -ArgumentList "-m", "uvicorn", "forgepilot_api.app:app", "--host", $ApiHost, "--port", "$ApiPort" `
  -WorkingDirectory $repoRoot `
  -PassThru

try {
  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
      $resp = Invoke-WebRequest -UseBasicParsing "$apiUrl/health" -TimeoutSec 2
      if ($resp.StatusCode -eq 200) {
        $ready = $true
        break
      }
    } catch {
      # keep waiting
    }
  }

  if (-not $ready) {
    throw "API health check failed: $apiUrl/health"
  }

  if ($RunSmokeCheck) {
    Write-Host "[ForgePilot] Running API smoke check ..."
    $smokeArgs = @(
      "scripts/smoke_api_chain.py",
      "--base-url",
      $apiUrl,
      "--timeout-sec",
      "120",
      "--require-plan"
    )
    if ($RequireModel) {
      $smokeArgs += "--require-model"
    }
    python @smokeArgs
  }

  Write-Host "[ForgePilot] API is healthy. Launching Tauri dev ..."
  $env:VITE_API_BASE_URL = $apiUrl
  Push-Location $frontendShellPath
  try {
    pnpm tauri dev
  } finally {
    Pop-Location
  }
} finally {
  if ($apiProc -and -not $apiProc.HasExited) {
    Write-Host "[ForgePilot] Stopping Python API (PID $($apiProc.Id)) ..."
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
  }
}
