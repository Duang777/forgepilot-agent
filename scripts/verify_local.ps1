param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [string]$BinaryName = "forgepilot-agent-api",
    [switch]$SkipLint,
    [switch]$SkipTypecheck,
    [switch]$SkipTests,
    [switch]$SkipCargoCheck,
    [switch]$SkipSidecarBuild,
    [switch]$SkipArtifactCleanup,
    [switch]$SkipBrandResidueCheck,
    [switch]$SkipChecksum
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$entry = Join-Path $repoRoot "scripts/dev.py"
$verifyArgs = @(
    $entry,
    "verify",
    "--target-triple", $TargetTriple,
    "--binary-name", $BinaryName
)
if ($SkipLint) { $verifyArgs += "--skip-lint" }
if ($SkipTypecheck) { $verifyArgs += "--skip-typecheck" }
if ($SkipTests) { $verifyArgs += "--skip-tests" }
if ($SkipCargoCheck) { $verifyArgs += "--skip-cargo-check" }
if ($SkipSidecarBuild) { $verifyArgs += "--skip-sidecar-build" }
if ($SkipArtifactCleanup) { $verifyArgs += "--skip-artifact-cleanup" }
if ($SkipBrandResidueCheck) { $verifyArgs += "--skip-brand-residue-check" }
if ($SkipChecksum) { $verifyArgs += "--skip-checksum" }

Push-Location $repoRoot
try {
    python @verifyArgs
}
finally {
    Pop-Location
}
