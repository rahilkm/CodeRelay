"""Integration tests for Phase 6: end-to-end 'coderelay switch' command across CLI processes."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from coderelay.agents.base import BaseAgent
from coderelay.agents.registry import AgentRegistry
from coderelay.agents.workspace import LOCK_FILENAME, WorkspaceManager, WorkspaceOwnership
from coderelay.cli import app
from coderelay.config import AgentConfig, Config, MonitoringConfig, WorkspaceConfig
from coderelay.context import CONTEXT_FILENAME, init_context
from coderelay.exceptions import (
    GitError,
    HandoffCommitError,
    HandoffContextError,
    HandoffProcessShutdownError,
    HandoffPushError,
    HandoffSyncError,
    HandoffTaskCompletionError,
    HandoffTestFailureError,
    IncomingAgentLaunchError,
    WorkspaceOccupiedError,
)
from coderelay.git import GitResult, GitStatus
from coderelay.orchestrator import ANTIGRAVITY_CONTINUATION_INSTRUCTION, HandoffService
from coderelay.sync import SyncResult, SyncStatus

runner = CliRunner()


@pytest.fixture
def clean_git_status() -> GitStatus:
    return GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=True,
        ahead=0,
        behind=0,
        staged_files=[],
        unstaged_files=[],
        untracked_files=[],
        conflicted_files=[],
    )


@pytest.fixture
def handoff_git_status() -> GitStatus:
    return GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        ahead=0,
        behind=0,
        staged_files=[],
        unstaged_files=[CONTEXT_FILENAME],
        untracked_files=[],
        conflicted_files=[],
    )


@pytest.fixture
def mock_antigravity_agent() -> MagicMock:
    agent = MagicMock(spec=BaseAgent)
    agent.name = "antigravity"
    agent.display_name = "Antigravity CLI"
    agent.command = "agy"
    agent.is_available.return_value = True
    agent.is_running.return_value = True
    agent.get_pid.return_value = 8888
    agent.get_exit_code.return_value = None
    agent.get_output.return_value = "Antigravity running..."
    agent.stop.side_effect = lambda *a, **kw: setattr(agent.is_running, "return_value", False)
    return agent


@pytest.fixture
def switch_workspace(tmp_path: Path) -> Path:
    """Prepare a temporary workspace with a valid CODERELAY_CONTEXT.md and coderelay.json."""
    ws = tmp_path / "project"
    ws.mkdir(parents=True, exist_ok=True)
    init_context(ws, project_name="SwitchTestProject")

    cfg = {
        "workspace": {"path": str(ws)},
        "primary_agent": {"name": "codex", "command": "codex"},
        "secondary_agent": {"name": "antigravity", "command": "agy"},
        "monitoring": {"interval_seconds": 120, "recommend_threshold": 90.0, "critical_threshold": 95.0},
    }
    cfg_file = ws / "coderelay.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    return ws


def write_cross_process_lockfile(workspace: Path, agent_name: str = "codex", pid: int = 54321) -> None:
    """Simulate another CLI process having started an agent and acquired workspace ownership."""
    lockfile = workspace / LOCK_FILENAME
    payload = {
        "workspace": str(workspace),
        "agent_name": agent_name,
        "pid": pid,
        "acquired_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    lockfile.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# 1. CLI invokes the full HandoffService
# ----------------------------------------------------------------------


def test_cli_switch_invokes_full_handoff_service(switch_workspace: Path):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    with patch("coderelay.cli.Orchestrator") as mock_orch_cls, \
         patch("coderelay.agents.workspace.is_pid_alive", return_value=True):
        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)])

        assert mock_orch_cls.called
        call_kwargs = mock_orch_cls.call_args[1]
        handoff_init = call_kwargs.get("handoff_initiator")
        assert handoff_init is not None
        assert isinstance(handoff_init, HandoffService)
        assert handoff_init.auto_launch_incoming is True
        assert handoff_init.task_completion_handler is not None


# ----------------------------------------------------------------------
# 2. Cross-process PID-only ownership recovery
# ----------------------------------------------------------------------


def test_cli_switch_cross_process_pid_only_ownership(switch_workspace: Path):
    write_cross_process_lockfile(switch_workspace, agent_name="codex", pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    ws_mgr = WorkspaceManager()
    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True):
        owner = ws_mgr.get_active_owner(switch_workspace)
        assert owner is not None
        assert owner.agent_name == "codex"
        assert owner.pid == 54321
        assert owner.agent is None  # Confirmed: external process without in-memory agent object
        assert owner.is_alive() is True


# ----------------------------------------------------------------------
# 3. User confirmation and cancellation
# ----------------------------------------------------------------------


def test_cli_switch_user_cancellation_at_initial_recommendation(switch_workspace: Path):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True):
        # User enters 'n' at the initial switch recommendation
        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="n\n")

        assert result.exit_code == 0
        assert "Switch declined. Active agent remains 'codex'" in result.output
        # Ownership lockfile is preserved
        assert (switch_workspace / LOCK_FILENAME).is_file()


def test_cli_switch_user_cancellation_at_task_completion_check(switch_workspace: Path):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True):
        # User approves switch ('y'), but declines task completion check ('n')
        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\nn\n")

        assert result.exit_code == 1
        assert "OUTGOING AGENT TASK COMPLETION CHECK" in result.output
        assert "Outgoing task completion not confirmed" in result.output
        assert "Switch error" in result.output
        # Codex ownership lockfile must be retained; process was not killed
        assert (switch_workspace / LOCK_FILENAME).is_file()


# ----------------------------------------------------------------------
# 4. Context validation failure
# ----------------------------------------------------------------------


def test_cli_switch_context_validation_failure(switch_workspace: Path):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    # Corrupt context file by removing required sections
    (switch_workspace / CONTEXT_FILENAME).write_text("# Invalid context without sections", encoding="utf-8")

    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace):

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 1
        assert "missing required sections" in result.output
        # Outgoing agent is preserved
        assert (switch_workspace / LOCK_FILENAME).is_file()


# ----------------------------------------------------------------------
# 5. Test runner failure
# ----------------------------------------------------------------------


def test_cli_switch_test_runner_failure(switch_workspace: Path):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace), \
         patch("coderelay.orchestrator.handoff.default_test_runner", return_value=(False, "FAILED tests/test_foo.py::test_bar - assert 1 == 2")):

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 1
        assert "Test suite verification failed during handoff" in result.output
        assert (switch_workspace / LOCK_FILENAME).is_file()


# ----------------------------------------------------------------------
# 6. Git commit/push/remote verification failure
# ----------------------------------------------------------------------


def test_cli_switch_git_push_failure(
    switch_workspace: Path,
    handoff_git_status: GitStatus,
):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace), \
         patch("coderelay.orchestrator.handoff.default_test_runner", return_value=(True, "OK")), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", side_effect=GitError("Could not resolve host: github.com")):

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 1
        assert "Handoff push to remote 'origin/main' failed" in result.output
        assert "Local handoff commit 'abc1234' was retained" in result.output
        # Outgoing process is not terminated on push failure
        assert (switch_workspace / LOCK_FILENAME).is_file()


# ----------------------------------------------------------------------
# 7. Outgoing process termination failure
# ----------------------------------------------------------------------


def test_cli_switch_outgoing_termination_failure(
    switch_workspace: Path,
    handoff_git_status: GitStatus,
):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    # Simulate process that ignores taskkill and remains alive
    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace), \
         patch("coderelay.orchestrator.handoff.default_test_runner", return_value=(True, "OK")), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("subprocess.run") as mock_subproc:

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 1
        assert "failed to terminate within" in result.output
        assert "Workspace ownership is retained" in result.output
        assert (switch_workspace / LOCK_FILENAME).is_file()


# ----------------------------------------------------------------------
# 8. Incoming launch failure
# ----------------------------------------------------------------------


def test_cli_switch_incoming_launch_failure(
    switch_workspace: Path,
    handoff_git_status: GitStatus,
    clean_git_status: GitStatus,
):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    # Codex is alive initially, then dies after taskkill
    alive_status = [True, True, True, False, False, False, False]
    def fake_is_pid_alive(pid):
        if alive_status:
            return alive_status.pop(0)
        return False

    git_statuses = [handoff_git_status, clean_git_status]
    sync_mock = MagicMock()
    sync_mock.synchronize.return_value = SyncResult(SyncStatus.UP_TO_DATE, "Up to date", "main", "abc1234")

    # Antigravity agent fails immediately on start
    failed_agy = MagicMock(spec=BaseAgent)
    failed_agy.name = "antigravity"
    failed_agy.display_name = "Antigravity CLI"
    failed_agy.command = "agy"
    failed_agy.is_available.return_value = True
    failed_agy.is_running.return_value = False
    failed_agy.get_exit_code.return_value = 1
    failed_agy.get_output.return_value = "Fatal: Command failed"

    with patch("coderelay.agents.workspace.is_pid_alive", side_effect=fake_is_pid_alive), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace), \
         patch("coderelay.orchestrator.handoff.default_test_runner", return_value=(True, "OK")), \
         patch("coderelay.orchestrator.handoff.get_git_status", side_effect=lambda ws: git_statuses.pop(0) if git_statuses else clean_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=sync_mock), \
         patch("coderelay.agents.registry.AgentRegistry.get", return_value=failed_agy), \
         patch("subprocess.run"):

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 1
        assert "Incoming agent 'antigravity' terminated immediately after launch" in result.output
        assert "Actionable recovery" in result.output


# ----------------------------------------------------------------------
# 9. Successful Codex-to-Antigravity handoff
# ----------------------------------------------------------------------


def test_cli_switch_successful_codex_to_antigravity_handoff(
    switch_workspace: Path,
    mock_antigravity_agent: MagicMock,
    handoff_git_status: GitStatus,
    clean_git_status: GitStatus,
):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    alive_sequence = [True, True, True, False, False, False, False, False]
    def fake_is_alive(pid):
        if alive_sequence:
            return alive_sequence.pop(0)
        return False

    git_statuses = [handoff_git_status, clean_git_status]
    sync_mock = MagicMock()
    sync_mock.synchronize.return_value = SyncResult(SyncStatus.SYNCHRONIZED, "Synced", "main", "abc1234", "def5678")

    with patch("coderelay.agents.workspace.is_pid_alive", side_effect=fake_is_alive), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace), \
         patch("coderelay.orchestrator.handoff.default_test_runner", return_value=(True, "OK")), \
         patch("coderelay.orchestrator.handoff.get_git_status", side_effect=lambda ws: git_statuses.pop(0) if git_statuses else clean_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=sync_mock), \
         patch("coderelay.agents.registry.AgentRegistry.get", return_value=mock_antigravity_agent), \
         patch("subprocess.run"):

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 0
        assert "[Success] Switched active agent from 'codex' to 'antigravity'." in result.output
        mock_antigravity_agent.start.assert_called_once()
        call_kwargs = mock_antigravity_agent.start.call_args[1]
        assert call_kwargs["workspace"] == switch_workspace.resolve()
        assert call_kwargs["instruction"] == ANTIGRAVITY_CONTINUATION_INSTRUCTION


# ----------------------------------------------------------------------
# 10. No simultaneous workspace ownership
# ----------------------------------------------------------------------


def test_cli_switch_no_simultaneous_workspace_ownership(
    switch_workspace: Path,
    mock_antigravity_agent: MagicMock,
    handoff_git_status: GitStatus,
    clean_git_status: GitStatus,
):
    write_cross_process_lockfile(switch_workspace, pid=54321)
    cfg_file = switch_workspace / "coderelay.json"

    alive_sequence = [True, True, True, False, False, False, False, False]
    def fake_is_alive(pid):
        if alive_sequence:
            return alive_sequence.pop(0)
        return False

    git_statuses = [handoff_git_status, clean_git_status]
    sync_mock = MagicMock()
    sync_mock.synchronize.return_value = SyncResult(SyncStatus.UP_TO_DATE, "Up to date", "main", "abc1234")

    observed_owners = []

    def inspect_ownership(*args, **kwargs):
        ws_mgr = WorkspaceManager()
        owner = ws_mgr.get_active_owner(switch_workspace)
        observed_owners.append(owner.agent_name if owner else None)

    mock_antigravity_agent.start.side_effect = inspect_ownership

    with patch("coderelay.agents.workspace.is_pid_alive", side_effect=fake_is_alive), \
         patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=switch_workspace), \
         patch("coderelay.orchestrator.handoff.default_test_runner", return_value=(True, "OK")), \
         patch("coderelay.orchestrator.handoff.get_git_status", side_effect=lambda ws: git_statuses.pop(0) if git_statuses else clean_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=sync_mock), \
         patch("coderelay.agents.registry.AgentRegistry.get", return_value=mock_antigravity_agent), \
         patch("subprocess.run"):

        result = runner.invoke(app, ["switch", "--to", "antigravity", "-c", str(cfg_file)], input="y\ny\n")

        assert result.exit_code == 0
        # When Antigravity start() was called, Codex had already released ownership:
        assert observed_owners == [None]
