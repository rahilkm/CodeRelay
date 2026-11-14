"""Reusable user approval interface for agent switching."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Callable, Optional

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


class ApprovalHandler:
    """Handles formatted presentation of switch recommendations and user approval input."""

    def __init__(
        self,
        confirm_func: Optional[Callable[[str], bool]] = None,
        output_func: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.confirm_func = confirm_func or default_cli_confirm
        self.output_func = output_func or (lambda s: sys.stdout.write(s + "\n"))

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
