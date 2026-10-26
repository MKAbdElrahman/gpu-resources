"""Pull remote artifact directories over SSH/SCP."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

CallFn = Callable[..., subprocess.CompletedProcess[str]]


def ssh_base(run_name: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        run_name,
    ]


def scp_base() -> list[str]:
    return [
        "scp",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-q",
    ]


def pull_artifacts(
    run_name: str,
    remote_dir: str,
    local_dir: Path,
    call: CallFn,
) -> bool:
    """Tar remote_dir on the host and extract into local_dir.

    Returns True if anything was pulled, False if the remote path was missing/empty.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    remote_tar = f"/tmp/gpu-resources-artifacts-{run_name}.tar"
    # Create tar only if the directory exists and has content; otherwise skip.
    remote_q = shlex.quote(remote_dir)
    check = call(
        [
            *ssh_base(run_name),
            f"test -d {remote_q} && find {remote_q} -mindepth 1 -print -quit | grep -q .",
        ],
        check=False,
        timeout=60,
    )
    if check.returncode != 0:
        return False
    call(
        [
            *ssh_base(run_name),
            f"tar -cf {shlex.quote(remote_tar)} -C {remote_q} .",
        ],
        timeout=300,
    )
    local_tar = local_dir / "artifacts.tar"
    call(
        [*scp_base(), f"{run_name}:{remote_tar}", str(local_tar)],
        timeout=600,
    )
    call(
        [*ssh_base(run_name), f"rm -f {remote_tar}"],
        check=False,
        timeout=60,
    )
    subprocess.run(
        ["tar", "-xf", str(local_tar), "-C", str(local_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    local_tar.unlink(missing_ok=True)
    return True
