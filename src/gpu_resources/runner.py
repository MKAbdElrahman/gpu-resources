"""Allocate → run → collect logs/artifacts → release (verified)."""

from __future__ import annotations

import base64
import contextlib
import json
import secrets
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gpu_resources.artifacts import pull_artifacts, ssh_base
from gpu_resources.config import Config, parse_duration_seconds
from gpu_resources.dstack_backend import DstackBackend, default_call

TERMINAL = frozenset({"done", "failed", "terminated", "aborted"})
DIGEST_MARKER = "@sha256:"


@dataclass
class RunRequest:
    image: str
    command: str
    out: Path
    gpu: str | None = None
    max_price: float | None = None
    max_duration: str | None = None
    artifacts_dir: str | None = None
    env: list[str] = field(default_factory=list)
    attach: bool = False
    run_name: str | None = None


@dataclass
class RunSummary:
    run_name: str
    result: str = "FAIL"
    image: str | None = None
    exit_code: int | None = None
    artifacts_pulled: bool = False
    resource_release_verified: bool = False
    estimated_cost_usd: float | None = None
    cleanup_errors: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_digest_pinned(image: str) -> None:
    if DIGEST_MARKER not in image:
        raise ValueError(
            "Use a digest-pinned image (must contain '@sha256:'). "
            "Push to Docker Hub first, then pass IMAGE@sha256:..."
        )


def read_registry_auth() -> tuple[str, str] | None:
    config_path = Path.home() / ".docker" / "config.json"
    if not config_path.is_file():
        return None
    try:
        auths = json.loads(config_path.read_text()).get("auths") or {}
        entry = auths.get("https://index.docker.io/v1/") or auths.get("https://index.docker.io/v1")
        if not entry or "auth" not in entry:
            return None
        username, password = base64.b64decode(entry["auth"]).decode().split(":", 1)
        return username, password
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def redacted_task(task: dict[str, Any]) -> dict[str, Any]:
    copy = dict(task)
    if "registry_auth" in copy:
        auth = dict(copy["registry_auth"])
        if "password" in auth:
            auth["password"] = "<redacted>"
        copy["registry_auth"] = auth
    return copy


