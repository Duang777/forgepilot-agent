param(
    [bool]$FailOnHit = $true
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$args = @(
    "scripts/scan_brand_residue.py",
    "--repo-root", $repoRoot,
    "--output", ".build/verify/brand-residue-report.json"
)
if ($FailOnHit) {
    $args += "--fail-on-hit"
}

Write-Host "[ForgePilot] Running brand residue scan..."
python @args
