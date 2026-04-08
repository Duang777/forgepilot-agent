param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [string]$BinaryName = "forgepilot-agent-api",
    [switch]$SkipLint,
    [switch]$SkipTests,
    [switch]$SkipCargoCheck,
    [switch]$SkipSidecarBuild,
    [switch]$SkipArtifactCleanup,
    [switch]$SkipBrandResidueCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolveScript = Join-Path $repoRoot "scripts/resolve_frontend_shell.py"
$frontendShell = (python $resolveScript --repo-root $repoRoot).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "[ForgePilot] Failed to resolve frontend shell directory."
}
if (-not $frontendShell) {
    throw "[ForgePilot] Unable to resolve frontend shell directory."
}
Write-Host "[ForgePilot] Repo root: $repoRoot"
Write-Host "[ForgePilot] Frontend shell: $frontendShell"

Push-Location $repoRoot
try {
    if (-not $SkipArtifactCleanup) {
        Write-Host "[ForgePilot] Cleaning stale build artifacts..."
        & (Join-Path $repoRoot "scripts/clean_stale_artifacts.ps1")
    }

    if (-not $SkipBrandResidueCheck) {
        & (Join-Path $repoRoot "scripts/check_brand_residue.ps1")
        if ($LASTEXITCODE -ne 0) {
            throw "[ForgePilot] Brand residue scan failed."
        }
    }

    if (-not $SkipLint) {
        Write-Host "[ForgePilot] Running Ruff lint checks..."
        python -m ruff check .
        if ($LASTEXITCODE -ne 0) {
            throw "[ForgePilot] Ruff lint check failed."
        }
    }

    if (-not $SkipTests) {
        Write-Host "[ForgePilot] Running full pytest suite..."
        python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "[ForgePilot] Pytest suite failed."
        }
    }

    if (-not $SkipCargoCheck) {
        Write-Host "[ForgePilot] Running Tauri cargo check..."
        Push-Location (Join-Path $frontendShell "src-tauri")
        try {
            cargo check
            if ($LASTEXITCODE -ne 0) {
                throw "[ForgePilot] cargo check failed."
            }
        }
        finally {
            Pop-Location
        }
    }

    if (-not $SkipSidecarBuild) {
        Write-Host "[ForgePilot] Building Python sidecar..."
        python scripts/build_python_sidecar.py --repo-root . --target-triple $TargetTriple --binary-name $BinaryName
        if ($LASTEXITCODE -ne 0) {
            throw "[ForgePilot] Python sidecar build failed."
        }
    }

    Write-Host "[ForgePilot] Generating sidecar checksums..."
    $pattern = "$BinaryName-$TargetTriple*"
    python scripts/write_sidecar_checksums.py --repo-root . --pattern $pattern --output ".build/verify/sidecar-sha256-$TargetTriple.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "[ForgePilot] Sidecar checksum generation failed."
    }

    Write-Host "[ForgePilot] Verification completed."
}
finally {
    Pop-Location
}