class Runner:
    def __init__(
        self,
        config: Config,
        backend: DstackBackend | None = None,
        call: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        background: Callable[[list[str], Path], subprocess.Popen] | None = None,
        interactive_ssh: Callable[[str], None] | None = None,
        ssh_run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.call = call or default_call
        self.backend = backend or DstackBackend(config.project, config.fleet, call=self.call)
        self.sleep = sleep
        self.clock = clock
        self._background = background
        self._interactive_ssh = interactive_ssh
        self._ssh_run = ssh_run
        self._processes: list[subprocess.Popen] = []
        self._handles: list[Any] = []

    def _bg(self, cmd: list[str], log_path: Path) -> subprocess.Popen:
        if self._background:
            return self._background(cmd, log_path)
        handle = log_path.open("w")
        self._handles.append(handle)
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
        self._processes.append(proc)
        return proc

    def run(self, request: RunRequest) -> RunSummary:
        require_digest_pinned(request.image)
        name = request.run_name or ("gpu-" + secrets.token_hex(4))
        out = request.out
        out.mkdir(parents=True, exist_ok=True)
        gpu = request.gpu or self.config.gpu
        max_price = self.config.max_price if request.max_price is None else request.max_price
        max_duration = request.max_duration or self.config.max_duration
        artifacts_dir = request.artifacts_dir or self.config.artifacts_dir
        duration_s = parse_duration_seconds(max_duration)
        # Keepalive slightly under max_duration so dstack can stop cleanly.
        keepalive_s = max(60, duration_s - 30)

        summary = RunSummary(run_name=name, image=request.image)
        submitted = False
        password: str | None = None

        def redact(text: str) -> str:
            return text.replace(password, "<redacted>") if password else text

        def on_alarm(*_: Any) -> None:
            raise TimeoutError(f"Bounded GPU run exceeded deadline ({max_duration})")

        signal.signal(signal.SIGALRM, on_alarm)
        signal.signal(signal.SIGTERM, on_alarm)
        signal.signal(signal.SIGINT, on_alarm)
        signal.alarm(duration_s + 120)

        try:
            auth = read_registry_auth()
            task: dict[str, Any] = {
                "type": "task",
                "name": name,
                "image": request.image,
                "resources": {"gpu": gpu},
                "backends": list(self.config.backends),
                "regions": list(self.config.regions),
                "fleets": [self.config.fleet],
                "spot_policy": self.config.spot_policy,
                "max_price": max_price,
                "max_duration": max_duration,
                "stop_duration": self.config.stop_duration,
                "retry": False,
                "idle_duration": self.config.idle_duration,
                "commands": [f"sleep {keepalive_s}"],
            }
            if auth:
                username, password = auth
                task["registry_auth"] = {"username": username, "password": password}

            (out / "task.redacted.json").write_text(json.dumps(redacted_task(task), indent=2))
            with tempfile.TemporaryDirectory() as temp:
                submitted = True
                self.backend.apply(task, Path(temp))

            print(json.dumps({"run": name, "phase": "provisioning", "out": str(out)}), flush=True)
            while True:
                current = self.backend.get_run(name)
                if current and current.get("status") == "running":
                    break
                if current and current.get("status") in TERMINAL:
                    raise RuntimeError("Cloud instance stopped during provisioning")
                self.sleep(10)

            self._bg(self.backend.attach_cmd(name), out / "attach.log")
            ssh = ssh_base(name)
            for _ in range(60):
                if self.call([*ssh, "true"], check=False, timeout=15).returncode == 0:
                    break
                self.sleep(1)
            else:
                raise RuntimeError("Cloud SSH unavailable")

            self.call([*ssh, f"mkdir -p {shlex.quote(artifacts_dir)}"], timeout=60)

            env_exports = [f"export ARTIFACTS_DIR={shlex.quote(artifacts_dir)}"]
            for item in request.env:
                if "=" not in item:
                    raise ValueError(f"Invalid --env {item!r}; expected KEY=VAL")
                key, value = item.split("=", 1)
                env_exports.append(f"export {shlex.quote(key)}={shlex.quote(value)}")
            remote = " && ".join(env_exports) + " && " + request.command

            run_log = out / "run.log"
            try:
                if self._ssh_run:
                    completed = self._ssh_run(
                        [*ssh, remote],
                        timeout=max(30, duration_s - 60),
                        log_path=run_log,
                    )
                else:
                    with run_log.open("w") as log:
                        completed = subprocess.run(
                            [*ssh, remote],
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=max(30, duration_s - 60),
                            text=True,
                        )
            except subprocess.TimeoutExpired as exc:
                summary.error = "Remote command timed out"
                raise RuntimeError(summary.error) from exc
            summary.exit_code = completed.returncode

            try:
                summary.artifacts_pulled = pull_artifacts(
                    name, artifacts_dir, out / "artifacts", call=self.call
                )
            except Exception as exc:
                summary.cleanup_errors.append(f"ArtifactPull:{type(exc).__name__}")

            if request.attach:
                print(
                    json.dumps({"run": name, "phase": "attach", "hint": f"ssh {name}"}),
                    flush=True,
                )
                if self._interactive_ssh:
                    self._interactive_ssh(name)
                else:
                    subprocess.call(["ssh", "-o", "StrictHostKeyChecking=accept-new", name])

            if completed.returncode == 0:
                summary.result = "PASS"
            else:
                summary.result = "FAIL"
                summary.error = summary.error or f"Remote command exited {completed.returncode}"
        except Exception as exc:
            summary.result = "FAIL"
            summary.error = redact(str(exc))
            print("GPU run failed:", summary.error, flush=True)
        finally:
            signal.alarm(0)
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal.SIG_IGN)
            released = not submitted
            if submitted:
                try:
                    self.backend.stop(name, check=False)
                except Exception as exc:
                    summary.cleanup_errors.append(type(exc).__name__)
                for _ in range(24):
                    try:
                        current = self.backend.get_run(name)
                        instances = self.backend.fleet_instances()
                        released = (not instances) and (
                            not current or current.get("status") in TERMINAL
                        )
                        if current is not None:
                            summary.estimated_cost_usd = current.get("cost")
                        if released:
                            break
                    except Exception as exc:
                        summary.cleanup_errors.append(type(exc).__name__)
                    self.sleep(5)
            for proc in self._processes:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            for handle in self._handles:
                with contextlib.suppress(Exception):
                    handle.close()
            summary.resource_release_verified = released
            if not released:
                summary.result = "FAIL"
                if not summary.error:
                    summary.error = "Resource release not verified"
            (out / "summary.json").write_text(json.dumps(summary.to_dict(), indent=2))
            print(json.dumps(summary.to_dict()), flush=True)
        return summary


def status_run(
    config: Config,
    name: str,
    call: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    backend = DstackBackend(config.project, config.fleet, call=call or default_call)
    run = backend.get_run(name)
    if run is None:
        return {"run_name": name, "found": False}
    return {
        "run_name": name,
        "found": True,
        "status": run.get("status"),
        "cost": run.get("cost"),
        "run": run,
    }


def stop_run(
    config: Config,
    name: str,
    call: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    backend = DstackBackend(config.project, config.fleet, call=call or default_call)
    backend.stop(name, check=False)
    released = False
    cost = None
    for _ in range(24):
        current = backend.get_run(name)
        instances = backend.fleet_instances()
        cost = current.get("cost") if current else cost
        released = (not instances) and (not current or current.get("status") in TERMINAL)
        if released:
            break
        sleep(5)
    return {
        "run_name": name,
        "resource_release_verified": released,
        "estimated_cost_usd": cost,
    }
