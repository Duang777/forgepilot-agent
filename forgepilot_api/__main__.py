from __future__ import annotations

import uvicorn

from forgepilot_api.config import API_HOST, API_PORT


def main() -> None:
    uvicorn.run("forgepilot_api.app:app", host=API_HOST, port=API_PORT, reload=False)


if __name__ == "__main__":
    main()


