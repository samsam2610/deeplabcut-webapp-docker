"""GPU selection through the inline path, and the shm requirement it rides on.

Both come from the same 2026-08-07 investigation: `dataloader_workers: 4` died
with "Unexpected bus error … insufficient shared memory" 10 s into training,
and the GPU selector recorded a choice nothing acted on.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dlc.inline_analysis import gpu_device, _snap_key  # noqa: E402


class TestGpuDevice:
    @pytest.mark.parametrize("raw", ["", None, "auto", "AUTO", "  ", "none"])
    def test_unset_means_let_dlc_choose(self, raw):
        assert gpu_device(raw) == ""

    @pytest.mark.parametrize("raw,want", [
        (0, "cuda:0"), (1, "cuda:1"), ("1", "cuda:1"), (" 1 ", "cuda:1"),
    ])
    def test_an_index_becomes_a_torch_device(self, raw, want):
        assert gpu_device(raw) == want

    def test_explicit_device_strings_pass_through(self):
        assert gpu_device("cpu") == "cpu"
        assert gpu_device("cuda:1") == "cuda:1"

    def test_garbage_falls_back_to_auto_rather_than_crashing(self):
        # A malformed selection must not take the whole run down; DLC's own default is
        # a safe landing spot.
        assert gpu_device("banana") == ""


class TestSnapKeyIncludesDevice:
    ARGS = ("/p/config.yaml", 1, "/p/snapshot-200.pt")

    def test_different_gpus_are_different_sessions(self):
        # Sessions are REUSED by key. Without the device in it, asking for
        # GPU 1 while a warm GPU 0 session exists silently attaches to the
        # GPU 0 one and the selector does nothing at all.
        assert _snap_key(*self.ARGS, "cuda:0") != _snap_key(*self.ARGS, "cuda:1")

    def test_the_same_gpu_is_the_same_session(self):
        assert _snap_key(*self.ARGS, "cuda:1") == _snap_key(*self.ARGS, "cuda:1")

    def test_auto_is_its_own_session(self):
        assert _snap_key(*self.ARGS, "") not in (
            _snap_key(*self.ARGS, "cuda:0"), _snap_key(*self.ARGS, "cuda:1"))


class TestWorkerRuntimeConfig:
    """docker-compose guarantees the two GPU workers need."""

    @pytest.fixture(scope="class")
    def compose(self):
        return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]

    def test_the_pytorch_worker_declares_shm(self, compose):
        # 64 MB (Docker's default) kills any run with dataloader_workers > 0:
        # DataLoader ships every batch through /dev/shm.
        assert compose["worker"].get("shm_size"), (
            "worker needs shm_size or dataloader_workers > 0 dies with a bus error")

    def test_every_gpu_worker_declares_shm(self, compose):
        # dlc-3d-worker hit this first and was fixed in isolation; the DLC
        # worker inherited the same bug months later. Guard the class, not the
        # instance.
        gpu_services = [
            name for name, svc in compose.items()
            if "nvidia" in str(svc.get("deploy", {})) and "torch" not in name
            and name != "worker-tf"          # TF does not use torch DataLoader
        ]
        assert gpu_services, "no GPU services found — has the compose shape changed?"
        missing = [n for n in gpu_services if not compose[n].get("shm_size")]
        assert not missing, f"GPU workers without shm_size: {missing}"

    def test_the_worker_pins_pci_device_order(self, compose):
        # Without it torch uses FASTEST_FIRST and cuda:0 is the RTX PRO 6000,
        # while nvidia-smi and the training task call that GPU 1 — so the UI's
        # "GPU 0" would select the other card.
        env = compose["worker"].get("environment") or []
        assert any(str(e).strip() == "CUDA_DEVICE_ORDER=PCI_BUS_ID" for e in env), env


class TestDeviceReachesTheRunner:
    """Source assertions: the wiring has no test harness of its own."""

    SRC = (ROOT / "src" / "dlc" / "tasks.py").read_text()

    def test_the_session_passes_its_device_to_the_runner(self):
        assert "device=(device or None)" in self.SRC

    def test_a_batch_size_change_does_not_move_the_session_off_its_gpu(self):
        # The runner is rebuilt when a request changes batch_size; passing
        # device=None there would silently relocate a GPU-1 session to GPU 0.
        rebuild = self.SRC.split("!= cached_batch_size")[1][:600]
        assert "device=(device or None)" in rebuild
        assert "device=None" not in rebuild

    def test_results_take_a_per_request_ttl(self):
        # 300 s suits the interactive card; a batch nobody watches needs longer
        # or its completions expire uncounted.
        assert "def _publish_result(" in self.SRC
        assert "ttl=300" in self.SRC
        assert "int(ttl or 300)" in self.SRC
        bat = (ROOT / "src" / "dlc" / "batch_analyze.py").read_text()
        assert '"result_ttl":    BATCH_RESULT_TTL_S' in bat
