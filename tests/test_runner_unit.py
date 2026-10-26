"""Unit tests: digest pin, cleanup-on-failure, release verification gates success."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from gpu_resources.config import Config, load_config, parse_duration_seconds
from gpu_resources.dstack_backend import DstackBackend
from gpu_resources.runner import (
    Runner,
    RunRequest,
    require_digest_pinned,
)


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class FakeBackend:
    def __init__(self):
        self.applied = []
        self.stopped = []
        self.status_sequence = ["provisioning", "running"]
        self._idx = 0
        self.instances_after_stop = [[], []]
        self._stop_poll = 0
        self.cost = 0.12

    def apply(self, task, workdir):
        self.applied.append(task)

    def get_run(self, name):
        if self.stopped:
            return {
                "run_spec": {"run_name": name},
                "status": "terminated",
                "cost": self.cost,
            }
        if self._idx < len(self.status_sequence):
            status = self.status_sequence[self._idx]
            self._idx += 1
        else:
            status = "running"
        return {"run_spec": {"run_name": name}, "status": status, "cost": self.cost}

    def attach_cmd(self, name):
        return ["dstack", "attach", name]

    def stop(self, name, check=False):
        self.stopped.append(name)
        self._stop_poll = 0
        return _ok()

    def fleet_instances(self):
        if not self.stopped:
            return [{"id": "i1"}]
        # First poll after stop may still show instances, then empty.
        seq = self.instances_after_stop
        idx = min(self._stop_poll, len(seq) - 1)
        self._stop_poll += 1
        return seq[idx]


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "results"


def test_require_digest_pinned_rejects_tag_only():
    with pytest.raises(ValueError, match="digest-pinned"):
        require_digest_pinned("pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime")


def test_require_digest_pinned_accepts_digest():
    require_digest_pinned("user/img@sha256:abcd")


def test_parse_duration_seconds():
    assert parse_duration_seconds("25m") == 1500
    assert parse_duration_seconds("90s") == 90
    assert parse_duration_seconds("1h") == 3600


def test_load_config_overlay(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('project = "other"\nmax_price = 0.5\n')
    cfg = load_config(path)
    assert cfg.project == "other"
    assert cfg.max_price == 0.5
    assert cfg.fleet == "robotics-experiments"


def test_run_pass_and_release_verified(out_dir: Path):
    backend = FakeBackend()
    calls: list[list[str]] = []

    def call(cmd, timeout=60, check=True, **kwargs):
        calls.append(list(cmd))
        # SSH reachability and mkdir / artifact helpers
        if cmd[:1] == ["ssh"] and cmd[-1] == "true":
            return _ok()
        if cmd[:1] == ["ssh"] and "mkdir" in cmd[-1]:
            return _ok()
        if cmd[:1] == ["ssh"] and "test -d" in cmd[-1]:
            return _ok(returncode=1)  # no artifacts
        return _ok()

    def ssh_run(cmd, timeout=60, log_path=None):
        if log_path:
            log_path.write_text("ok\n")
        return _ok(returncode=0)

    def background(cmd, log_path):
        log_path.write_text("attached\n")
        return mock.Mock(poll=mock.Mock(return_value=0), terminate=mock.Mock(), wait=mock.Mock())

    with mock.patch("gpu_resources.runner.signal.alarm"), mock.patch(
        "gpu_resources.runner.signal.signal"
    ), mock.patch("gpu_resources.runner.read_registry_auth", return_value=None), mock.patch(
        "gpu_resources.runner.pull_artifacts", return_value=False
    ):
        runner = Runner(
            Config(max_duration="2m"),
            backend=backend,  # type: ignore[arg-type]
            call=call,
            sleep=lambda _: None,
            background=background,
            ssh_run=ssh_run,
        )
        summary = runner.run(
            RunRequest(
                image="user/img@sha256:deadbeef",
                command="nvidia-smi",
                out=out_dir,
                run_name="gpu-test0001",
            )
        )

    assert summary.result == "PASS"
    assert summary.resource_release_verified is True
    assert backend.stopped == ["gpu-test0001"]
    assert backend.applied and backend.applied[0]["image"] == "user/img@sha256:deadbeef"
    assert "sleep" in backend.applied[0]["commands"][0]
    assert (out_dir / "summary.json").is_file()
    assert (out_dir / "task.redacted.json").is_file()


def test_cleanup_runs_even_when_remote_command_fails(out_dir: Path):
    backend = FakeBackend()

    def call(cmd, timeout=60, check=True, **kwargs):
        if cmd[:1] == ["ssh"] and cmd[-1] == "true":
            return _ok()
        if cmd[:1] == ["ssh"] and "mkdir" in cmd[-1]:
            return _ok()
        return _ok()

    def ssh_run(cmd, timeout=60, log_path=None):
        if log_path:
            log_path.write_text("boom\n")
        return _ok(returncode=7)

    def background(cmd, log_path):
        log_path.write_text("")
        return mock.Mock(poll=mock.Mock(return_value=0), terminate=mock.Mock(), wait=mock.Mock())

    with mock.patch("gpu_resources.runner.signal.alarm"), mock.patch(
        "gpu_resources.runner.signal.signal"
    ), mock.patch("gpu_resources.runner.read_registry_auth", return_value=None), mock.patch(
        "gpu_resources.runner.pull_artifacts", return_value=False
    ):
        summary = Runner(
            Config(max_duration="2m"),
            backend=backend,  # type: ignore[arg-type]
            call=call,
            sleep=lambda _: None,
            background=background,
            ssh_run=ssh_run,
        ).run(
            RunRequest(
                image="user/img@sha256:abc",
                command="false",
                out=out_dir,
                run_name="gpu-fail0001",
            )
        )

    assert summary.result == "FAIL"
    assert summary.exit_code == 7
    assert backend.stopped == ["gpu-fail0001"]
    assert summary.resource_release_verified is True


def test_unverified_release_forces_fail_even_if_command_passed(out_dir: Path):
    backend = FakeBackend()
    backend.instances_after_stop = [["still-here"]] * 30  # never empty

    def call(cmd, timeout=60, check=True, **kwargs):
        if cmd[:1] == ["ssh"] and cmd[-1] == "true":
            return _ok()
        if cmd[:1] == ["ssh"] and "mkdir" in cmd[-1]:
            return _ok()
        return _ok()

    def ssh_run(cmd, timeout=60, log_path=None):
        return _ok(returncode=0)

    def background(cmd, log_path):
        return mock.Mock(poll=mock.Mock(return_value=0), terminate=mock.Mock(), wait=mock.Mock())

    with mock.patch("gpu_resources.runner.signal.alarm"), mock.patch(
        "gpu_resources.runner.signal.signal"
    ), mock.patch("gpu_resources.runner.read_registry_auth", return_value=None), mock.patch(
        "gpu_resources.runner.pull_artifacts", return_value=False
    ):
        summary = Runner(
            Config(max_duration="2m"),
            backend=backend,  # type: ignore[arg-type]
            call=call,
            sleep=lambda _: None,
            background=background,
            ssh_run=ssh_run,
        ).run(
            RunRequest(
                image="user/img@sha256:abc",
                command="true",
                out=out_dir,
                run_name="gpu-leak0001",
            )
        )

    data = json.loads((out_dir / "summary.json").read_text())
    assert summary.result == "FAIL"
    assert summary.resource_release_verified is False
    assert data["resource_release_verified"] is False
    assert backend.stopped  # stop was still attempted


def test_cli_rejects_undigested_image():
    from gpu_resources.cli import main

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "run",
                "--image",
                "user/img:latest",
                "--command",
                "true",
                "--out",
                "/tmp/out",
            ]
        )
    assert exc.value.code == 2


def test_cli_run_parses_remote_command_without_clobbering_subcommand(tmp_path, monkeypatch):
    from gpu_resources import cli
    from gpu_resources.runner import RunSummary

    captured = {}

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, request):
            captured["command"] = request.command
            captured["image"] = request.image
            return RunSummary(
                run_name="gpu-x",
                result="PASS",
                resource_release_verified=True,
            )

    monkeypatch.setattr(cli, "Runner", FakeRunner)
    code = cli.main(
        [
            "run",
            "--image",
            "user/img@sha256:abc",
            "--command",
            'nvidia-smi && echo PASS > "$ARTIFACTS_DIR/smoke.txt"',
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert captured["image"] == "user/img@sha256:abc"
    assert "nvidia-smi" in captured["command"]
    assert "ARTIFACTS_DIR" in captured["command"]


def test_dstack_backend_apply_writes_task(tmp_path: Path):
    recorded = []

    def call(cmd, timeout=60, check=True, **kwargs):
        recorded.append(cmd)
        return _ok(stdout='{"runs":[]}')

    backend = DstackBackend("proj", "fleet", call=call)
    backend.apply({"type": "task", "name": "n"}, tmp_path)
    assert (tmp_path / "task.dstack.yml").is_file()
    assert recorded[0][:2] == ["dstack", "apply"]
