"""Tests for Phase 5B Outgoing Agent Handoff Execution Service & Safety Audits."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.agents.base import BaseAgent
from coderelay.agents.manager import AgentManager
from coderelay.agents.workspace import WorkspaceManager, WorkspaceOwnership
from coderelay.config import Config, MonitoringConfig, WorkspaceConfig
from coderelay.context import CONTEXT_FILENAME, init_context
from coderelay.exceptions import (
    GitError,
    HandoffCommitError,
    HandoffContextError,
    HandoffError,
    HandoffGitStateError,
    HandoffProcessShutdownError,
    HandoffPushError,
    HandoffSyncError,
    HandoffTaskCompletionError,
    HandoffTestFailureError,
    HandoffVerificationError,
    IncomingAgentLaunchError,
    IncomingHandoffError,
    WorkspaceOccupiedError,
    WorkspaceOwnershipError,
)
from coderelay.git import GitResult, GitStatus
from coderelay.orchestrator import (
    ANTIGRAVITY_CONTINUATION_INSTRUCTION,
    ApprovalHandler,
    HandoffRequest,
    HandoffService,
    Orchestrator,
    OrchestratorState,
)
from coderelay.sync import GitSyncService, SyncResult, SyncStatus


@pytest.fixture
def mock_workspace(tmp_path: Path) -> Path:
    """Prepare a temporary workspace with a valid CODERELAY_CONTEXT.md."""
    init_context(tmp_path, project_name="HandoffTestProject")
    return tmp_path


@pytest.fixture
def mock_agent() -> MagicMock:
    """Create a mock outgoing agent."""
    agent = MagicMock(spec=BaseAgent)
    agent.name = "codex"
    agent.display_name = "Codex CLI"
    agent.is_available.return_value = True
    agent.is_running.return_value = True
    agent.get_pid.return_value = 4242
    agent.send_input.return_value = True

    def fake_stop(timeout=3.0):
        agent.is_running.return_value = False

    agent.stop.side_effect = fake_stop
    return agent


@pytest.fixture
def mock_agent_manager(mock_agent: MagicMock, mock_workspace: Path) -> MagicMock:
    """Create a mock AgentManager with an active owner."""
    manager = MagicMock(spec=AgentManager)
    owner = MagicMock(spec=WorkspaceOwnership)
    owner.agent_name = "codex"
    owner.pid = 4242
    owner.agent = mock_agent
    owner.is_alive.return_value = True

    manager.get_active_agent.return_value = mock_agent
    manager.get_active_owner.return_value = owner

    def fake_stop_agent(ws, timeout=5.0):
        mock_agent.is_running.return_value = False
        owner.is_alive.return_value = False
        manager.get_active_owner.return_value = None
        manager.get_active_agent.return_value = None
        return True

    manager.stop_agent.side_effect = fake_stop_agent
    return manager


@pytest.fixture
def handoff_git_status() -> GitStatus:
    """Status where only the handoff context file has been modified."""
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


# ----------------------------------------------------------------------
# 1. Successful Handoff
# ----------------------------------------------------------------------


def test_successful_handoff(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_agent_manager: MagicMock,
    handoff_git_status: GitStatus,
):
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda agent, ws, instr: True,
        test_runner=lambda ws: (True, "All 42 tests passed"),
        remote="origin",
        branch="main",
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Account usage reached 90.0%",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc12345678")):

        result = service.initiate_handoff(request)

        assert result.status == "completed"
        assert result.commit_hash == "abc12345678"
        assert result.error is None
        mock_agent_manager.stop_agent.assert_called_once()
        assert mock_agent.is_running() is False


# ----------------------------------------------------------------------
# 2. Agent Communication Audits
# ----------------------------------------------------------------------


def test_unsupported_codex_communication_fails_safely(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_agent_manager: MagicMock,
):
    # Running agent without a verified task_completion_handler
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=None,
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with pytest.raises(HandoffTaskCompletionError, match="Cannot establish reliable communication"):
        service.initiate_handoff(request)

    assert request.status == "failed"
    assert "Handoff requires a supported communication handler" in request.error


def test_declined_or_failed_task_completion(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_agent_manager: MagicMock,
):
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda agent, ws, instr: False,  # Agent declined
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with pytest.raises(HandoffTaskCompletionError, match="failed or declined task completion"):
        service.initiate_handoff(request)

    assert request.status == "failed"
    assert "declined task completion" in request.error


# ----------------------------------------------------------------------
# 3. Context Update Failures
# ----------------------------------------------------------------------


def test_context_update_failure_missing_file(tmp_path: Path):
    empty_ws = tmp_path / "empty_workspace"
    empty_ws.mkdir()
    service = HandoffService(test_runner=lambda ws: (True, "OK"))

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=empty_ws,
        reason="Handoff requested",
    )

    with pytest.raises(HandoffContextError, match="missing in workspace"):
        service.initiate_handoff(request)

    assert request.status == "failed"


def test_context_update_failure_invalid_sections(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    context_file = mock_workspace / CONTEXT_FILENAME
    context_file.write_text("# Invalid Context\n\nNo required sections here.", encoding="utf-8")

    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with pytest.raises(HandoffContextError, match="missing required sections"):
        service.initiate_handoff(request)

    assert request.status == "failed"


# ----------------------------------------------------------------------
# 4. Test Suite Failure
# ----------------------------------------------------------------------


def test_test_failure(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (False, "FAILED tests/test_calc.py::test_add - AssertionError"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with pytest.raises(HandoffTestFailureError, match="Test suite verification failed"):
        service.initiate_handoff(request)

    assert request.status == "failed"
    assert "AssertionError" in request.error


# ----------------------------------------------------------------------
# 5. Git Isolation & Security Tests
# ----------------------------------------------------------------------


def test_git_state_merge_conflicts(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    conflict_status = GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        conflicted_files=["auth.py", "main.py"],
    )

    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=conflict_status):

        with pytest.raises(HandoffGitStateError, match="merge conflicts exist"):
            service.initiate_handoff(request)

    assert request.status == "failed"


def test_git_state_protected_file_modified(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    protected_dirty_status = GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        staged_files=[],
        unstaged_files=[CONTEXT_FILENAME, "coderelay.json"],  # Protected local config!
    )

    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=protected_dirty_status):

        with pytest.raises(HandoffGitStateError, match="protected sensitive file is modified"):
            service.initiate_handoff(request)

    assert request.status == "failed"


def test_pre_existing_staged_changes_rejected(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    # User had pre-existing staged files
    staged_status = GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        staged_files=["user_feature.py"],
        unstaged_files=[CONTEXT_FILENAME],
    )

    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=staged_status):

        with pytest.raises(HandoffGitStateError, match="pre-existing staged changes"):
            service.initiate_handoff(request)

    assert request.status == "failed"


def test_pre_existing_unstaged_user_changes_rejected(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    # User has uncommitted modifications in user_code.py alongside context file
    unstaged_status = GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        staged_files=[],
        unstaged_files=[CONTEXT_FILENAME, "user_code.py"],
    )

    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=unstaged_status):

        with pytest.raises(HandoffGitStateError, match="uncommitted user modifications in user_code.py"):
            service.initiate_handoff(request)

    assert request.status == "failed"


def test_safe_staging_of_handoff_generated_changes_only(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
):
    # Only CONTEXT_FILENAME and an explicit handoff output file are present
    status = GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        staged_files=[],
        unstaged_files=[CONTEXT_FILENAME, "handoff_notes.md"],
    )

    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
        handoff_files=[CONTEXT_FILENAME, "handoff_notes.md"],
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=status), \
         patch("coderelay.orchestrator.handoff.commit_changes") as mock_commit, \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True):

        mock_commit.return_value = GitResult(True, ["commit"])
        service.initiate_handoff(request)

        # Verified: Only designated handoff files were staged and committed
        mock_commit.assert_called_once()
        staged_arg = mock_commit.call_args[1]["files"]
        assert set(staged_arg) == {CONTEXT_FILENAME, "handoff_notes.md"}


# ----------------------------------------------------------------------
# 6. Commit & Push Failures (Commit Retention)
# ----------------------------------------------------------------------


def test_commit_failure(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
    handoff_git_status: GitStatus,
):
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", side_effect=GitError("GPG signing failed")):

        with pytest.raises(HandoffCommitError, match="Handoff commit failed"):
            service.initiate_handoff(request)

    assert request.status == "failed"


def test_push_failure_retains_local_commit(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
    handoff_git_status: GitStatus,
):
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="commit_hash_999")), \
         patch("coderelay.orchestrator.handoff.push_changes", side_effect=GitError("Could not resolve host")):

        with pytest.raises(HandoffPushError) as exc_info:
            service.initiate_handoff(request)

        # Crucial safety check: Local commit was NOT discarded/reset; hash and recovery state are reported
        assert "commit_hash_999" in str(exc_info.value)
        assert "Local handoff commit 'commit_hash_999' was retained" in str(exc_info.value)
        assert request.commit_hash == "commit_hash_999"
        assert request.status == "failed"


def test_remote_commit_verification_failure_retains_commit(
    mock_workspace: Path,
    mock_agent_manager: MagicMock,
    handoff_git_status: GitStatus,
):
    service = HandoffService(
        agent_manager=mock_agent_manager,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="commit_hash_777")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=False):

        with pytest.raises(HandoffVerificationError, match="Local commit 'commit_hash_777' was retained"):
            service.initiate_handoff(request)

    assert request.commit_hash == "commit_hash_777"
    assert request.status == "failed"


# ----------------------------------------------------------------------
# 7. Workspace Ownership and Process Shutdown Safety
# ----------------------------------------------------------------------


def test_process_termination_timeout_retains_ownership(
    mock_workspace: Path,
    mock_agent: MagicMock,
    handoff_git_status: GitStatus,
):
    # Agent process hangs / refuses to stop
    mock_agent.is_running.return_value = True
    mock_agent.stop.side_effect = None

    ws_mgr = WorkspaceManager()
    ws_mgr.acquire_ownership(mock_workspace, mock_agent)
    agent_mgr = AgentManager(workspace_manager=ws_mgr)

    service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
        shutdown_timeout=0.1,
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True):

        with pytest.raises(HandoffProcessShutdownError, match="failed to terminate within 0.1s timeout"):
            service.initiate_handoff(request)

    # Ownership was retained because termination failed!
    active_owner = ws_mgr.get_active_owner(mock_workspace)
    assert active_owner is not None
    assert active_owner.agent_name == "codex"


def test_rejection_of_second_agent_while_first_process_alive(
    mock_workspace: Path,
    mock_agent: MagicMock,
    handoff_git_status: GitStatus,
):
    mock_agent.is_running.return_value = True
    mock_agent.stop.side_effect = None

    ws_mgr = WorkspaceManager()
    ws_mgr.acquire_ownership(mock_workspace, mock_agent)
    agent_mgr = AgentManager(workspace_manager=ws_mgr)

    service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
        shutdown_timeout=0.1,
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True):

        with pytest.raises(HandoffProcessShutdownError):
            service.initiate_handoff(request)

    # Another agent attempts to acquire ownership: rejected with WorkspaceOccupiedError!
    second_agent = MagicMock(spec=BaseAgent)
    second_agent.name = "antigravity"
    with pytest.raises(WorkspaceOccupiedError, match="already occupied by agent 'codex'"):
        ws_mgr.acquire_ownership(mock_workspace, second_agent)


def test_successful_ownership_release_after_confirmed_termination(
    mock_workspace: Path,
    mock_agent: MagicMock,
    handoff_git_status: GitStatus,
):
    # Agent stops cleanly when requested
    mock_agent.is_running.return_value = True

    def clean_stop(timeout=3.0):
        mock_agent.is_running.return_value = False

    mock_agent.stop.side_effect = clean_stop

    ws_mgr = WorkspaceManager()
    ws_mgr.acquire_ownership(mock_workspace, mock_agent)
    agent_mgr = AgentManager(workspace_manager=ws_mgr)

    service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Handoff requested",
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True):

        service.initiate_handoff(request)

    # Ownership confirmed released
    assert ws_mgr.get_active_owner(mock_workspace) is None

    # New agent can now acquire the workspace without errors
    second_agent = MagicMock(spec=BaseAgent)
    second_agent.name = "antigravity"
    second_agent.get_pid.return_value = 5555
    ownership = ws_mgr.acquire_ownership(mock_workspace, second_agent)
    assert ownership.agent_name == "antigravity"


# ----------------------------------------------------------------------
# 8. Orchestrator Integration & Error Recovery
# ----------------------------------------------------------------------


def test_orchestrator_integration_phase5b_does_not_launch_antigravity(
    mock_workspace: Path,
    mock_agent: MagicMock,
    handoff_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    cfg = Config(
        workspace=WorkspaceConfig(path=mock_workspace),
        monitoring=MonitoringConfig(interval_seconds=120, recommend_threshold=90.0, critical_threshold=95.0),
    )

    reg = AgentRegistry()
    agy_agent = MagicMock(spec=BaseAgent)
    agy_agent.name = "antigravity"
    agy_agent.display_name = "Antigravity CLI"
    agy_agent.is_available.return_value = True
    agy_agent.is_running.return_value = False

    reg.register(mock_agent)
    reg.register(agy_agent)

    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)
    agent_mgr.workspace_manager.acquire_ownership(mock_workspace, mock_agent)

    handoff_service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    approval = ApprovalHandler(confirm_func=lambda prompt: True, output_func=lambda msg: None)

    orch = Orchestrator(
        config=cfg,
        project_name_or_path=mock_workspace,
        agent_manager=agent_mgr,
        approval_handler=approval,
        handoff_initiator=handoff_service,
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True):

        decision = orch.request_manual_switch(target_agent="antigravity")

        assert decision.approved is True
        assert orch.context.state == OrchestratorState.HANDOFF_INITIATED
        assert mock_agent.is_running() is False
        agy_agent.start.assert_not_called()
        assert orch.context.active_agent_name is None


def test_orchestrator_error_recovery_maintains_active_state_when_process_alive(
    mock_workspace: Path,
    mock_agent: MagicMock,
    handoff_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    cfg = Config(
        workspace=WorkspaceConfig(path=mock_workspace),
        monitoring=MonitoringConfig(interval_seconds=120, recommend_threshold=90.0, critical_threshold=95.0),
    )

    reg = AgentRegistry()
    mock_agent.is_running.return_value = True
    reg.register(mock_agent)

    agy_agent = MagicMock(spec=BaseAgent)
    agy_agent.name = "antigravity"
    agy_agent.display_name = "Antigravity CLI"
    agy_agent.is_available.return_value = True
    reg.register(agy_agent)

    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)
    agent_mgr.workspace_manager.acquire_ownership(mock_workspace, mock_agent)

    # Failing handoff service (push fails)
    handoff_service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "OK"),
    )

    approval = ApprovalHandler(confirm_func=lambda prompt: True, output_func=lambda msg: None)

    orch = Orchestrator(
        config=cfg,
        project_name_or_path=mock_workspace,
        agent_manager=agent_mgr,
        approval_handler=approval,
        handoff_initiator=handoff_service,
    )

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=handoff_git_status), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"])), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.push_changes", side_effect=GitError("Network down")):

        with pytest.raises(HandoffPushError):
            orch.request_manual_switch(target_agent="antigravity")

        # Verified: State correctly reflects active agent and occupied workspace, not an available/idle state
        assert orch.context.state == OrchestratorState.ACTIVE
        assert orch.context.active_agent_name == "codex"
        assert ws_mgr.get_active_owner(mock_workspace) is not None


# ----------------------------------------------------------------------
# Phase 5C Incoming Agent Handoff Tests
# ----------------------------------------------------------------------


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
def mock_antigravity_agent() -> MagicMock:
    agent = MagicMock(spec=BaseAgent)
    agent.name = "antigravity"
    agent.display_name = "Antigravity CLI"
    agent.is_available.return_value = True
    agent.is_running.return_value = True
    agent.get_pid.return_value = 9999
    agent.get_exit_code.return_value = None
    agent.get_output.return_value = "Antigravity session running..."

    def fake_stop(timeout=3.0):
        agent.is_running.return_value = False

    agent.stop.side_effect = fake_stop
    return agent


def test_incoming_handoff_success(
    mock_workspace: Path,
    mock_antigravity_agent: MagicMock,
    clean_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    service = HandoffService(
        agent_manager=agent_mgr,
        remote="origin",
        branch="main",
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Account usage reached 90.0%",
    )

    sync_result = SyncResult(
        status=SyncStatus.SYNCHRONIZED,
        message="Fast-forwarded to origin/main",
        current_branch="main",
        initial_commit="abc1234",
        synced_commit="def5678",
    )

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = sync_result

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=clean_git_status):

        launched_agent = service.execute_incoming_handoff(request)

        assert launched_agent == mock_antigravity_agent
        assert request.status == "completed"
        assert request.incoming_pid == 9999
        assert request.error is None
        mock_antigravity_agent.start.assert_called_once_with(
            workspace=mock_workspace.resolve(),
            instruction=ANTIGRAVITY_CONTINUATION_INSTRUCTION,
        )
        active_owner = agent_mgr.get_active_owner(mock_workspace)
        assert active_owner is not None
        assert active_owner.agent_name == "antigravity"
        assert active_owner.pid == 9999


def test_incoming_handoff_outgoing_agent_still_running_raises(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_antigravity_agent: MagicMock,
):
    from coderelay.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.register(mock_agent)
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    # Codex is still alive and occupies the workspace
    mock_agent.is_running.return_value = True
    agent_mgr.workspace_manager.acquire_ownership(mock_workspace, mock_agent)

    service = HandoffService(agent_manager=agent_mgr)
    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Account usage switch",
    )

    with pytest.raises(WorkspaceOwnershipError) as exc_info:
        service.execute_incoming_handoff(request)

    assert "outgoing agent 'codex'" in str(exc_info.value)
    assert "Simultaneous agent execution is strictly prohibited" in str(exc_info.value)
    mock_antigravity_agent.start.assert_not_called()
    assert request.status == "failed"


def test_incoming_handoff_git_sync_diverged_raises(
    mock_workspace: Path,
    mock_antigravity_agent: MagicMock,
):
    from coderelay.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    service = HandoffService(agent_manager=agent_mgr)
    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Switch",
    )

    diverged_result = SyncResult(
        status=SyncStatus.DIVERGED,
        message="Branch 'main' has diverged from 'origin/main'",
        current_branch="main",
        initial_commit="abc1234",
    )

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = diverged_result

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance):

        with pytest.raises(HandoffSyncError) as exc_info:
            service.execute_incoming_handoff(request)

        assert "branches have diverged" in str(exc_info.value)
        mock_antigravity_agent.start.assert_not_called()
        assert request.status == "failed"


def test_incoming_handoff_dirty_workspace_after_sync_raises(
    mock_workspace: Path,
    mock_antigravity_agent: MagicMock,
):
    from coderelay.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    service = HandoffService(agent_manager=agent_mgr)
    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Switch",
    )

    up_to_date = SyncResult(
        status=SyncStatus.UP_TO_DATE,
        message="Up to date",
        current_branch="main",
        initial_commit="abc1234",
    )

    dirty_status = GitStatus(
        current_branch="main",
        tracking_branch="origin/main",
        is_clean=False,
        ahead=0,
        behind=0,
        staged_files=["unexpected.py"],
        unstaged_files=[],
        untracked_files=[],
        conflicted_files=[],
    )

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = up_to_date

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=dirty_status):

        with pytest.raises(HandoffGitStateError) as exc_info:
            service.execute_incoming_handoff(request)

        assert "Unexpected uncommitted changes detected" in str(exc_info.value)
        mock_antigravity_agent.start.assert_not_called()
        assert request.status == "failed"


def test_incoming_handoff_antigravity_immediate_exit_raises(
    mock_workspace: Path,
    mock_antigravity_agent: MagicMock,
    clean_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    # Simulate agent process crashing or exiting immediately after start
    mock_antigravity_agent.is_running.return_value = False
    mock_antigravity_agent.get_exit_code.return_value = 1
    mock_antigravity_agent.get_output.return_value = "Fatal: Command not supported"

    reg = AgentRegistry()
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    service = HandoffService(agent_manager=agent_mgr)
    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Switch",
    )

    up_to_date = SyncResult(
        status=SyncStatus.UP_TO_DATE,
        message="Up to date",
        current_branch="main",
        initial_commit="abc1234",
    )

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = up_to_date

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=clean_git_status):

        with pytest.raises(IncomingAgentLaunchError) as exc_info:
            service.execute_incoming_handoff(request)

        assert "terminated immediately after launch" in str(exc_info.value)
        assert "exit code: 1" in str(exc_info.value)
        # Lockfile must be cleared on immediate exit
        assert agent_mgr.get_active_owner(mock_workspace) is None
        assert request.status == "failed"


def test_incoming_handoff_continuation_instruction_content():
    """Verify that continuation instruction strictly requires inspecting context, git state, and continuing next pending task."""
    assert "CODERELAY_CONTEXT.md" in ANTIGRAVITY_CONTINUATION_INSTRUCTION
    assert "git status" in ANTIGRAVITY_CONTINUATION_INSTRUCTION
    assert "git log -n 3" in ANTIGRAVITY_CONTINUATION_INSTRUCTION
    assert "pending" in ANTIGRAVITY_CONTINUATION_INSTRUCTION.lower()


def test_incoming_handoff_prevents_simultaneous_execution(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_antigravity_agent: MagicMock,
    clean_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.register(mock_agent)
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    service = HandoffService(agent_manager=agent_mgr)
    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Switch",
    )

    up_to_date = SyncResult(
        status=SyncStatus.UP_TO_DATE,
        message="Up to date",
        current_branch="main",
        initial_commit="abc1234",
    )

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = up_to_date

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance), \
         patch("coderelay.orchestrator.handoff.get_git_status", return_value=clean_git_status):

        service.execute_incoming_handoff(request)

        # Now Antigravity owns the workspace. Attempting to start Codex must be rejected.
        with pytest.raises(WorkspaceOccupiedError, match="currently occupied by active agent 'antigravity'"):
            agent_mgr.start_agent("codex", mock_workspace)


def test_full_handoff_execution_end_to_end(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_antigravity_agent: MagicMock,
    handoff_git_status: GitStatus,
    clean_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    reg = AgentRegistry()
    reg.register(mock_agent)
    reg.register(mock_antigravity_agent)
    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)

    # Start with Codex owning workspace
    agent_mgr.workspace_manager.acquire_ownership(mock_workspace, mock_agent)

    service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "All tests passed"),
        auto_launch_incoming=True,
    )

    request = HandoffRequest(
        from_agent="codex",
        to_agent="antigravity",
        workspace_path=mock_workspace,
        reason="Account usage reached 90%",
    )

    up_to_date = SyncResult(
        status=SyncStatus.UP_TO_DATE,
        message="Up to date",
        current_branch="main",
        initial_commit="abc1234",
    )

    # During Phase 5B git status has modified context file; during Phase 5C after commit/sync it is clean
    git_status_mock = MagicMock(side_effect=[handoff_git_status, clean_git_status])

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = up_to_date

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", git_status_mock), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance):

        result = service.initiate_handoff(request)

        assert result.status == "completed"
        assert result.commit_hash == "abc1234"
        assert result.incoming_pid == 9999
        assert mock_agent.is_running() is False
        assert mock_antigravity_agent.is_running() is True
        owner = agent_mgr.get_active_owner(mock_workspace)
        assert owner is not None
        assert owner.agent_name == "antigravity"


def test_orchestrator_integration_full_switch_to_antigravity(
    mock_workspace: Path,
    mock_agent: MagicMock,
    mock_antigravity_agent: MagicMock,
    handoff_git_status: GitStatus,
    clean_git_status: GitStatus,
):
    from coderelay.agents.registry import AgentRegistry

    cfg = Config(
        workspace=WorkspaceConfig(path=mock_workspace),
        monitoring=MonitoringConfig(interval_seconds=120, recommend_threshold=90.0, critical_threshold=95.0),
    )

    reg = AgentRegistry()
    reg.register(mock_agent)
    reg.register(mock_antigravity_agent)

    ws_mgr = WorkspaceManager()
    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)
    agent_mgr.workspace_manager.acquire_ownership(mock_workspace, mock_agent)

    handoff_service = HandoffService(
        agent_manager=agent_mgr,
        task_completion_handler=lambda a, w, i: True,
        test_runner=lambda ws: (True, "All tests passed"),
        auto_launch_incoming=True,
    )

    approval = ApprovalHandler(confirm_func=lambda prompt: True, output_func=lambda msg: None)

    orch = Orchestrator(
        config=cfg,
        project_name_or_path=mock_workspace,
        agent_manager=agent_mgr,
        approval_handler=approval,
        handoff_initiator=handoff_service,
    )

    up_to_date = SyncResult(
        status=SyncStatus.UP_TO_DATE,
        message="Up to date",
        current_branch="main",
        initial_commit="abc1234",
    )

    git_status_mock = MagicMock(side_effect=[handoff_git_status, clean_git_status])

    mock_sync_instance = MagicMock()
    mock_sync_instance.synchronize.return_value = up_to_date

    with patch("coderelay.orchestrator.handoff.ensure_git_repo", return_value=mock_workspace), \
         patch("coderelay.orchestrator.handoff.get_git_status", git_status_mock), \
         patch("coderelay.orchestrator.handoff.commit_changes", return_value=GitResult(True, ["commit"], stdout="[main abc1234]")), \
         patch("coderelay.orchestrator.handoff.push_changes", return_value=GitResult(True, ["push"])), \
         patch("coderelay.orchestrator.handoff.verify_remote_commit", return_value=True), \
         patch("coderelay.orchestrator.handoff.verify_remote_file", return_value=True), \
         patch("coderelay.orchestrator.handoff.run_git", return_value=GitResult(True, ["rev-parse"], stdout="abc1234")), \
         patch("coderelay.orchestrator.handoff.GitSyncService", return_value=mock_sync_instance):

        decision = orch.request_manual_switch(target_agent="antigravity")

        assert decision.approved is True
        assert orch.context.state == OrchestratorState.ACTIVE
        assert orch.context.active_agent_name == "antigravity"
        assert mock_agent.is_running() is False
        assert mock_antigravity_agent.is_running() is True

