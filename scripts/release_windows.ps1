param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [string]$BinaryName = "forgepilot-agent-api",
    [switch]$InstallPyInstaller,
    [switch]$SkipTests,
    [switch]$SkipTauri,
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
$distDir = Join-Path $frontendShell "src-api/dist"
$bundleDir = Join-Path $frontendShell "src-tauri/target/$TargetTriple/release/bundle"
$checksumOutDir = Join-Path $repoRoot ".build/release"
$checksumFile = Join-Path $checksumOutDir "checksums-windows-$TargetTriple.txt"

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

    if (-not $SkipTests) {
        Write-Host "[ForgePilot] Running test suite..."
        python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "[ForgePilot] Pytest suite failed."
        }
    }

    Write-Host "[ForgePilot] Building Python sidecar..."
    $buildArgs = @(
        "scripts/build_python_sidecar.py",
        "--repo-root", ".",
        "--target-triple", $TargetTriple,
        "--binary-name", $BinaryName
    )
    if ($InstallPyInstaller) {
        $buildArgs += "--install-pyinstaller"
    }
    python @buildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "[ForgePilot] Python sidecar build failed."
    }

    if (-not $SkipTauri) {
        Write-Host "[ForgePilot] Building Tauri app bundle..."
        Push-Location $frontendShell
        try {
            pnpm tauri build --target $TargetTriple --config src-tauri/tauri.conf.python-sidecar.json
            if ($LASTEXITCODE -ne 0) {
                throw "[ForgePilot] Tauri bundle build failed."
            }
        }
        finally {
            Pop-Location
        }
    }

    New-Item -ItemType Directory -Force -Path $checksumOutDir | Out-Null
    if (Test-Path $checksumFile) {
        Remove-Item -LiteralPath $checksumFile -Force
    }

    $sidecarPattern = "$BinaryName-$TargetTriple*"
    python scripts/write_sidecar_checksums.py --repo-root . --pattern $sidecarPattern --output ".build/release/checksums-windows-$TargetTriple.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "[ForgePilot] Sidecar checksum generation failed."
    }

    if (Test-Path $bundleDir) {
        Write-Host "[ForgePilot] Appending Tauri bundle checksums..."
        $bundleTargets = Get-ChildItem -Path $bundleDir -Recurse -File |
            Where-Object { $_.Extension -in @(".exe", ".msi", ".zip") } |
            Sort-Object FullName -Unique
        foreach ($file in $bundleTargets) {
            $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $relative = $file.FullName.Substring($repoRoot.Length + 1).Replace("\", "/")
            "$hash *$relative" | Out-File -FilePath $checksumFile -Append -Encoding utf8
        }
    }

    Write-Host "[ForgePilot] Release complete."
    Write-Host "[ForgePilot] Checksum file: $checksumFile"
    Get-Content $checksumFile
}
finally {
    Pop-Location
}
