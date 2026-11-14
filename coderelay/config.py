"""Configuration models, loading, and validation for CodeRelay."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from coderelay.exceptions import (
    ConfigFormatError,
    ConfigNotFoundError,
    ConfigValidationError,
)

DEFAULT_CONFIG_FILENAME = "coderelay.json"
DEFAULT_CONTEXT_FILENAME = "CODERELAY_CONTEXT.md"
DEFAULT_MONITORING_INTERVAL_SECONDS = 120
DEFAULT_RECOMMEND_THRESHOLD = 90.0
DEFAULT_CRITICAL_THRESHOLD = 95.0


@dataclass(slots=True)
class AgentConfig:
    """Configuration for an individual coding agent."""

    name: str
    command: str
    enabled: bool = True
    app_server_url: str | None = None

    def validate(self, role: str) -> None:
        if not self.name or not isinstance(self.name, str) or not self.name.strip():
            raise ConfigValidationError(f"agents.{role}.name must be a non-empty string.")
        if not self.command or not isinstance(self.command, str) or not self.command.strip():
            raise ConfigValidationError(f"agents.{role}.command must be a non-empty string.")
        if not isinstance(self.enabled, bool):
            raise ConfigValidationError(f"agents.{role}.enabled must be a boolean.")
        if self.app_server_url is not None and not isinstance(self.app_server_url, str):
            raise ConfigValidationError(
                f"agents.{role}.app_server_url must be a string or null if specified."
            )


@dataclass(slots=True)
class MonitoringConfig:
    """Configuration for usage monitoring and threshold recommendations."""

    interval_seconds: int = DEFAULT_MONITORING_INTERVAL_SECONDS
    recommend_threshold: float = DEFAULT_RECOMMEND_THRESHOLD
    critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD

    def validate(self) -> None:
        if isinstance(self.interval_seconds, bool) or not isinstance(self.interval_seconds, int):
            raise ConfigValidationError("monitoring.interval_seconds must be an integer.")
        if self.interval_seconds <= 0:
            raise ConfigValidationError(
                f"monitoring.interval_seconds must be greater than 0, got {self.interval_seconds}."
            )

        if not isinstance(self.recommend_threshold, (int, float)) or isinstance(
            self.recommend_threshold, bool
        ):
            raise ConfigValidationError("monitoring.recommend_threshold must be a number.")
        if not (0.0 < self.recommend_threshold < 100.0):
            raise ConfigValidationError(
                f"monitoring.recommend_threshold must be between 0 and 100 (exclusive), got {self.recommend_threshold}."
            )

        if not isinstance(self.critical_threshold, (int, float)) or isinstance(
            self.critical_threshold, bool
        ):
            raise ConfigValidationError("monitoring.critical_threshold must be a number.")
        if not (0.0 < self.critical_threshold <= 100.0):
            raise ConfigValidationError(
                f"monitoring.critical_threshold must be between 0 and 100, got {self.critical_threshold}."
            )

        if self.critical_threshold <= self.recommend_threshold:
            raise ConfigValidationError(
                f"monitoring.critical_threshold ({self.critical_threshold}) must be strictly "
                f"greater than monitoring.recommend_threshold ({self.recommend_threshold})."
            )


@dataclass(slots=True)
class WorkspaceConfig:
    """Configuration for the managed local project workspace."""

    path: Path
    context_file: str = DEFAULT_CONTEXT_FILENAME

    def validate(self) -> None:
        if not str(self.path).strip():
            raise ConfigValidationError("workspace.path cannot be empty.")
        if not self.context_file or not isinstance(self.context_file, str) or not self.context_file.strip():
            raise ConfigValidationError("workspace.context_file must be a non-empty string.")

    @property
    def resolved_path(self) -> Path:
        return self.path.expanduser().resolve()

    @property
    def context_file_path(self) -> Path:
        return self.resolved_path / self.context_file


@dataclass(slots=True)
class Config:
    """Root configuration for CodeRelay."""

    workspace: WorkspaceConfig
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    primary_agent: AgentConfig = field(
        default_factory=lambda: AgentConfig(name="codex", command="codex")
    )
    secondary_agent: AgentConfig = field(
        default_factory=lambda: AgentConfig(name="antigravity", command="agy")
    )

    def validate(self) -> None:
        self.workspace.validate()
        self.monitoring.validate()
        self.primary_agent.validate(role="primary")
        self.secondary_agent.validate(role="secondary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": {
                "path": str(self.workspace.path),
                "context_file": self.workspace.context_file,
            },
            "monitoring": asdict(self.monitoring),
            "agents": {
                "primary": asdict(self.primary_agent),
                "secondary": asdict(self.secondary_agent),
            },
        }


def parse_config_dict(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    """Parse and validate a dictionary into a Config instance."""
    if not isinstance(data, dict):
        raise ConfigValidationError("Configuration root must be a JSON object.")

    if "workspace" not in data or not isinstance(data["workspace"], dict):
        raise ConfigValidationError("Missing required 'workspace' section in configuration.")

    ws_data = data["workspace"]
    if "path" not in ws_data:
        raise ConfigValidationError("Missing required field 'path' in workspace configuration.")

    raw_path = ws_data["path"]
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigValidationError("workspace.path must be a non-empty string.")

    ws_path = Path(raw_path)
    if not ws_path.is_absolute() and base_dir is not None:
        ws_path = (base_dir / ws_path).resolve()

    context_file = ws_data.get("context_file", DEFAULT_CONTEXT_FILENAME)
    if not isinstance(context_file, str) or not context_file.strip():
        raise ConfigValidationError("workspace.context_file must be a non-empty string.")

    workspace = WorkspaceConfig(path=ws_path, context_file=context_file)

    # Monitoring section (optional, defaults provided)
    mon_data = data.get("monitoring", {})
    if not isinstance(mon_data, dict):
        raise ConfigValidationError("monitoring must be an object if specified.")

    interval = mon_data.get("interval_seconds", DEFAULT_MONITORING_INTERVAL_SECONDS)
    recommend_thresh = mon_data.get("recommend_threshold", DEFAULT_RECOMMEND_THRESHOLD)
    crit_thresh = mon_data.get("critical_threshold", DEFAULT_CRITICAL_THRESHOLD)

    # Cast integer thresholds to float
    if isinstance(recommend_thresh, int) and not isinstance(recommend_thresh, bool):
        recommend_thresh = float(recommend_thresh)
    if isinstance(crit_thresh, int) and not isinstance(crit_thresh, bool):
        crit_thresh = float(crit_thresh)

    monitoring = MonitoringConfig(
        interval_seconds=interval,
        recommend_threshold=recommend_thresh,
        critical_threshold=crit_thresh,
    )

    # Agents section (optional, defaults provided)
    agents_data = data.get("agents", {})
    if not isinstance(agents_data, dict):
        raise ConfigValidationError("agents must be an object if specified.")

    primary_data = agents_data.get("primary", {})
    if not isinstance(primary_data, dict):
        raise ConfigValidationError("agents.primary must be an object if specified.")

    primary_agent = AgentConfig(
        name=primary_data.get("name", "codex"),
        command=primary_data.get("command", "codex"),
        enabled=primary_data.get("enabled", True),
        app_server_url=primary_data.get("app_server_url"),
    )

    secondary_data = agents_data.get("secondary", {})
    if not isinstance(secondary_data, dict):
        raise ConfigValidationError("agents.secondary must be an object if specified.")

    secondary_agent = AgentConfig(
        name=secondary_data.get("name", "antigravity"),
        command=secondary_data.get("command", "agy"),
        enabled=secondary_data.get("enabled", True),
        app_server_url=secondary_data.get("app_server_url"),
    )

    config = Config(
        workspace=workspace,
        monitoring=monitoring,
        primary_agent=primary_agent,
        secondary_agent=secondary_agent,
    )
    config.validate()
    return config


def find_default_config_path() -> Path | None:
    """Find default config path from env var or current working directory."""
    env_path = os.environ.get("CODERELAY_CONFIG")
    if env_path:
        return Path(env_path)

    cwd_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_path.exists():
        return cwd_path

    return None


def load_config(config_path: Path | str | None = None) -> tuple[Config, Path]:
    """Load, parse, and validate CodeRelay configuration from disk.

    Returns the validated Config and resolved Path of the loaded file.
    """
    if config_path is not None:
        target_path = Path(config_path).expanduser().resolve()
        if not target_path.exists():
            raise ConfigNotFoundError(f"Configuration file not found: {target_path}")
    else:
        found = find_default_config_path()
        if found is None:
            raise ConfigNotFoundError(
                f"No configuration file found. Please create '{DEFAULT_CONFIG_FILENAME}' "
                "or specify one with --config."
            )
        target_path = found.expanduser().resolve()
        if not target_path.exists():
            raise ConfigNotFoundError(f"Configuration file not found: {target_path}")

    if not target_path.is_file():
        raise ConfigNotFoundError(f"Configuration path is not a file: {target_path}")

    try:
        raw_text = target_path.read_text(encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"Failed to read configuration file: {err}") from err

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as err:
        raise ConfigFormatError(
            f"Invalid JSON in {target_path} at line {err.lineno}, column {err.colno}: {err.msg}"
        ) from err

    config = parse_config_dict(data, base_dir=target_path.parent)
    return config, target_path
