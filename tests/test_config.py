"""Tests for CodeRelay configuration loading and validation."""

import json
from pathlib import Path

import pytest

from coderelay.config import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_CONTEXT_FILENAME,
    DEFAULT_CRITICAL_THRESHOLD,
    DEFAULT_MONITORING_INTERVAL_SECONDS,
    DEFAULT_RECOMMEND_THRESHOLD,
    AgentConfig,
    Config,
    MonitoringConfig,
    WorkspaceConfig,
    load_config,
    parse_config_dict,
)
from coderelay.exceptions import (
    ConfigFormatError,
    ConfigNotFoundError,
    ConfigValidationError,
)


def test_parse_valid_minimal_config(tmp_path: Path):
    raw = {
        "workspace": {
            "path": str(tmp_path),
        }
    }
    cfg = parse_config_dict(raw)
    assert cfg.workspace.path == tmp_path
    assert cfg.workspace.context_file == DEFAULT_CONTEXT_FILENAME
    assert cfg.monitoring.interval_seconds == DEFAULT_MONITORING_INTERVAL_SECONDS
    assert cfg.monitoring.recommend_threshold == DEFAULT_RECOMMEND_THRESHOLD
    assert cfg.monitoring.critical_threshold == DEFAULT_CRITICAL_THRESHOLD
    assert cfg.primary_agent.name == "codex"
    assert cfg.primary_agent.command == "codex"
    assert cfg.secondary_agent.name == "antigravity"
    assert cfg.secondary_agent.command == "agy"


def test_parse_valid_custom_config(tmp_path: Path):
    raw = {
        "workspace": {
            "path": str(tmp_path),
            "context_file": "CUSTOM_CONTEXT.md",
        },
        "monitoring": {
            "interval_seconds": 60,
            "recommend_threshold": 85.0,
            "critical_threshold": 92.5,
        },
        "agents": {
            "primary": {
                "name": "my-codex",
                "command": "codex.exe",
                "enabled": True,
                "app_server_url": "http://127.0.0.1:4545",
            },
            "secondary": {
                "name": "my-agy",
                "command": "agy.cmd",
                "enabled": False,
            },
        },
    }
    cfg = parse_config_dict(raw)
    assert cfg.workspace.context_file == "CUSTOM_CONTEXT.md"
    assert cfg.workspace.context_file_path == tmp_path / "CUSTOM_CONTEXT.md"
    assert cfg.monitoring.interval_seconds == 60
    assert cfg.monitoring.recommend_threshold == 85.0
    assert cfg.monitoring.critical_threshold == 92.5
    assert cfg.primary_agent.app_server_url == "http://127.0.0.1:4545"
    assert cfg.secondary_agent.enabled is False


def test_parse_config_relative_path_resolution(tmp_path: Path):
    raw = {"workspace": {"path": "subproject"}}
    cfg = parse_config_dict(raw, base_dir=tmp_path)
    assert cfg.workspace.path == (tmp_path / "subproject").resolve()


def test_to_dict_roundtrip(tmp_path: Path):
    raw = {
        "workspace": {
            "path": str(tmp_path),
            "context_file": "CONTEXT.md",
        },
        "monitoring": {
            "interval_seconds": 90,
            "recommend_threshold": 80.0,
            "critical_threshold": 90.0,
        },
        "agents": {
            "primary": {
                "name": "codex",
                "command": "codex",
                "enabled": True,
                "app_server_url": None,
            },
            "secondary": {
                "name": "antigravity",
                "command": "agy",
                "enabled": True,
                "app_server_url": None,
            },
        },
    }
    cfg = parse_config_dict(raw)
    d = cfg.to_dict()
    reparsed = parse_config_dict(d)
    assert reparsed.workspace.path == cfg.workspace.path
    assert reparsed.monitoring.recommend_threshold == 80.0


def test_validation_errors():
    with pytest.raises(ConfigValidationError, match="root must be a JSON object"):
        parse_config_dict("not-a-dict")  # type: ignore

    with pytest.raises(ConfigValidationError, match="Missing required 'workspace'"):
        parse_config_dict({})

    with pytest.raises(ConfigValidationError, match="Missing required field 'path'"):
        parse_config_dict({"workspace": {}})

    with pytest.raises(ConfigValidationError, match="workspace.path must be a non-empty string"):
        parse_config_dict({"workspace": {"path": "   "}})

    with pytest.raises(ConfigValidationError, match="workspace.context_file must be a non-empty string"):
        parse_config_dict({"workspace": {"path": "/valid", "context_file": ""}})

    with pytest.raises(ConfigValidationError, match="monitoring.interval_seconds must be greater than 0"):
        parse_config_dict({"workspace": {"path": "/valid"}, "monitoring": {"interval_seconds": 0}})

    with pytest.raises(ConfigValidationError, match="monitoring.recommend_threshold must be between 0 and 100"):
        parse_config_dict({"workspace": {"path": "/valid"}, "monitoring": {"recommend_threshold": 105.0}})

    with pytest.raises(ConfigValidationError, match="critical_threshold .* must be strictly greater"):
        parse_config_dict(
            {
                "workspace": {"path": "/valid"},
                "monitoring": {"recommend_threshold": 95.0, "critical_threshold": 90.0},
            }
        )

    with pytest.raises(ConfigValidationError, match="agents.primary.name must be a non-empty string"):
        parse_config_dict(
            {
                "workspace": {"path": "/valid"},
                "agents": {"primary": {"name": ""}},
            }
        )

    with pytest.raises(ConfigValidationError, match="agents.secondary.enabled must be a boolean"):
        parse_config_dict(
            {
                "workspace": {"path": "/valid"},
                "agents": {"secondary": {"enabled": "yes"}},
            }
        )


def test_load_config_file_not_found(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.json"
    with pytest.raises(ConfigNotFoundError, match="Configuration file not found"):
        load_config(non_existent)


def test_load_config_invalid_json(tmp_path: Path):
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{ unquoted_key: 123 ", encoding="utf-8")
    with pytest.raises(ConfigFormatError, match="Invalid JSON"):
        load_config(bad_json)


def test_load_config_success(tmp_path: Path):
    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(
        json.dumps({"workspace": {"path": str(tmp_path)}}),
        encoding="utf-8",
    )
    loaded_cfg, resolved_path = load_config(cfg_file)
    assert resolved_path == cfg_file.resolve()
    assert loaded_cfg.workspace.path == tmp_path


def test_load_config_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_file = tmp_path / "custom.json"
    cfg_file.write_text(
        json.dumps({"workspace": {"path": str(tmp_path)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODERELAY_CONFIG", str(cfg_file))
    loaded_cfg, resolved_path = load_config()
    assert resolved_path == cfg_file.resolve()
