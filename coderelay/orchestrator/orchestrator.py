"""Central Orchestrator coordinating usage monitoring, agent lifecycle, and handoff approval."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from coderelay.agents.manager import AgentManager
from coderelay.config import Config
from coderelay.exceptions import (
    AgentError,
    NoActiveAgentError,
    OrchestratorError,
    ProjectNotFoundError,
    WorkspaceOccupiedError,
)
from coderelay.monitor import UsageMonitor
from coderelay.orchestrator.approval import ApprovalHandler
from coderelay.orchestrator.handoff import (
    HandoffInterface,
    HandoffRequest,
    HandoffService,
    Phase5AHandoffInitiator,
)
from coderelay.orchestrator.state import (
    OrchestrationContext,
    OrchestratorState,
    SwitchDecision,
)
from coderelay.projects import ProjectRegistry
from coderelay.usage_models import UsageSnapshot

logger = logging.getLogger(__name__)


class Orchestrator:
    """Central engine managing projects, agents, usage monitoring, and switching decisions."""

    def __init__(
        self,
        config: Config,
        project_name_or_path: Optional[str | Path] = None,
        agent_manager: Optional[AgentManager] = None,
        usage_monitor: Optional[UsageMonitor] = None,
        approval_handler: Optional[ApprovalHandler] = None,
        handoff_initiator: Optional[HandoffInterface] = None,
        project_registry: Optional[ProjectRegistry] = None,
    ) -> None:
        self.config = config
        self.project_registry = project_registry or ProjectRegistry()
        self.agent_manager = agent_manager or AgentManager()
        self.approval_handler = approval_handler or ApprovalHandler()
        self.handoff_initiator = handoff_initiator or HandoffService(
            agent_manager=self.agent_manager,
            task_completion_handler=self.approval_handler.create_task_completion_handler(
                self.agent_manager.workspace_manager
            ),
            auto_launch_incoming=True,
        )

        # Resolve project and workspace
        self.project_name, self.workspace_path = self._resolve_project_and_workspace(
            project_name_or_path
        )

        # Context / state management
        self.context = OrchestrationContext(
            project_name=self.project_name,
            workspace_path=self.workspace_path,
        )

        # Configure usage monitor (independent of the active coding agent)
        self.monitor = usage_monitor or UsageMonitor(
            config=self.config,
            on_snapshot=self.handle_usage_snapshot,
        )

    def _resolve_project_and_workspace(
        self,
        project_ref: Optional[str | Path],
    ) -> tuple[str, Path]:
        """Resolve project reference from registry, directory path, or config."""
        if project_ref is not None:
            # 1. Try registry lookup
            proj = self.project_registry.get_project(project_ref)
            if proj is not None:
                return proj.name, proj.path.resolve()

            # 2. Check if reference is an existing path
            try:
                candidate = Path(project_ref).expanduser().resolve()
                if candidate.exists():
                    reg_proj = self.project_registry.get_project(candidate)
                    if reg_proj is not None:
                        return reg_proj.name, candidate
                    return candidate.name, candidate
            except Exception:
                pass

            raise ProjectNotFoundError(f"Project '{project_ref}' was not found in registry or on disk.")

        # Default to config workspace
        ws = self.config.workspace.resolved_path
        reg_proj = self.project_registry.get_project(ws)
        if reg_proj is not None:
            return reg_proj.name, ws
        return ws.name, ws

    @property
    def primary_agent_name(self) -> str:
        return self.config.primary_agent.name if self.config.primary_agent else "codex"

    @property
    def secondary_agent_name(self) -> str:
        return self.config.secondary_agent.name if self.config.secondary_agent else "antigravity"

    def startup(self, instruction: Optional[str] = None) -> None:
        """Execute the startup behavior:

        1. Identify selected project and workspace.
        2. Immediately retrieve current account usage via monitor.
        3. Check whether switching thresholds have already been reached.
        4. If a threshold is reached, request user approval.
        5. Otherwise, launch the configured primary agent.
        """
        self.context.state = OrchestratorState.INITIALIZING

        # Check existing active agent in workspace
        active_owner = self.agent_manager.get_active_owner(self.workspace_path)
        if active_owner is not None and active_owner.is_alive():
            self.context.active_agent_name = active_owner.agent_name
            self.context.state = OrchestratorState.ACTIVE
            return

        # Poll usage immediately
        snapshot = self.monitor.poll_once()
        if snapshot is not None:
            self.context.last_snapshot = snapshot
            self.context.current_usage_percent = snapshot.highest_usage_percent

        usage = self.context.current_usage_percent
        rec_thresh = self.config.monitoring.recommend_threshold
        crit_thresh = self.config.monitoring.critical_threshold

        agent_to_start = self.primary_agent_name

        # Check switching thresholds at startup
        if usage >= crit_thresh:
            self.context.state = OrchestratorState.RECOMMENDING
            decision = self.approval_handler.request_approval(
                from_agent=self.primary_agent_name,
                to_agent=self.secondary_agent_name,
                reason="Account usage already at or above critical threshold at startup",
                usage_percent=usage,
                threshold=crit_thresh,
            )
            self.context.last_decision = decision
            self.context.last_recommended_threshold = crit_thresh

            if decision.approved:
                agent_to_start = self.secondary_agent_name
                self.context.state = OrchestratorState.HANDOFF_INITIATED
                self.handoff_initiator.initiate_handoff(
                    HandoffRequest(
                        from_agent=self.primary_agent_name,
                        to_agent=self.secondary_agent_name,
                        workspace_path=self.workspace_path,
                        reason=decision.reason,
                    )
                )
            else:
                self.context.declined_threshold = crit_thresh

        elif usage >= rec_thresh:
            self.context.state = OrchestratorState.RECOMMENDING
            decision = self.approval_handler.request_approval(
                from_agent=self.primary_agent_name,
                to_agent=self.secondary_agent_name,
                reason="Account usage already at or above recommendation threshold at startup",
                usage_percent=usage,
                threshold=rec_thresh,
            )
            self.context.last_decision = decision
            self.context.last_recommended_threshold = rec_thresh

            if decision.approved:
                agent_to_start = self.secondary_agent_name
                self.context.state = OrchestratorState.HANDOFF_INITIATED
                self.handoff_initiator.initiate_handoff(
                    HandoffRequest(
                        from_agent=self.primary_agent_name,
                        to_agent=self.secondary_agent_name,
                        workspace_path=self.workspace_path,
                        reason=decision.reason,
                    )
                )
            else:
                self.context.declined_threshold = rec_thresh

        # Start the selected agent
        self.agent_manager.start_agent(
            agent_name=agent_to_start,
            workspace=self.workspace_path,
            instruction=instruction,
        )
        self.context.active_agent_name = agent_to_start
        self.context.state = OrchestratorState.ACTIVE

    def handle_usage_snapshot(self, snapshot: UsageSnapshot) -> None:
        """Periodic callback when usage monitor updates account usage."""
        self.context.last_snapshot = snapshot
        self.context.current_usage_percent = snapshot.highest_usage_percent
        usage = snapshot.highest_usage_percent

        rec_thresh = self.config.monitoring.recommend_threshold
        crit_thresh = self.config.monitoring.critical_threshold

        # If usage dropped below threshold, reset notification states
        if usage < rec_thresh:
            self.context.reset_usage_threshold_state()
            return

        # Only evaluate switching recommendations if primary (codex) is active
        if self.context.active_agent_name != self.primary_agent_name:
            return

        # Check critical threshold
        if usage >= crit_thresh:
            if self.context.declined_threshold != crit_thresh:
                self._recommend_switch(crit_thresh, usage, "Account usage reached critical threshold")

        # Check recommend threshold
        elif usage >= rec_thresh:
            if self.context.declined_threshold is None:
                self._recommend_switch(rec_thresh, usage, "Account usage reached recommendation threshold")

    def _recommend_switch(self, threshold: float, usage: float, reason: str) -> None:
        """Display recommendation and process user decision."""
        prev_state = self.context.state
        self.context.state = OrchestratorState.RECOMMENDING

        decision = self.approval_handler.request_approval(
            from_agent=self.primary_agent_name,
            to_agent=self.secondary_agent_name,
            reason=reason,
            usage_percent=usage,
            threshold=threshold,
        )
        self.context.last_decision = decision
        self.context.last_recommended_threshold = threshold

        if decision.approved:
            self.context.state = OrchestratorState.HANDOFF_INITIATED
            try:
                self.monitor.stop()
            except Exception:
                pass
            handoff_req = HandoffRequest(
                from_agent=self.primary_agent_name,
                to_agent=self.secondary_agent_name,
                workspace_path=self.workspace_path,
                reason=reason,
            )
            try:
                self.handoff_initiator.initiate_handoff(handoff_req)
            except Exception:
                active_owner = self.agent_manager.get_active_owner(self.workspace_path)
                if active_owner is not None and active_owner.is_alive():
                    self.context.active_agent_name = active_owner.agent_name
                    self.context.state = OrchestratorState.ACTIVE
                else:
                    self.context.active_agent_name = None
                    self.context.state = OrchestratorState.STOPPED
                raise

            # Backward compatibility for Phase 5A initiator; in Phase 5B HandoffService,
            # outgoing agent is stopped and Antigravity is not launched unless auto_launch_incoming is True.
            if isinstance(self.handoff_initiator, Phase5AHandoffInitiator):
                self.agent_manager.stop_agent(self.workspace_path)
                self.agent_manager.start_agent(self.secondary_agent_name, self.workspace_path)
                self.context.active_agent_name = self.secondary_agent_name
                self.context.state = OrchestratorState.ACTIVE
            elif isinstance(self.handoff_initiator, HandoffService) and self.handoff_initiator.auto_launch_incoming:
                self.context.active_agent_name = self.secondary_agent_name
                self.context.state = OrchestratorState.ACTIVE
            else:
                self.context.active_agent_name = None
                self.context.state = OrchestratorState.HANDOFF_INITIATED
        else:
            self.context.declined_threshold = threshold
            self.context.state = prev_state

    def request_manual_switch(self, target_agent: Optional[str] = None) -> SwitchDecision:
        """Execute manual switch workflow with explicit user confirmation."""
        active_owner = self.agent_manager.get_active_owner(self.workspace_path)
        if active_owner is None or not active_owner.is_alive():
            raise NoActiveAgentError(
                f"No active agent is running in workspace '{self.workspace_path}'."
            )

        current_agent = active_owner.agent_name

        # Determine proposed destination
        if target_agent:
            destination = target_agent.strip().lower()
            if destination == "agy":
                destination = "antigravity"
        else:
            destination = (
                self.secondary_agent_name
                if current_agent == self.primary_agent_name
                else self.primary_agent_name
            )

        if destination == current_agent:
            raise OrchestratorError(
                f"Agent '{destination}' is already the active agent in workspace '{self.workspace_path}'."
            )

        # Validate that destination agent is known in registry before prompting user
        self.agent_manager.registry.get(destination)

        # Prompt user
        decision = self.approval_handler.request_approval(
            from_agent=current_agent,
            to_agent=destination,
            reason="Manual switch requested by user",
            usage_percent=(
                self.context.current_usage_percent
                if current_agent == self.primary_agent_name
                else None
            ),
        )
        self.context.last_decision = decision

        if decision.approved:
            prev_state = self.context.state
            self.context.state = OrchestratorState.HANDOFF_INITIATED
            handoff_req = HandoffRequest(
                from_agent=current_agent,
                to_agent=destination,
                workspace_path=self.workspace_path,
                reason=decision.reason,
            )
            try:
                self.handoff_initiator.initiate_handoff(handoff_req)
            except Exception:
                active_owner = self.agent_manager.get_active_owner(self.workspace_path)
                if active_owner is not None and active_owner.is_alive():
                    self.context.active_agent_name = active_owner.agent_name
                    self.context.state = OrchestratorState.ACTIVE
                else:
                    self.context.active_agent_name = None
                    self.context.state = OrchestratorState.STOPPED
                raise

            # Backward compatibility for Phase 5A initiator; in Phase 5B HandoffService,
            # outgoing agent is stopped and Antigravity is not launched unless auto_launch_incoming is True.
            if isinstance(self.handoff_initiator, Phase5AHandoffInitiator):
                self.agent_manager.stop_agent(self.workspace_path)
                self.agent_manager.start_agent(destination, self.workspace_path)
                self.context.active_agent_name = destination
                self.context.state = OrchestratorState.ACTIVE
            elif isinstance(self.handoff_initiator, HandoffService) and self.handoff_initiator.auto_launch_incoming:
                self.context.active_agent_name = destination
                self.context.state = OrchestratorState.ACTIVE
            else:
                self.context.active_agent_name = None
                self.context.state = OrchestratorState.HANDOFF_INITIATED
        else:
            # Leave active agent running
            self.context.state = OrchestratorState.ACTIVE

        return decision

    def execute_incoming_handoff(
        self,
        request: Optional[HandoffRequest] = None,
        instruction: Optional[str] = None,
    ) -> BaseAgent:
        """Execute Phase 5C incoming handoff to safely launch and transfer ownership to incoming agent."""
        if not hasattr(self.handoff_initiator, "execute_incoming_handoff"):
            raise OrchestratorError(
                f"Configured handoff initiator ({type(self.handoff_initiator).__name__}) does not support incoming handoff."
            )

        req = request or HandoffRequest(
            from_agent=self.context.active_agent_name or self.primary_agent_name,
            to_agent=self.secondary_agent_name,
            workspace_path=self.workspace_path,
            reason="Orchestrator incoming handoff execution",
        )

        try:
            agent = self.handoff_initiator.execute_incoming_handoff(req, instruction=instruction)
            self.context.active_agent_name = req.to_agent
            self.context.state = OrchestratorState.ACTIVE
            return agent
        except Exception:
            active_owner = self.agent_manager.get_active_owner(self.workspace_path)
            if active_owner is not None and active_owner.is_alive():
                self.context.active_agent_name = active_owner.agent_name
                self.context.state = OrchestratorState.ACTIVE
            else:
                self.context.active_agent_name = None
                self.context.state = OrchestratorState.STOPPED
            raise

    def shutdown(self) -> None:
        """Stop monitor and update state cleanly."""
        try:
            self.monitor.stop()
        except Exception:
            pass
        self.context.state = OrchestratorState.STOPPED
