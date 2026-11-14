"""CodeRelay Orchestrator module."""

from coderelay.orchestrator.approval import (
    ApprovalHandler,
    cli_task_completion_confirm,
    default_cli_confirm,
)
from coderelay.orchestrator.handoff import (
    ANTIGRAVITY_CONTINUATION_INSTRUCTION,
    HandoffInterface,
    HandoffRequest,
    HandoffService,
    Phase5AHandoffInitiator,
    Phase5BHandoffService,
    default_test_runner,
)
from coderelay.orchestrator.orchestrator import Orchestrator
from coderelay.orchestrator.state import (
    OrchestrationContext,
    OrchestratorState,
    SwitchDecision,
)

__all__ = [
    "ANTIGRAVITY_CONTINUATION_INSTRUCTION",
    "ApprovalHandler",
    "HandoffInterface",
    "HandoffRequest",
    "HandoffService",
    "OrchestrationContext",
    "Orchestrator",
    "OrchestratorState",
    "Phase5AHandoffInitiator",
    "Phase5BHandoffService",
    "SwitchDecision",
    "cli_task_completion_confirm",
    "default_cli_confirm",
    "default_test_runner",
]
