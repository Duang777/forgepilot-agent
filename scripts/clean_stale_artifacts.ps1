param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pyinstallerRootResolve = Resolve-Path (Join-Path $repoRoot ".build/pyinstaller") -ErrorAction SilentlyContinue
$pyinstallerRoot = if ($pyinstallerRootResolve) { $pyinstallerRootResolve.Path } else { $null }
if (-not $pyinstallerRoot) {
    Write-Host "[ForgePilot] No .build/pyinstaller directory found. Nothing to clean."
    exit 0
}

$targets = @(
    (Join-Path $repoRoot ".build/pyinstaller/dist/workany-api-temp.exe"),
    (Join-Path $repoRoot ".build/pyinstaller/spec/workany-api-temp.spec"),
    (Join-Path $repoRoot ".build/pyinstaller/work/workany-api-temp")
)

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target)) {
        continue
    }

    $resolved = (Resolve-Path -LiteralPath $target).Path
    if (-not $resolved.StartsWith($pyinstallerRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "[ForgePilot] Refusing to clean outside pyinstaller root: $resolved"
    }

    if ($WhatIf) {
        Write-Host "[ForgePilot] Would remove: $resolved"
        continue
    }

    Remove-Item -LiteralPath $resolved -Recurse -Force
    Write-Host "[ForgePilot] Removed stale artifact: $resolved"
}

Write-Host "[ForgePilot] Stale artifact cleanup complete."
