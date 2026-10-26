"""CLI: gpu-resources run|status|stop."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gpu_resources.config import Config, load_config
from gpu_resources.runner import Runner, RunRequest, require_digest_pinned, status_run, stop_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpu-resources",
        description="Borrow a cloud GPU, run a Docker Hub image, collect logs/artifacts, release.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional TOML config (default: ~/.config/gpu-resources/config.toml)",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    run = sub.add_parser("run", help="Allocate GPU, run image command, collect, release")
    run.add_argument("--image", required=True, help="Digest-pinned image USER/IMG@sha256:...")
    run.add_argument(
        "--command",
        required=True,
        dest="remote_command",
        help="Shell command to run on the GPU host",
    )
    run.add_argument("--out", type=Path, required=True, help="Local directory for logs and artifacts")
    run.add_argument("--gpu", default=None, help="GPU resource string (default from config)")
    run.add_argument("--max-price", type=float, default=None)
    run.add_argument("--max-duration", default=None, help="e.g. 25m")
    run.add_argument("--artifacts-dir", default=None, help="Remote artifacts directory (default /results)")
    run.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Environment variable for the remote command (repeatable)",
    )
    run.add_argument(
        "--attach",
        action="store_true",
        help="Open an interactive SSH session after the command (before release)",
    )
    run.add_argument("--run-name", default=None, help="Optional fixed dstack run name")

    status = sub.add_parser("status", help="Show dstack run status")
    status.add_argument("name", help="Run name")

    stop = sub.add_parser("stop", help="Stop a run and verify fleet release")
    stop.add_argument("name", help="Run name")

    return parser


def apply_config_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """CLI flags override config for run-related knobs when present on the namespace."""
    if getattr(args, "gpu", None):
        cfg.gpu = args.gpu
    if getattr(args, "max_price", None) is not None:
        cfg.max_price = args.max_price
    if getattr(args, "max_duration", None):
        cfg.max_duration = args.max_duration
    if getattr(args, "artifacts_dir", None):
        cfg.artifacts_dir = args.artifacts_dir
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.subcommand == "run":
        try:
            require_digest_pinned(args.image)
        except ValueError as exc:
            parser.error(str(exc))
        apply_config_overrides(cfg, args)
        request = RunRequest(
            image=args.image,
            command=args.remote_command,
            out=args.out,
            gpu=args.gpu,
            max_price=args.max_price,
            max_duration=args.max_duration,
            artifacts_dir=args.artifacts_dir,
            env=list(args.env),
            attach=args.attach,
            run_name=args.run_name,
        )
        summary = Runner(cfg).run(request)
        return 0 if summary.result == "PASS" and summary.resource_release_verified else 1

    if args.subcommand == "status":
        print(json.dumps(status_run(cfg, args.name), indent=2, default=str))
        return 0

    if args.subcommand == "stop":
        result = stop_run(cfg, args.name)
        print(json.dumps(result, indent=2))
        return 0 if result["resource_release_verified"] else 1

    parser.error(f"Unknown command {args.subcommand}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
