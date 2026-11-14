"""Tests for Agent integration layer (Phase 3)."""

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.agents.antigravity import AntigravityAgent
from coderelay.agents.base import SubprocessAgent
from coderelay.agents.codex import CodexAgent
from coderelay.agents.manager import AgentManager
from coderelay.agents.registry import AgentRegistry
from coderelay.agents.workspace import WorkspaceManager, WorkspaceOwnership
from coderelay.exceptions import (
    AgentNotAvailableError,
    AgentNotFoundError,
    AgentStartError,
    WorkspaceOccupiedError,
)


def test_agent_discovery_codex():
    with patch("shutil.which", return_value="C:\\fake\\codex.cmd"):
        agent = CodexAgent()
        assert agent.is_available() is True
        assert agent.get_executable_path() == "C:\\fake\\codex.cmd"


def test_agent_discovery_antigravity():
    with patch("shutil.which", return_value="C:\\fake\\agy.exe"):
        agent = AntigravityAgent()
        assert agent.is_available() is True
        assert agent.get_executable_path() == "C:\\fake\\agy.exe"


def test_agent_missing_executable():
    with patch("shutil.which", return_value=None):
        agent = CodexAgent()
        assert agent.is_available() is False
        assert agent.get_executable_path() is None

        with pytest.raises(AgentNotAvailableError, match="not found in PATH"):
            agent.start(Path("."))


def test_codex_build_launch_command(tmp_path: Path):
    agent = CodexAgent()

    # Without instruction
    cmd_no_instr = agent._build_launch_command("C:\\bin\\codex.cmd", tmp_path, None)
    assert "-C" in cmd_no_instr
    assert str(tmp_path) in cmd_no_instr

    # With instruction
    cmd_with_instr = agent._build_launch_command("C:\\bin\\codex.cmd", tmp_path, "Refactor models")
    assert "Refactor models" in cmd_with_instr
    assert "-C" in cmd_with_instr


def test_antigravity_build_launch_command(tmp_path: Path):
    agent = AntigravityAgent()

    # Without instruction
    cmd_no_instr = agent._build_launch_command("C:\\bin\\agy.exe", tmp_path, None)
    assert "--prompt-interactive" not in cmd_no_instr

    # With instruction
    cmd_with_instr = agent._build_launch_command("C:\\bin\\agy.exe", tmp_path, "Implement auth")
    assert "--prompt-interactive" in cmd_with_instr
    assert "Implement auth" in cmd_with_instr


def test_agent_startup_success(tmp_path: Path):
    agent = CodexAgent()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 4321
    mock_proc.stdout = io.StringIO("Line 1\nLine 2\n")

    with patch.object(agent, "get_executable_path", return_value="C:\\fake\\codex.cmd"), \
         patch("subprocess.Popen", return_value=mock_proc) as mock_popen:

        agent.start(tmp_path, instruction="Run tests")

        assert agent.is_running() is True
        assert agent.get_pid() == 4321
        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == str(tmp_path)

        # Output capture
        if agent._reader_thread:
            agent._reader_thread.join(timeout=1.0)
        assert "Line 1" in agent.get_output()

        agent.stop()
        assert agent.is_running() is False


def test_agent_startup_immediate_exit(tmp_path: Path):
    agent = CodexAgent()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    mock_proc.returncode = 1

    with patch.object(agent, "get_executable_path", return_value="C:\\fake\\codex.cmd"), \
         patch("subprocess.Popen", return_value=mock_proc):

        with pytest.raises(AgentStartError, match="terminated immediately"):
            agent.start(tmp_path)


def test_agent_startup_missing_workspace(tmp_path: Path):
    agent = CodexAgent()
    with patch.object(agent, "get_executable_path", return_value="C:\\fake\\codex.cmd"):
        non_existent = tmp_path / "does_not_exist"
        with pytest.raises(AgentStartError, match="does not exist"):
            agent.start(non_existent)


