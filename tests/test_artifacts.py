"""Artifact pull helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from gpu_resources.artifacts import pull_artifacts, ssh_base


def _ok(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_ssh_base_includes_batch_mode():
    cmd = ssh_base("gpu-abc")
    assert cmd[0] == "ssh"
    assert "BatchMode=yes" in cmd
    assert cmd[-1] == "gpu-abc"


def test_pull_artifacts_skips_empty_remote(tmp_path: Path):
    calls: list[list[str]] = []

    def call(cmd, timeout=60, check=True, **kwargs):
        calls.append(list(cmd))
        return _ok(returncode=1)

    assert pull_artifacts("gpu-abc", "/results", tmp_path / "out", call=call) is False
    assert any("test -d" in c[-1] for c in calls)
