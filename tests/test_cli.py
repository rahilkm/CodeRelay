"""Tests for CodeRelay CLI commands."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from coderelay import __version__
from coderelay.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "CodeRelay" in result.output
    assert "status" in result.output


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_status_missing_config():
    result = runner.invoke(app, ["status", "--config", "non_existent_file.json"])
    assert result.exit_code == 1
    assert "Configuration error:" in result.output or "Configuration error:" in result.stderr


def test_status_invalid_config_json(tmp_path: Path):
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{bad", encoding="utf-8")
    result = runner.invoke(app, ["status", "--config", str(bad_cfg)])
    assert result.exit_code == 1
    assert "Configuration error:" in result.output or "Configuration error:" in result.stderr


def test_status_invalid_config_validation(tmp_path: Path):
    invalid_cfg = tmp_path / "invalid.json"
    invalid_cfg.write_text(
        json.dumps({
            "workspace": {"path": str(tmp_path)},
            "monitoring": {"interval_seconds": -5},
        }),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["status", "--config", str(invalid_cfg)])
    assert result.exit_code == 1
    assert "monitoring.interval_seconds must be greater than 0" in (
        result.output + (result.stderr or "")
    )


def test_status_valid_config(tmp_path: Path):
    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(
        json.dumps({
            "workspace": {
                "path": str(tmp_path),
                "context_file": "CODERELAY_CONTEXT.md",
            },
            "monitoring": {
                "interval_seconds": 120,
                "recommend_threshold": 90.0,
                "critical_threshold": 95.0,
            },
            "agents": {
                "primary": {"name": "codex", "command": "codex", "enabled": True},
                "secondary": {"name": "antigravity", "command": "agy", "enabled": True},
            },
        }),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["status", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "=== CodeRelay Status ===" in result.output
    assert "Primary (Codex)" in result.output
    assert "Secondary (AGY)" in result.output
    assert "120s" in result.output
    assert "90.0%" in result.output
    assert "95.0%" in result.output
    assert "Context exists   : No" in result.output

    # Create the context file and verify status detects it
    (tmp_path / "CODERELAY_CONTEXT.md").write_text("# Context", encoding="utf-8")
    result2 = runner.invoke(app, ["status", "--config", str(cfg_file)])
    assert result2.exit_code == 0
    assert "Context exists   : Yes" in result2.output


def test_monitor_missing_config():
    result = runner.invoke(app, ["monitor", "--config", "non_existent_file.json"])
    assert result.exit_code == 1
    assert "Configuration error:" in result.output or "Configuration error:" in result.stderr


def test_monitor_invocation(tmp_path: Path):
    from unittest.mock import patch
    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(
        json.dumps({
            "workspace": {"path": str(tmp_path)},
        }),
        encoding="utf-8",
    )
    with patch("coderelay.cli.UsageMonitor.run") as mock_run:
        result = runner.invoke(app, ["monitor", "--config", str(cfg_file)])
        assert result.exit_code == 0
        mock_run.assert_called_once()


def test_cli_agents_list():
    result = runner.invoke(app, ["agents"])
    assert result.exit_code == 0
    assert "codex" in result.output.lower()
    assert "antigravity" in result.output.lower()


def test_cli_agent_status_unoccupied(tmp_path: Path):
    result = runner.invoke(app, ["agent", "status", "-w", str(tmp_path)])
    assert result.exit_code == 0
    assert "Active Agent: None" in result.output


def test_cli_agent_start_and_stop(tmp_path: Path):
    from unittest.mock import MagicMock, patch

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.get_pid.return_value = 54321

    with patch("coderelay.cli._get_agent_manager") as mock_get_mgr:
        mock_mgr = MagicMock()
        mock_get_mgr.return_value = mock_mgr
        mock_mgr.start_agent.return_value = mock_agent

        # Test start
        res_start = runner.invoke(
            app,
            ["agent", "start", "codex", "-w", str(tmp_path), "-i", "Hello agent"],
        )
        assert res_start.exit_code == 0
        assert "Agent 'codex' started successfully" in res_start.output
        assert "54321" in res_start.output
        mock_mgr.start_agent.assert_called_once()

        # Test stop
        mock_mgr.stop_agent.return_value = True
        res_stop = runner.invoke(app, ["agent", "stop", "-w", str(tmp_path)])
        assert res_stop.exit_code == 0
        assert "stopped successfully" in res_stop.output


def test_cli_agent_start_occupied_error(tmp_path: Path):
    from unittest.mock import MagicMock, patch
    from coderelay.exceptions import WorkspaceOccupiedError

    with patch("coderelay.cli._get_agent_manager") as mock_get_mgr:
        mock_mgr = MagicMock()
        mock_get_mgr.return_value = mock_mgr
        mock_mgr.start_agent.side_effect = WorkspaceOccupiedError("Workspace is already occupied")

        res = runner.invoke(app, ["agent", "start", "codex", "-w", str(tmp_path)])
        assert res.exit_code == 1
        assert "Workspace error:" in (res.output + (res.stderr or ""))


def test_cli_project_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reg_file = tmp_path / "reg.json"
    monkeypatch.setenv("CODERELAY_HOME", str(tmp_path))

    from unittest.mock import patch
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()

    with patch("coderelay.projects.ensure_git_repo", return_value=repo_dir), \
         patch("coderelay.projects.get_remote_url", return_value="https://github.com/my/project.git"), \
         patch("coderelay.projects.get_default_branch", return_value="main"):

        # Register
        res_reg = runner.invoke(app, ["project", "register", str(repo_dir), "--name", "ProjectCLI"])
        assert res_reg.exit_code == 0
        assert "Project 'ProjectCLI' registered successfully" in res_reg.output

        # List
        res_list = runner.invoke(app, ["projects"])
        assert res_list.exit_code == 0
        assert "ProjectCLI" in res_list.output

        # Inspect
        res_insp = runner.invoke(app, ["project", "inspect", "ProjectCLI"])
        assert res_insp.exit_code == 0
        assert "ProjectCLI" in res_insp.output
        assert "main" in res_insp.output

        # Remove
        res_rem = runner.invoke(app, ["project", "remove", "ProjectCLI"])
        assert res_rem.exit_code == 0
        assert "removed from registry" in res_rem.output
        assert repo_dir.is_dir()  # Files preserved


def test_cli_context_commands(tmp_path: Path):
    # Init
    res_init = runner.invoke(app, ["context", "init", "-w", str(tmp_path)])
    assert res_init.exit_code == 0
    assert "Created context file" in res_init.output

    # Show
    res_show = runner.invoke(app, ["context", "show", "-w", str(tmp_path)])
    assert res_show.exit_code == 0
    assert "CodeRelay Context" in res_show.output

    # Update
    res_update = runner.invoke(app, ["context", "update", "Completed Tasks", "Implemented Phase 4", "-w", str(tmp_path)])
    assert res_update.exit_code == 0
    assert "updated successfully" in res_update.output

    # Validate
    res_val = runner.invoke(app, ["context", "validate", "-w", str(tmp_path)])
    assert res_val.exit_code == 0
    assert "valid" in res_val.output.lower()


def test_cli_git_commands(tmp_path: Path):
    from unittest.mock import patch
    from coderelay.git import GitStatus
    from coderelay.sync import SyncResult, SyncStatus

    clean_status = GitStatus(current_branch="main", tracking_branch="origin/main", is_clean=True)

    with patch("coderelay.cli.get_git_status", return_value=clean_status), \
         patch("coderelay.cli._resolve_workspace", return_value=tmp_path):

        # Status
        res_st = runner.invoke(app, ["git", "status", "-w", str(tmp_path)])
        assert res_st.exit_code == 0
        assert "Branch       : main" in res_st.output
        assert "Clean" in res_st.output

    # Sync
    with patch("coderelay.sync.ensure_git_repo", return_value=tmp_path), \
         patch("coderelay.sync.GitSyncService.synchronize") as mock_sync, \
         patch("coderelay.cli._resolve_workspace", return_value=tmp_path):

        mock_sync.return_value = SyncResult(
            status=SyncStatus.UP_TO_DATE,
            message="Already up to date.",
            current_branch="main",
            initial_commit="abc1234",
        )
        res_sync = runner.invoke(app, ["git", "sync", "-w", str(tmp_path)])
        assert res_sync.exit_code == 0
        assert "Already up to date" in res_sync.output


def test_cli_start_command(tmp_path: Path):
    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(json.dumps({"workspace": {"path": str(tmp_path)}}), encoding="utf-8")

    with patch("coderelay.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch.project_name = "TestProject"
        mock_orch.workspace_path = tmp_path
        mock_orch.context.current_usage_percent = 45.0
        mock_orch.context.active_agent_name = "codex"
        mock_orch.context.state.value = "active"
        mock_orch_cls.return_value = mock_orch

        result = runner.invoke(app, ["start", "TestProject", "-c", str(cfg_file), "-i", "Fix bugs"])
        assert result.exit_code == 0
        assert "CodeRelay Orchestrator Started" in result.output
        assert "Project          : TestProject" in result.output
        assert "Active Agent     : codex" in result.output
        mock_orch.startup.assert_called_once_with(instruction="Fix bugs")


def test_cli_start_command_error(tmp_path: Path):
    from coderelay.exceptions import ProjectNotFoundError

    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(json.dumps({"workspace": {"path": str(tmp_path)}}), encoding="utf-8")

    with patch("coderelay.cli.Orchestrator", side_effect=ProjectNotFoundError("Project missing")):
        result = runner.invoke(app, ["start", "BadProject", "-c", str(cfg_file)])
        assert result.exit_code == 1
        assert "Orchestration startup error: Project missing" in result.output or "Orchestration startup error: Project missing" in result.stderr


def test_cli_switch_command_approved(tmp_path: Path):
    from coderelay.orchestrator.state import SwitchDecision

    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(json.dumps({"workspace": {"path": str(tmp_path)}}), encoding="utf-8")

    with patch("coderelay.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_decision = SwitchDecision(
            from_agent="codex",
            to_agent="antigravity",
            approved=True,
            timestamp=MagicMock(),
            reason="Manual switch",
        )
        mock_orch.request_manual_switch.return_value = mock_decision
        mock_orch_cls.return_value = mock_orch

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)])
        assert result.exit_code == 0
        assert "Switched active agent from 'codex' to 'antigravity'" in result.output
        mock_orch.request_manual_switch.assert_called_once_with(target_agent="antigravity")


def test_cli_switch_command_declined(tmp_path: Path):
    from coderelay.orchestrator.state import SwitchDecision

    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(json.dumps({"workspace": {"path": str(tmp_path)}}), encoding="utf-8")

    with patch("coderelay.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_decision = SwitchDecision(
            from_agent="codex",
            to_agent="antigravity",
            approved=False,
            timestamp=MagicMock(),
            reason="Manual switch",
        )
        mock_orch.request_manual_switch.return_value = mock_decision
        mock_orch_cls.return_value = mock_orch

        result = runner.invoke(app, ["switch", "-c", str(cfg_file)])
        assert result.exit_code == 0
        assert "Switch declined. Active agent remains 'codex'" in result.output


def test_cli_switch_no_active_agent(tmp_path: Path):
    from coderelay.exceptions import NoActiveAgentError

    cfg_file = tmp_path / "coderelay.json"
    cfg_file.write_text(json.dumps({"workspace": {"path": str(tmp_path)}}), encoding="utf-8")

    with patch("coderelay.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch.request_manual_switch.side_effect = NoActiveAgentError("No active agent running")
        mock_orch_cls.return_value = mock_orch

        result = runner.invoke(app, ["switch", "-c", str(cfg_file)])
        assert result.exit_code == 1
        assert "Switch error: No active agent running" in result.output or "Switch error: No active agent running" in result.stderr