def test_agent_already_running_raises(tmp_path: Path):
    agent = CodexAgent()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 1111
    mock_proc.stdout = io.StringIO("")

    with patch.object(agent, "get_executable_path", return_value="C:\\fake\\codex.cmd"), \
         patch("subprocess.Popen", return_value=mock_proc):

        agent.start(tmp_path)
        with pytest.raises(AgentStartError, match="already running"):
            agent.start(tmp_path)

        agent.stop()


def test_workspace_ownership_lifecycle(tmp_path: Path):
    ws_mgr = WorkspaceManager()
    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.get_pid.return_value = 999
    mock_agent.is_running.return_value = True

    # Initial state: unoccupied
    assert ws_mgr.get_active_owner(tmp_path) is None

    # Acquire ownership
    owner = ws_mgr.acquire_ownership(tmp_path, mock_agent)
    assert owner.agent_name == "codex"
    assert owner.pid == 999

    # Inspect active owner
    active = ws_mgr.get_active_owner(tmp_path)
    assert active is not None
    assert active.agent_name == "codex"

    # Concurrent acquisition fails
    mock_agent2 = MagicMock()
    mock_agent2.name = "antigravity"
    with pytest.raises(WorkspaceOccupiedError, match="already occupied by agent 'codex'"):
        ws_mgr.acquire_ownership(tmp_path, mock_agent2)

    # Release ownership
    ws_mgr.release_ownership(tmp_path)
    assert ws_mgr.get_active_owner(tmp_path) is None


def test_workspace_ownership_stale_cleanup_on_agent_exit(tmp_path: Path):
    ws_mgr = WorkspaceManager()
    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.get_pid.return_value = 999
    mock_agent.is_running.return_value = True

    ws_mgr.acquire_ownership(tmp_path, mock_agent)
    assert ws_mgr.get_active_owner(tmp_path) is not None

    # Agent unexpectedly terminates
    mock_agent.is_running.return_value = False
    assert ws_mgr.get_active_owner(tmp_path) is None

    # Now workspace is immediately re-acquirable
    mock_agent2 = MagicMock()
    mock_agent2.name = "antigravity"
    mock_agent2.get_pid.return_value = 1000
    mock_agent2.is_running.return_value = True

    owner2 = ws_mgr.acquire_ownership(tmp_path, mock_agent2)
    assert owner2.agent_name == "antigravity"


def test_agent_registry_lookup():
    reg = AgentRegistry()
    codex = CodexAgent()
    agy = AntigravityAgent()
    reg.register(codex)
    reg.register(agy)

    assert reg.get("codex") is codex
    assert reg.get("CODEX") is codex
    assert reg.get("antigravity") is agy
    assert reg.get("agy") is agy  # Alias

    with pytest.raises(AgentNotFoundError, match="unknown"):
        reg.get("non_existent_agent")


def test_agent_manager_start_and_stop(tmp_path: Path):
    ws_mgr = WorkspaceManager()
    reg = AgentRegistry()
    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.display_name = "Codex CLI"
    mock_agent.command = "codex"
    mock_agent.is_available.return_value = True
    mock_agent.is_running.return_value = True
    mock_agent.stop.side_effect = lambda *a, **kw: setattr(mock_agent.is_running, "return_value", False)
    mock_agent.get_pid.return_value = 8888
    mock_agent.get_executable_path.return_value = "C:\\fake\\codex.cmd"
    reg.register(mock_agent)

    manager = AgentManager(registry=reg, workspace_manager=ws_mgr)

    # Start agent
    agent = manager.start_agent("codex", tmp_path, instruction="Plan next feature")
    assert agent is mock_agent
    mock_agent.start.assert_called_once_with(workspace=tmp_path.resolve(), instruction="Plan next feature")

    # Status shows occupied
    status = manager.get_status(tmp_path)
    assert status["has_active_agent"] is True
    assert status["active_agent_name"] == "codex"

    # Stop agent
    stopped = manager.stop_agent(tmp_path)
    assert stopped is True
    mock_agent.stop.assert_called_once()
    assert manager.get_active_owner(tmp_path) is None

    # Second stop returns False
    assert manager.stop_agent(tmp_path) is False
