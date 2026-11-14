"""Tests for Orchestrator service and switching rules (Phase 5A)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.agents.base import BaseAgent
from coderelay.agents.manager import AgentManager
from coderelay.agents.registry import AgentRegistry
from coderelay.agents.workspace import WorkspaceManager, WorkspaceOwnership
from coderelay.config import Config, MonitoringConfig, WorkspaceConfig
from coderelay.exceptions import (
    AgentError,
    AgentNotAvailableError,
    AgentNotFoundError,
    NoActiveAgentError,
    OrchestratorError,
    ProjectNotFoundError,
    WorkspaceOccupiedError,
)
from coderelay.monitor import UsageMonitor
from coderelay.orchestrator import (
    ApprovalHandler,
    HandoffRequest,
    Orchestrator,
    OrchestratorState,
    Phase5AHandoffInitiator,
    SwitchDecision,
)
from coderelay.projects import Project, ProjectRegistry
from coderelay.usage_models import (
    AccountInfo,
    UsageBucket,
    UsageSnapshot,
    UsageWindow,
)


def make_test_config(tmp_path: Path) -> Config:
    return Config(
        workspace=WorkspaceConfig(path=tmp_path),
        monitoring=MonitoringConfig(
            interval_seconds=120,
            recommend_threshold=90.0,
            critical_threshold=95.0,
        ),
    )


def make_snapshot(usage: float) -> UsageSnapshot:
    bucket = UsageBucket(
        limit_id="codex",
        primary=UsageWindow(name="primary", used_percent=usage),
    )
    return UsageSnapshot(
        timestamp=datetime.now(timezone.utc).astimezone(),
        account=AccountInfo(plan_type="pro"),
        buckets={"codex": bucket},
    )


def create_mock_agent(name: str) -> MagicMock:
    agent = MagicMock(spec=BaseAgent)
    agent.name = name
    agent.display_name = f"{name.title()} CLI"
    agent.command = name
    agent.is_available.return_value = True
    agent.is_running.return_value = True
    agent.get_pid.return_value = 1000 + len(name)
    return agent


@pytest.fixture
def orchestrator_setup(tmp_path: Path):
    cfg = make_test_config(tmp_path)
    ws_mgr = WorkspaceManager()
    reg = AgentRegistry()

    codex = create_mock_agent("codex")
    agy = create_mock_agent("antigravity")
    reg.register(codex)
    reg.register(agy)

    agent_mgr = AgentManager(registry=reg, workspace_manager=ws_mgr)
    handoff_init = Phase5AHandoffInitiator()
    proj_reg = MagicMock(spec=ProjectRegistry)
    proj_reg.get_project.return_value = Project(
        name="TestProj",
        path=tmp_path,
        remote_url="https://github.com/test/repo.git",
    )

    mock_monitor = MagicMock(spec=UsageMonitor)
    mock_monitor.poll_once.return_value = make_snapshot(45.0)

    # By default, confirm_func approves
    approval = ApprovalHandler(
        confirm_func=lambda prompt: True,
        output_func=lambda msg: None,
    )

    orch = Orchestrator(
        config=cfg,
        project_name_or_path="TestProj",
        agent_manager=agent_mgr,
        usage_monitor=mock_monitor,
        approval_handler=approval,
        handoff_initiator=handoff_init,
        project_registry=proj_reg,
    )
    return orch, codex, agy, mock_monitor, handoff_init


def test_startup_below_threshold(orchestrator_setup):
    orch, codex, agy, monitor, _ = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(45.0)

    orch.startup()

    assert orch.context.state == OrchestratorState.ACTIVE
    assert orch.context.active_agent_name == "codex"
    assert orch.context.current_usage_percent == 45.0
    codex.start.assert_called_once()
    agy.start.assert_not_called()


def test_startup_at_90_percent_approved(orchestrator_setup):
    orch, codex, agy, monitor, handoff = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(91.0)
    orch.approval_handler.confirm_func = lambda prompt: True

    orch.startup()

    assert orch.context.state == OrchestratorState.ACTIVE
    assert orch.context.active_agent_name == "antigravity"
    assert orch.context.last_decision is not None
    assert orch.context.last_decision.approved is True
    assert handoff.last_request is not None
    assert handoff.last_request.to_agent == "antigravity"
    agy.start.assert_called_once()
    codex.start.assert_not_called()


def test_startup_at_90_percent_declined(orchestrator_setup):
    orch, codex, agy, monitor, handoff = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(91.0)
    orch.approval_handler.confirm_func = lambda prompt: False

    orch.startup()

    assert orch.context.state == OrchestratorState.ACTIVE
    assert orch.context.active_agent_name == "codex"
    assert orch.context.declined_threshold == 90.0
    assert handoff.last_request is None
    codex.start.assert_called_once()
    agy.start.assert_not_called()


def test_startup_at_95_percent_approved_and_declined(orchestrator_setup):
    orch, codex, agy, monitor, handoff = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(96.0)

    # Approved:
    orch.approval_handler.confirm_func = lambda prompt: True
    orch.startup()
    assert orch.context.active_agent_name == "antigravity"
    agy.start.assert_called_once()


def test_duplicate_recommendation_suppression(orchestrator_setup):
    orch, codex, agy, monitor, _ = orchestrator_setup
    prompt_count = 0

    def mock_confirm(prompt):
        nonlocal prompt_count
        prompt_count += 1
        return False  # decline

    orch.approval_handler.confirm_func = mock_confirm

    # Startup at 91% -> 1 prompt, declined
    monitor.poll_once.return_value = make_snapshot(91.0)
    orch.startup()
    assert prompt_count == 1
    assert orch.context.declined_threshold == 90.0

    # Subsequent poll at 92%: duplicate suppressed!
    orch.handle_usage_snapshot(make_snapshot(92.0))
    assert prompt_count == 1

    # Subsequent poll at 95.5%: critical threshold triggers a 2nd prompt!
    orch.handle_usage_snapshot(make_snapshot(95.5))
    assert prompt_count == 2
    assert orch.context.declined_threshold == 95.0

    # Subsequent poll at 97%: duplicate suppressed!
    orch.handle_usage_snapshot(make_snapshot(97.0))
    assert prompt_count == 2


def test_usage_reset_clears_declined_threshold(orchestrator_setup):
    orch, codex, agy, monitor, _ = orchestrator_setup
    orch.approval_handler.confirm_func = lambda prompt: False

    # Hit 91%, decline
    monitor.poll_once.return_value = make_snapshot(91.0)
    orch.startup()
    assert orch.context.declined_threshold == 90.0

    # Usage drops below threshold (e.g. usage reset)
    orch.handle_usage_snapshot(make_snapshot(15.0))
    assert orch.context.declined_threshold is None
    assert orch.context.last_recommended_threshold is None

    # Usage climbs back up to 90.5%: prompts again!
    prompted = False

    def on_prompt(prompt):
        nonlocal prompted
        prompted = True
        return False

    orch.approval_handler.confirm_func = on_prompt
    orch.handle_usage_snapshot(make_snapshot(90.5))
    assert prompted is True


def test_manual_switch_request_approved(orchestrator_setup):
    orch, codex, agy, monitor, handoff = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(30.0)
    orch.startup()
    assert orch.context.active_agent_name == "codex"

    # Request manual switch to Antigravity
    orch.approval_handler.confirm_func = lambda prompt: True
    decision = orch.request_manual_switch()

    assert decision.approved is True
    assert decision.from_agent == "codex"
    assert decision.to_agent == "antigravity"
    assert orch.context.active_agent_name == "antigravity"
    codex.stop.assert_called_once()
    agy.start.assert_called_once()
    assert handoff.last_request is not None


def test_manual_switch_request_declined(orchestrator_setup):
    orch, codex, agy, monitor, handoff = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(30.0)
    orch.startup()

    # User declines switch
    orch.approval_handler.confirm_func = lambda prompt: False
    decision = orch.request_manual_switch()

    assert decision.approved is False
    assert orch.context.active_agent_name == "codex"
    codex.stop.assert_not_called()
    agy.start.assert_not_called()


def test_manual_switch_no_active_agent(orchestrator_setup):
    orch, _, _, _, _ = orchestrator_setup
    # Don't call startup -> workspace is unoccupied
    with pytest.raises(NoActiveAgentError, match="No active agent is running"):
        orch.request_manual_switch()


def test_missing_project_raises(tmp_path: Path):
    cfg = make_test_config(tmp_path)
    proj_reg = MagicMock(spec=ProjectRegistry)
    proj_reg.get_project.return_value = None

    with pytest.raises(ProjectNotFoundError, match="was not found in registry"):
        Orchestrator(
            config=cfg,
            project_name_or_path="NonExistentProject123",
            project_registry=proj_reg,
        )


def test_existing_active_agent_at_startup(orchestrator_setup, tmp_path: Path):
    orch, codex, agy, monitor, _ = orchestrator_setup

    # Simulate an agent already owning the workspace
    mock_owner = WorkspaceOwnership(
        workspace_path=tmp_path.resolve(),
        agent_name="codex",
        pid=12345,
        acquired_at=datetime.now(timezone.utc).astimezone(),
        agent=codex,
    )
    orch.agent_manager.workspace_manager._ownerships[tmp_path.resolve()] = mock_owner

    orch.startup()

    # Did not attempt to launch codex again
    codex.start.assert_not_called()
    assert orch.context.active_agent_name == "codex"


def test_monitor_lifecycle_shutdown(orchestrator_setup):
    orch, _, _, monitor, _ = orchestrator_setup
    orch.startup()
    assert orch.context.state == OrchestratorState.ACTIVE

    orch.shutdown()
    assert orch.context.state == OrchestratorState.STOPPED
    monitor.stop.assert_called_once()


def test_startup_missing_agent_raises(orchestrator_setup):
    orch, codex, _, _, _ = orchestrator_setup
    codex.is_available.return_value = False

    with pytest.raises(AgentNotAvailableError, match="not available"):
        orch.startup()


def test_manual_switch_missing_target_agent_raises(orchestrator_setup):
    orch, _, _, monitor, _ = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(30.0)
    orch.startup()

    with pytest.raises(AgentNotFoundError, match="unknown"):
        orch.request_manual_switch(target_agent="nonexistent_agent")


def test_manual_switch_to_same_active_agent_raises(orchestrator_setup):
    orch, _, _, monitor, _ = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(30.0)
    orch.startup()

    with pytest.raises(OrchestratorError, match="already the active agent"):
        orch.request_manual_switch(target_agent="codex")


def test_simultaneous_agents_prevention(orchestrator_setup, tmp_path: Path):
    orch, codex, agy, monitor, _ = orchestrator_setup
    monitor.poll_once.return_value = make_snapshot(30.0)
    orch.startup()
    assert orch.context.active_agent_name == "codex"

    # Attempting to start another agent in the same workspace directly raises WorkspaceOccupiedError
    with pytest.raises(WorkspaceOccupiedError, match="currently occupied"):
        orch.agent_manager.start_agent("antigravity", tmp_path)

