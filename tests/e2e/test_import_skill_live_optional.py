from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgepilot_api.main import app

LIVE_ENV = "FORGEPILOT_RUN_LIVE_NET_TESTS"
LIVE_URL = "https://github.com/geekjourneyx/md2wechat-skill/tree/main/skills/md2wechat"

pytestmark = pytest.mark.skipif(
    os.getenv(LIVE_ENV) != "1",
    reason=f"Set {LIVE_ENV}=1 to run live GitHub integration tests.",
)


@pytest.mark.live_network
def test_import_skill_live_branch_and_path() -> None:
    target_root = Path(tempfile.mkdtemp(prefix="forgepilot-live-import-"))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/files/import-skill",
                json={
                    "url": LIVE_URL,
                    "targetDir": str(target_root),
                    "branch": "main",
                    "path": "skills/md2wechat",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["count"] == 1
        assert payload["source"]["branch"] == "main"
        assert payload["source"]["path"] == "skills/md2wechat"

        imported = payload["imported"]
        assert isinstance(imported, list)
        assert len(imported) == 1

        imported_path = Path(imported[0]["path"])
        assert imported_path.exists()
        assert imported_path.is_dir()
        assert (imported_path / "SKILL.md").exists()
    finally:
        shutil.rmtree(target_root, ignore_errors=True)
