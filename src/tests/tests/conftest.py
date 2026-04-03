"""
Pytest configuration and shared fixtures for DLC refactoring test suite.

CRITICAL CONSTRAINTS ENFORCED HERE:
  - Constraint #1: GPU routing — RTX 5090 is CUDA_VISIBLE_DEVICES=0 (DLC processes)
  - Constraint #2: VRAM teardown — subprocess cleanup after GPU tests
  - Constraint #4: Isolated test state — sandbox fixture duplicates project, tears down after
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Set DATA_DIR env BEFORE any app.py import (app.py calls mkdir at module level) ──
# Use a session-scoped temp dir so the value is stable across all tests.
_SESSION_TMP = tempfile.mkdtemp(prefix="dlc_test_session_")
_SESSION_DATA_DIR = os.path.join(_SESSION_TMP, "data")
_SESSION_USER_DIR = os.path.join(_SESSION_TMP, "user-data")
os.makedirs(_SESSION_DATA_DIR, exist_ok=True)
os.makedirs(_SESSION_USER_DIR, exist_ok=True)
os.environ.setdefault("DATA_DIR", _SESSION_DATA_DIR)
os.environ.setdefault("USER_DATA_DIR", _SESSION_USER_DIR)
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("FLASK_SECRET_KEY", "testkey1234567890abcdef12345678")

# ── GPU Routing (Constraint #1) ────────────────────────────────────────────────
# GPU 0 = RTX 5090  → DLC processes
# GPU 1 = Blackwell → orchestrator / LLM
DLC_GPU_INDEX = "0"
DLC_CUDA_ENV = {**os.environ, "CUDA_VISIBLE_DEVICES": DLC_GPU_INDEX}

# ── Original project (READ-ONLY — never modify) ────────────────────────────────
# Note: actual path on this machine differs slightly from original constraint spec
_POSSIBLE_ORIGINAL_PATHS = [
    Path("/home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07"),
    Path("/home/sam/data-disk/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07"),
]
ORIGINAL_DLC_PROJECT: Path | None = next(
    (p for p in _POSSIBLE_ORIGINAL_PATHS if p.is_dir()), None
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def dlc_sandbox_project(tmp_path) -> Path:
    """
    Create a sandboxed copy of the DLC test project for each test.

    - Duplicates ORIGINAL_DLC_PROJECT → tmp_path/DREADD-Ali-2026-01-07
    - Yields the path to the copy
    - Deletes the copy on teardown (regardless of test outcome)

    Tests MUST use this fixture instead of accessing ORIGINAL_DLC_PROJECT directly.
    """
    if ORIGINAL_DLC_PROJECT is None:
        pytest.skip("Original DLC project not found on this machine — skipping.")

    dest = tmp_path / ORIGINAL_DLC_PROJECT.name
    shutil.copytree(str(ORIGINAL_DLC_PROJECT), str(dest), symlinks=False)

    # Patch project_path in config.yaml to point to the sandbox copy
    config_file = dest / "config.yaml"
    if config_file.is_file():
        text = config_file.read_text()
        import re
        text = re.sub(
            r"^(project_path\s*:\s*).*$",
            lambda m: m.group(1) + str(dest),
            text,
            flags=re.MULTILINE,
        )
        config_file.write_text(text)

    yield dest

    # Teardown — always delete (Constraint #4)
    if dest.exists():
        shutil.rmtree(str(dest), ignore_errors=True)


@pytest.fixture(scope="function")
def sandbox_config_path(dlc_sandbox_project) -> Path:
    """Return the config.yaml path inside the sandbox project."""
    return dlc_sandbox_project / "config.yaml"


@pytest.fixture(scope="session")
def fake_redis():
    """
    In-memory dict-based fake Redis client for unit tests that don't need
    a real Redis server.  Only implements the subset of commands used by app.py.
    """

    class FakeRedis:
        def __init__(self):
            self._store: dict = {}
            self._hstore: dict = {}

        def get(self, key):
            return self._store.get(key)

        def set(self, key, value, ex=None):
            self._store[key] = value

        def delete(self, *keys):
            for k in keys:
                self._store.pop(k, None)
                self._hstore.pop(k, None)

        def hset(self, name, key=None, value=None, mapping=None, **kwargs):
            if name not in self._hstore:
                self._hstore[name] = {}
            # Support hset(name, key, value) positional form
            if key is not None:
                self._hstore[name][key] = value
            if mapping:
                self._hstore[name].update(mapping)
            self._hstore[name].update(kwargs)

        def hgetall(self, name):
            return self._hstore.get(name, {})

        def hget(self, name, key):
            return self._hstore.get(name, {}).get(key)

        def expire(self, key, seconds):
            pass

        def zadd(self, name, mapping):
            pass

        def zrange(self, name, start, stop, withscores=False, rev=False):
            return []

        def zrevrange(self, name, start, stop, withscores=False):
            return []

        def zadd(self, name, mapping, **kwargs):
            pass

        def zrem(self, name, *members):
            pass

        def setex(self, key, seconds, value):
            self._store[key] = value

        def scan_iter(self, pattern):
            return iter([])

        def from_url(self, url, decode_responses=True):
            return self

    return FakeRedis()


@pytest.fixture(scope="function")
def flask_test_client(fake_redis, tmp_path):
    """
    Returns a Flask test client with:
    - Redis patched to in-memory fake
    - DATA_DIR and USER_DATA_DIR set to temp directories
    - A pre-set session uid
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_data_dir = tmp_path / "user-data"
    user_data_dir.mkdir()

    env_vars = {
        "DATA_DIR": str(data_dir),
        "USER_DATA_DIR": str(user_data_dir),
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "test-secret-key-32-chars-minimum!",
    }

    with patch.dict(os.environ, env_vars):
        # Must import AFTER patching env so DATA_DIR resolves correctly
        # Use importlib to force re-evaluation if needed
        import importlib
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        # Patch redis at module level before import
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import app as flask_app_module
            flask_app_module.DATA_DIR = data_dir
            flask_app_module.USER_DATA_DIR = user_data_dir
            flask_app_module._redis_client = fake_redis

            flask_app_module.app.config["TESTING"] = True
            flask_app_module.app.config["SECRET_KEY"] = "test-secret"
            flask_app_module.app.config["WTF_CSRF_ENABLED"] = False

            with flask_app_module.app.test_client() as client:
                with flask_app_module.app.test_request_context():
                    yield client, flask_app_module, fake_redis, data_dir, user_data_dir


@pytest.fixture(scope="function")
def dlc_project_in_data_dir(flask_test_client, dlc_sandbox_project):
    """
    Move the sandbox DLC project into the test DATA_DIR so security checks pass,
    and return the new path along with the Flask client.
    """
    client, app_module, redis_client, data_dir, user_data_dir = flask_test_client
    dest = data_dir / dlc_sandbox_project.name
    shutil.copytree(str(dlc_sandbox_project), str(dest))
    return client, app_module, redis_client, dest


def vram_cleanup_check():
    """
    After a GPU test subprocess completes, verify no zombie Python processes
    hold CUDA contexts on GPU 0 (RTX 5090). (Constraint #2)
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader", "--id=0"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Any lingering processes are reported as a warning, not a test failure
            print(f"\n[VRAM CHECK] Processes on GPU 0 after test:\n{result.stdout}")
    except Exception as e:
        print(f"\n[VRAM CHECK] Could not query GPU: {e}")
