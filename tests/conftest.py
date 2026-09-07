import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real settings folder during tests."""
    monkeypatch.setenv("THEMOVER_HOME", str(tmp_path / "home"))
    yield
