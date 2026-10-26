# Contributing

## Setup

```bash
uv venv .venv
uv pip install -e '.[dev]'
source .venv/bin/activate
```

## Checks

```bash
ruff check src tests
pytest
```

Keep the tool **domain-agnostic**: no robotics, NATS, or experiment-framework imports in `src/`.

## Cloud runs

Live dstack runs spend money. Prefer unit tests with mocked subprocesses. If you must run live, use a short `--max-duration`, a digest-pinned public image, and confirm the fleet is empty afterward (`gpu-resources status` / `dstack fleet`).

## Pull requests

- Keep diffs focused.
- Add or update unit tests for behavior changes.
- Do not commit `results/`, credentials, or Docker Hub tokens.
