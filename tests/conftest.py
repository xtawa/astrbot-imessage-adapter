"""Tests import AstrBot itself; only Photon network operations are mocked."""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if source := os.environ.get("ASTRBOT_SOURCE"):
    sys.path.insert(0, source)
os.environ["ASTRBOT_DISABLE_METRICS"] = "1"
_runtime = tempfile.TemporaryDirectory(prefix="astrbot-imessage-tests-")
os.environ["ASTRBOT_ROOT"] = _runtime.name
spec = importlib.util.spec_from_file_location(
    "photon_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
module = importlib.util.module_from_spec(spec)
sys.modules["photon_plugin"] = module
spec.loader.exec_module(module)

from photon_plugin.imessage.config import Config  # noqa: E402
from photon_plugin.imessage.media import MediaStore  # noqa: E402


@pytest.fixture
def config():
    return Config.parse(
        {
            "id": "test_imessage",
            "project_id": "test-project",
            "project_secret": "test-secret",
            "mark_read": False,
        }
    )


@pytest.fixture
def media(tmp_path):
    return MediaStore(tmp_path / "media", 1024 * 1024, 24, 10 * 1024 * 1024)


@pytest.fixture
def inbound():
    return {
        "id": "message-1",
        "chat_id": "any;-;+15550000001",
        "phone": "+15550000002",
        "kind": "dm",
        "sender_id": "+15550000001",
        "direction": "inbound",
        "timestamp": 1700000000,
        "parts": [{"type": "text", "text": "你好"}],
    }
