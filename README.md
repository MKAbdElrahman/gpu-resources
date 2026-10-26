# gpu-resources

[![CI](https://github.com/MKAbdElrahman/gpu-resources/actions/workflows/ci.yml/badge.svg)](https://github.com/MKAbdElrahman/gpu-resources/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Domain-agnostic cloud GPU borrow tool. Push a Docker image to Docker Hub, run it on a rented GPU via [dstack](https://dstack.ai), pull logs and artifacts back locally, then **verify** that cloud resources are fully released.

This package intentionally knows nothing about robotics stacks, NATS, Rerun, or experiment frameworks. Any project can call it.

## Install

```bash
git clone https://github.com/MKAbdElrahman/gpu-resources.git
cd gpu-resources
uv venv .venv
uv pip install -e '.[dev]'
source .venv/bin/activate
```

Requires the `dstack` CLI on `PATH` (configured under `~/.dstack`) and Docker Hub credentials in `~/.docker/config.json` for private images.

## Quick start

1. Build and push your image to Docker Hub; pin by digest.
2. Run:

```bash
gpu-resources run \
  --image USER/IMAGE@sha256:DIGEST \
  --command 'python -u train.py --out "$ARTIFACTS_DIR"' \
  --out ./results/my-run
```

The remote process gets `ARTIFACTS_DIR` (default `/results`). Write outputs there; they are copied to `--out/artifacts/` after the command exits.

3. Inspect `--out/summary.json`. Success requires both a zero exit code **and** `resource_release_verified: true`.

### Optional flags

| Flag | Meaning |
|------|---------|
| `--gpu A40:1` | GPU resource selector |
| `--max-price 0.70` | Cap hourly price |
| `--max-duration 25m` | Wall clock bound (also drives keepalive) |
| `--artifacts-dir /results` | Remote directory to pull back |
| `--env KEY=VAL` | Extra remote env (repeatable) |
| `--attach` | Interactive SSH after the command, before release |

### Status / stop

```bash
gpu-resources status gpu-abcd1234
gpu-resources stop gpu-abcd1234
```

## Config

Defaults match a typical dstack Sky project (`robotics-research` / fleet `robotics-experiments`). Override via flags or `~/.config/gpu-resources/config.toml`:

```toml
project = "robotics-research"
fleet = "robotics-experiments"
backends = ["runpod"]
regions = ["EU-SE-1", "CA-MTL-1"]
gpu = "A40:1"
max_price = 0.70
max_duration = "25m"
artifacts_dir = "/results"
```

## How it works

1. Submit a dstack task with your image and a short keepalive `sleep`.
2. Wait until the run is `running`, attach, SSH in.
3. Run your `--command` with logs streamed to `--out/run.log`.
4. Tar/scp `$ARTIFACTS_DIR` → `--out/artifacts/`.
5. Always `dstack stop -x` and poll until the run is terminal **and** the fleet has no instances.

If release cannot be verified, the run is marked `FAIL` even when the experiment itself succeeded.

## Development

```bash
make check   # ruff + pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Smoke example

See [`examples/smoke.sh`](examples/smoke.sh). Substitute a real digest before spending cloud money:

```bash
IMAGE=pytorch/pytorch@sha256:... MAX_DURATION=15m ./examples/smoke.sh
```

## Non-goals

- Building or pushing images (you push to Docker Hub first)
- SkyPilot / NATS compute controllers
- Multi-node orchestration beyond one GPU instance

## License

Apache License 2.0 — see [LICENSE](LICENSE).
