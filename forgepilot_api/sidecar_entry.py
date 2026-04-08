from __future__ import annotations

import uvicorn

from forgepilot_api.config import API_HOST, API_PORT


def run_sidecar() -> None:
    uvicorn.run("forgepilot_api.app:app", host=API_HOST, port=API_PORT, reload=False, workers=1)


if __name__ == "__main__":
    run_sidecar()

