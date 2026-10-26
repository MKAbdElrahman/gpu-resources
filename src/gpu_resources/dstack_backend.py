"""Thin wrapper around the dstack CLI (JSON where available)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

CallFn = Callable[..., subprocess.CompletedProcess[str]]


def default_call(
    cmd: list[str],
    timeout: float | None = 60,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
    if check and result.returncode:
        detail = (result.stdout or "") + "\n" + (result.stderr or "")
        raise RuntimeError(f"{cmd[0]} failed: {detail[-2500:]}")
    return result


class DstackBackend:
    def __init__(self, project: str, fleet: str, call: CallFn | None = None):
        self.project = project
        self.fleet = fleet
        self.call = call or default_call

    def apply(self, task: dict[str, Any], workdir: Path) -> None:
        path = workdir / "task.dstack.yml"
        path.touch(mode=0o600)
        path.write_text(json.dumps(task))
        self.call(
            [
                "dstack",
                "apply",
                "--project",
                self.project,
                "-f",
                str(path),
                "--no-repo",
                "-y",
                "-d",
            ],
            timeout=90,
            cwd=str(workdir),
        )

    def list_runs(self) -> list[dict[str, Any]]:
        result = self.call(
            ["dstack", "ps", "--project", self.project, "-a", "--json"],
            timeout=60,
        )
        return json.loads(result.stdout)["runs"]

    def get_run(self, name: str) -> dict[str, Any] | None:
        return next(
            (r for r in self.list_runs() if r["run_spec"]["run_name"] == name),
            None,
        )

    def attach_cmd(self, name: str) -> list[str]:
        return ["dstack", "attach", "--project", self.project, name]

    def stop(self, name: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self.call(
            ["dstack", "stop", "--project", self.project, "-y", "-x", name],
            timeout=90,
            check=check,
        )

    def fleet_instances(self) -> list[Any]:
        result = self.call(
            ["dstack", "fleet", "--project", self.project, "get", self.fleet, "--json"],
            timeout=60,
        )
        payload = json.loads(result.stdout)
        return payload.get("instances") or []
