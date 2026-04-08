param(
  [int]$Port = 2026
)

$env:PORT = "$Port"
python -m uvicorn forgepilot_api.app:app --host 127.0.0.1 --port $Port --reload
