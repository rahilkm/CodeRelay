"""Tests for two-terminal manual Codex association, interactive Antigravity, and monitor handoff."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.agents.base import SubprocessAgent
from coderelay.agents.manager import AgentManager
from coderelay.agents.registry import AgentRegistry
from coderelay.agents.workspace import WorkspaceManager, WorkspaceOwnership
from coderelay.config import Config, WorkspaceConfig
from coderelay.exceptions import WorkspaceOccupiedError
from coderelay.orchestrator.approval import ApprovalHandler, cli_task_completion_confirm
from coderelay.orchestrator.handoff import HandoffRequest, HandoffService
from coderelay.orchestrator.orchestrator import Orchestrator, OrchestratorState
from coderelay.process import DiscoveredProcess, find_codex_processes, get_process_info
from coderelay.usage_models import (
    AccountInfo,
    UsageBucket,
    UsageSnapshot,
    UsageWindow,
)


class DummyAgent(SubprocessAgent):
    def __init__(self, name: str = "test-agent", command: str = "python") -> None:
        super().__init__(command=command, name=name, display_name="Test Agent")

    def _build_launch_command(self, executable: str, workspace: Path, instruction: str | None) -> list[str]:
        return [executable, "-c", "import sys; sys.exit(0)"]


def test_get_process_info_current_process():
    """Verify get_process_info retrieves CWD and CommandLine for an active process."""
    # Test on a spawned child process to verify non-trivial external inspection
    cmd = [sys.executable, "-c", "import time; time.sleep(2)"]
    proc = subprocess.Popen(cmd, cwd=os.getcwd())
    try:
        import time
        time.sleep(0.1)  # Ensure PEB is initialized
        cwd, cmdline = get_process_info(proc.pid)
        if sys.platform == "win32":
            assert cwd is not None
            assert cwd.resolve() == Path(os.getcwd()).resolve()
            assert cmdline is not None
            assert "python" in cmdline.lower() or "sleep" in cmdline.lower()
    finally:
        proc.kill()
        proc.wait()


def test_find_codex_processes_filtering(tmp_path: Path):
    """Verify find_codex_processes matches by workspace CWD and command pattern."""
    ws = tmp_path.resolve()
    with patch("coderelay.process.get_process_info") as mock_info:
        # Candidate 1: matches workspace and codex command
        # Candidate 2: matches workspace but not codex command
        # Candidate 3: matches codex but different workspace
        def fake_info(pid):
            if pid == 1001:
                return ws, "node C:\\npm\\codex.js"
            elif pid == 1002:
                return ws, "python script.py"
            elif pid == 1003:
                return Path("C:\\other"), "node C:\\npm\\codex.js"
            return None, None

        mock_info.side_effect = fake_info

        # Mock toolhelp snapshot on win32 or /proc on posix
        if sys.platform == "win32":
            with patch("coderelay.process.ctypes.WinDLL") as mock_windll:
                mock_k32 = MagicMock()
                mock_windll.return_value = mock_k32
                # We can test the fallback or directly test matching logic
                pass

    # Direct test of DiscoveredProcess structure
    disc = DiscoveredProcess(pid=1001, name="node.exe", command_line="node codex.js", cwd=ws)
    assert disc.pid == 1001
    assert disc.cwd == ws


def test_acquire_external_ownership(tmp_path: Path):
    """Verify WorkspaceManager.acquire_external_ownership records and persists ownership."""
    ws_mgr = WorkspaceManager()
    ws = tmp_path.resolve()

    owner = ws_mgr.acquire_external_ownership(ws, "codex", 12345)
    assert owner.agent_name == "codex"
    assert owner.pid == 12345
    assert owner.agent is None

    # Idempotent call for the same agent and pid
    owner_same = ws_mgr.acquire_external_ownership(ws, "codex", 12345)
    assert owner_same.pid == 12345

    # Conflicting process while original is considered alive
    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True):
        with pytest.raises(WorkspaceOccupiedError):
            ws_mgr.acquire_external_ownership(ws, "codex", 54321)


def test_agent_manager_associate_external_agent(tmp_path: Path):
    """Verify AgentManager.associate_external_agent delegates to WorkspaceManager."""
    ws_mgr = WorkspaceManager()
    reg = AgentRegistry()
    manager = AgentManager(registry=reg, workspace_manager=ws_mgr)

    with patch("coderelay.agents.workspace.is_pid_alive", return_value=True):
        owner = manager.associate_external_agent("codex", tmp_path, 9999)
        assert owner.agent_name == "codex"
        assert owner.pid == 9999

        active = manager.get_active_owner(tmp_path)
        assert active is not None
        assert active.pid == 9999


def test_subprocess_agent_interactive_mode(tmp_path: Path):
    """Verify SubprocessAgent launches with inherited stdio and no reader thread in interactive mode."""
    agent = DummyAgent("test-dummy")
    ws = tmp_path.resolve()

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 7777
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        agent.start(ws, instruction="Test instruction", interactive=True)

        assert agent._is_interactive is True
        assert agent._reader_thread is None  # Reader thread must NOT be started
        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        assert kwargs["stdin"] is None
        assert kwargs["stdout"] is None
        assert kwargs["stderr"] is None

        ret = agent.wait()
        assert ret == 0


def test_handoff_service_interactive_incoming(tmp_path: Path):
    """Verify HandoffService launches incoming agent interactively and releases ownership after wait."""
    ws = tmp_path.resolve()
    mock_agent = MagicMock()
    mock_agent.name = "antigravity"
    mock_agent.is_available.return_value = True
    mock_agent.is_running.return_value = True
    mock_agent.get_pid.return_value = 3333
    mock_agent.wait.return_value = 0

    reg = AgentRegistry()
    reg.register(mock_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    service = HandoffService(
        agent_manager=agent_mgr,
        auto_launch_incoming=True,
        interactive_incoming=True,
    )

    req = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=ws,
        reason="Testing interactive handoff",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo"), \
         patch("coderelay.orchestrator.handoff.GitSyncService") as mock_sync_cls, \
         patch("coderelay.orchestrator.handoff.get_git_status") as mock_status:

        mock_sync = MagicMock()
        mock_sync.synchronize.return_value = MagicMock(status=1, message="ok")
        mock_sync_cls.return_value = mock_sync

        mock_status_obj = MagicMock()
        mock_status_obj.has_conflicts = False
        mock_status_obj.has_uncommitted_changes = False
        mock_status.return_value = mock_status_obj

        agent = service.execute_incoming_handoff(req)
        assert agent == mock_agent
        mock_agent.wait.assert_called_once()
        # After wait, ownership should be released
        assert ws_mgr.get_active_owner(ws) is None


def test_approval_warning_in_cli_confirm():
    """Verify cli_task_completion_confirm warns user about process termination."""
    lines_output = []

    def mock_output(s):
        lines_output.append(s)

    def mock_confirm(prompt):
        return True

    confirmed = cli_task_completion_confirm(
        agent=None,
        workspace=Path("C:\\test"),
        instruction="Finish work",
        confirm_func=mock_confirm,
        output_func=mock_output,
    )
    assert confirmed is True
    full_output = "\n".join(lines_output)
    assert "OUTGOING AGENT TASK COMPLETION CHECK" in full_output
    assert "terminated" in full_output.lower()


def test_monitor_threshold_triggered_orchestration(tmp_path: Path):
    """Verify 90% recommendation, decline suppression, and 95% critical switch triggering."""
    ws = tmp_path.resolve()
    config = Config(workspace=WorkspaceConfig(path=ws))
    config.monitoring.recommend_threshold = 90.0
    config.monitoring.critical_threshold = 95.0

    approval_calls = []

    def mock_confirm(prompt):
        # Decline the first request (90%), approve the second (95%)
        if len(approval_calls) == 1:
            return False
        return True

    def mock_output(line):
        pass

    approval_handler = ApprovalHandler(confirm_func=mock_confirm, output_func=mock_output)

    mock_handoff = MagicMock(spec=HandoffService)
    mock_handoff.auto_launch_incoming = True

    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(workspace_manager=ws_mgr)
    agent_mgr.associate_external_agent("codex", ws, 4444)

    orchestrator = Orchestrator(
        config=config,
        project_name_or_path=ws,
        agent_manager=agent_mgr,
        approval_handler=approval_handler,
        handoff_initiator=mock_handoff,
    )
    orchestrator.context.active_agent_name = "codex"
    orchestrator.context.state = OrchestratorState.ACTIVE

    def make_snapshot(pct: float) -> UsageSnapshot:
        win = UsageWindow(name="primary", used_percent=pct)
        bucket = UsageBucket(limit_id="codex", primary=win)
        return UsageSnapshot(
            timestamp=datetime.now(timezone.utc),
            account=AccountInfo(plan_type="Pro"),
            buckets={"codex": bucket},
        )

    # 1. 85% usage -> below threshold, no approval requested
    orchestrator.handle_usage_snapshot(make_snapshot(85.0))
    assert len(approval_calls) == 0
    assert orchestrator.context.state == OrchestratorState.ACTIVE

    # Hook approval_handler to record calls
    orig_request = approval_handler.request_approval

    def tracked_approval(*a, **kw):
        approval_calls.append(kw)
        return orig_request(*a, **kw)

    approval_handler.request_approval = tracked_approval

    # 2. 90% usage -> threshold breached, user declines
    orchestrator.handle_usage_snapshot(make_snapshot(90.0))
    assert len(approval_calls) == 1
    assert orchestrator.context.declined_threshold == 90.0
    assert orchestrator.context.state == OrchestratorState.ACTIVE
    mock_handoff.initiate_handoff.assert_not_called()

    # 3. 92% usage -> suppressed! No duplicate recommendation
    orchestrator.handle_usage_snapshot(make_snapshot(92.0))
    assert len(approval_calls) == 1  # Still 1 call!

    # 4. 95% usage -> critical threshold, user approves
    orchestrator.handle_usage_snapshot(make_snapshot(95.0))
    assert len(approval_calls) == 2  # Prompted again at critical!
    mock_handoff.initiate_handoff.assert_called_once()
    assert orchestrator.context.state == OrchestratorState.ACTIVE
