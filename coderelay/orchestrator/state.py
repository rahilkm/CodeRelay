"""In-memory state models for the CodeRelay Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from coderelay.usage_models import UsageSnapshot


class OrchestratorState(str, Enum):
    """Lifecycle states of the Orchestrator."""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    RECOMMENDING = "recommending"
    HANDOFF_INITIATED = "handoff_initiated"
    STOPPED = "stopped"


@dataclass(slots=True)
class SwitchDecision:
    """Record of a user decision regarding an agent switch."""

    from_agent: str
    to_agent: str
    approved: bool
    timestamp: datetime
    reason: str
    usage_percent: Optional[float] = None
    threshold: Optional[float] = None


@dataclass(slots=True)
class OrchestrationContext:
    """Minimal in-memory state maintained by the Orchestrator."""

    project_name: str
    workspace_path: Path
    state: OrchestratorState = OrchestratorState.INITIALIZING
    active_agent_name: Optional[str] = None
    current_usage_percent: float = 0.0
    last_snapshot: Optional[UsageSnapshot] = None
    last_recommended_threshold: Optional[float] = None
    declined_threshold: Optional[float] = None
    last_decision: Optional[SwitchDecision] = None

    def reset_usage_threshold_state(self) -> None:
        """Reset threshold recommendation state when usage drops below threshold."""
        self.last_recommended_threshold = None
        self.declined_threshold = None
