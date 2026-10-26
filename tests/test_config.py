"""Config and duration parsing."""

from pathlib import Path

import pytest

from gpu_resources.config import Config, load_config, parse_duration_seconds


def test_default_config():
    cfg = Config()
    assert cfg.project == "robotics-research"
    assert cfg.fleet == "robotics-experiments"
    assert cfg.artifacts_dir == "/results"


def test_load_config_missing_file(tmp_path: Path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.project == "robotics-research"


def test_parse_duration_rejects_bad():
    with pytest.raises(ValueError, match="Unsupported duration"):
        parse_duration_seconds("25x")


def test_parse_duration_plain_seconds():
    assert parse_duration_seconds("120") == 120
