"""Defaults and optional ~/.config/gpu-resources/config.toml overrides."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "gpu-resources" / "config.toml"


@dataclass
class Config:
    project: str = "robotics-research"
    fleet: str = "robotics-experiments"
    backends: list[str] = field(default_factory=lambda: ["runpod"])
    regions: list[str] = field(default_factory=lambda: ["EU-SE-1", "CA-MTL-1"])
    gpu: str = "A40:1"
    max_price: float = 0.70
    max_duration: str = "25m"
    artifacts_dir: str = "/results"
    spot_policy: str = "on-demand"
    stop_duration: str = "30s"
    idle_duration: str = "0s"


def load_config(path: Path | None = None) -> Config:
    """Load config, overlaying TOML keys that match Config fields."""
    cfg = Config()
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        return cfg
    data = tomllib.loads(config_path.read_text())
    known = {f.name for f in fields(Config)}
    for key, value in data.items():
        if key in known:
            setattr(cfg, key, value)
    return cfg


def parse_duration_seconds(text: str) -> int:
    """Parse durations like '25m', '90s', '1h' into seconds."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    units = {"s": 1, "m": 60, "h": 3600}
    if text[-1] not in units:
        raise ValueError(f"Unsupported duration: {text!r} (use Ns, Nm, or Nh)")
    return int(float(text[:-1]) * units[text[-1]])
