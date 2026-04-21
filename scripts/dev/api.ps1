param(
  [int]$Port = 2026
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$entry = Join-Path $repoRoot "scripts/dev.py"
python $entry api --host 127.0.0.1 --port $Port
