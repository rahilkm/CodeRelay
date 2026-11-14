"""Reusable user approval interface for agent switching."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from coderelay.agents.base import BaseAgent
from coderelay.orchestrator.state import SwitchDecision


def default_cli_confirm(prompt_text: str) -> bool:
    """Prompt user for explicit yes/no confirmation from standard input."""
    sys.stdout.write(f"\n{prompt_text} [y/N]: ")
    sys.stdout.flush()
    try:
        response = sys.stdin.readline().strip().lower()
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def cli_task_completion_confirm(
    agent: Optional[BaseAgent],
    workspace: Path,
    instruction: str,
    confirm_func: Optional[Callable[[str], bool]] = None,
    output_func: Optional[Callable[[str], None]] = None,
    workspace_manager: Optional[Any] = None,
) -> bool:
    """Prompt user to explicitly confirm that the outgoing agent's task is complete,

    CODERELAY_CONTEXT.md is updated, and the workspace is ready for handoff.
    Used when the outgoing agent cannot receive automated completion signals.
    """
    confirm = confirm_func or default_cli_confirm
    output = output_func or (lambda s: sys.stdout.write(s + "\n"))

    ws_mgr = workspace_manager
    if ws_mgr is None:
        from coderelay.agents.workspace import default_workspace_manager
        ws_mgr = default_workspace_manager

    owner = ws_mgr.get_active_owner(workspace)
    agent_name = owner.agent_name if owner else (agent.name if agent else "outgoing agent")
    pid_str = (
        f" (PID: {owner.pid})"
        if (owner and owner.pid)
        else (f" (PID: {agent.get_pid()})" if (agent and agent.get_pid()) else "")
    )

    lines = [
        "",
        "=" * 50,
        "       OUTGOING AGENT TASK COMPLETION CHECK",
        "=" * 50,
        f"  Active Agent : {agent_name}{pid_str}",
        f"  Workspace    : {workspace}",
        "",
        "  The outgoing agent cannot receive automated completion signals.",
        "  Before proceeding with the handoff, please ensure:",
        "    1. Its current task is complete.",
        "    2. CODERELAY_CONTEXT.md has been updated with:",
        "       - Completed Tasks",
        "       - Current Task and Progress",
        "       - Pending Tasks",
        "       - Next Agent Instructions",
        "    3. The workspace is ready for handoff.",
        "=" * 50,
    ]
    for line in lines:
        output(line)

    prompt = f"Confirm that '{agent_name}' task is complete and workspace is ready for handoff?"
    approved = confirm(prompt)
    if approved:
        output(f"[Confirmed] Outgoing task completion confirmed by user for '{agent_name}'.")
    else:
        output(f"[Declined] Outgoing task completion not confirmed. Handoff aborted.")
    return approved


class ApprovalHandler:
    """Handles formatted presentation of switch recommendations and user approval input."""

    def __init__(
        self,
        confirm_func: Optional[Callable[[str], bool]] = None,
        output_func: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.confirm_func = confirm_func or default_cli_confirm
        self.output_func = output_func or (lambda s: sys.stdout.write(s + "\n"))

    def create_task_completion_handler(
        self,
        workspace_manager: Optional[Any] = None,
    ) -> Callable[[Optional[BaseAgent], Path, str], bool]:
        """Create a task completion handler bound to this ApprovalHandler's confirm_func and output_func."""
        return lambda agent, ws, instr: cli_task_completion_confirm(
            agent,
            ws,
            instr,
            confirm_func=self.confirm_func,
            output_func=self.output_func,
            workspace_manager=workspace_manager,
        )

    def request_approval(
        self,
        from_agent: str,
        to_agent: str,
        reason: str,
        usage_percent: Optional[float] = None,
        threshold: Optional[float] = None,
    ) -> SwitchDecision:
        """Present recommendation to the user and capture explicit approval or decline."""
        now = datetime.now(timezone.utc).astimezone()

        lines = [
            "",
            "=" * 50,
            "         AGENT SWITCH RECOMMENDATION",
            "=" * 50,
            f"  Current Active Agent    : {from_agent}",
            f"  Proposed Destination    : {to_agent}",
        ]
        if usage_percent is not None:
            thresh_str = f" (Threshold: {threshold:.1f}%)" if threshold else ""
            lines.append(f"  Codex Account Usage     : {usage_percent:.1f}%{thresh_str}")
        lines.append(f"  Reason                  : {reason}")
        lines.append("=" * 50)

        for line in lines:
            self.output_func(line)

        prompt = f"Approve switching from '{from_agent}' to '{to_agent}'?"
        approved = self.confirm_func(prompt)

        if approved:
            self.output_func(f"[Approval] Switch to '{to_agent}' approved by user.")
        else:
            self.output_func(f"[Declined] Switch to '{to_agent}' declined by user. Continuing with '{from_agent}'.")

        return SwitchDecision(
            from_agent=from_agent,
            to_agent=to_agent,
            approved=approved,
            timestamp=now,
            reason=reason,
            usage_percent=usage_percent,
            threshold=threshold,
        )
