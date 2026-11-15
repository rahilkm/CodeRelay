"""Agent coordination and management layer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from coderelay.agents.base import BaseAgent
from coderelay.agents.registry import AgentRegistry
from coderelay.agents.workspace import (
    WorkspaceManager,
    WorkspaceOwnership,
    default_workspace_manager,
    is_pid_alive,
)
from coderelay.config import Config
from coderelay.exceptions import AgentNotAvailableError, WorkspaceOccupiedError
from coderelay.process import terminate_codex_process

logger = logging.getLogger(__name__)


class AgentManager:
    """Orchestrates agent lifecycles, workspace ownership, and common operations."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        workspace_manager: Optional[WorkspaceManager] = None,
    ) -> None:
        self.registry = registry or AgentRegistry.from_config()
        self.workspace_manager = workspace_manager or default_workspace_manager

    def is_agent_available(self, agent_name: str) -> bool:
        """Check if the named agent is available on this system."""
        return self.registry.is_available(agent_name)

    def get_active_owner(self, workspace: Path) -> Optional[WorkspaceOwnership]:
        """Get the current active workspace owner, releasing stale records if found."""
        return self.workspace_manager.get_active_owner(workspace)

    def get_active_agent(self, workspace: Path) -> Optional[BaseAgent]:
        """Get the active BaseAgent running in the workspace, if known."""
        owner = self.get_active_owner(workspace)
        if owner and owner.agent:
            return owner.agent
        return None

    def associate_external_agent(
        self,
        agent_name: str,
        workspace: Path,
        pid: int,
    ) -> WorkspaceOwnership:
        """Associate an externally-launched running process with workspace ownership."""
        return self.workspace_manager.acquire_external_ownership(
            workspace=workspace,
            agent_name=agent_name,
            pid=pid,
        )

    def start_agent(
        self,
        agent_name: str,
        workspace: Path,
        instruction: Optional[str] = None,
        interactive: bool = False,
    ) -> BaseAgent:
        """Start an agent in the specified workspace with exclusive workspace ownership."""
        agent = self.registry.get(agent_name)

        if not agent.is_available():
            raise AgentNotAvailableError(
                f"Agent '{agent.name}' ({agent.display_name}) is not available on this system. "
                f"Executable '{agent.command}' was not found in PATH."
            )

        resolved_workspace = workspace.expanduser().resolve()

        # Check existing ownership before starting process
        active_owner = self.workspace_manager.get_active_owner(resolved_workspace)
        if active_owner is not None:
            raise WorkspaceOccupiedError(
                f"Workspace '{resolved_workspace}' is currently occupied by active agent "
                f"'{active_owner.agent_name}' (PID: {active_owner.pid})."
            )

        # Start agent process
        try:
            if interactive:
                agent.start(workspace=resolved_workspace, instruction=instruction, interactive=True)
            else:
                agent.start(workspace=resolved_workspace, instruction=instruction)
            # Acquire exclusive ownership
            self.workspace_manager.acquire_ownership(resolved_workspace, agent)
            return agent
        except Exception:
            # Ensure cleanup if startup fails halfway
            if agent.is_running():
                agent.stop()
            self.workspace_manager.release_ownership(resolved_workspace)
            raise

    def stop_agent(self, workspace: Path, timeout: float = 3.0) -> bool:
        """Stop the active agent in the workspace and release ownership.

        Returns True if an agent was confirmed stopped and ownership was released,
        False if no active agent was present or if process termination failed.
        """
        resolved_workspace = workspace.expanduser().resolve()
        owner = self.workspace_manager.get_active_owner(resolved_workspace)
        if owner is None:
            return False

        if owner.agent is not None:
            owner.agent.stop(timeout=timeout)
        elif owner.pid is not None and is_pid_alive(owner.pid):
            # Terminate external process by PID
            import subprocess
            import sys
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(owner.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                import os
                import signal
                try:
                    os.kill(owner.pid, signal.SIGTERM)
                except OSError:
                    pass

        # Confirm process is terminated before releasing workspace ownership
        if owner.is_alive():
            logger.error(
                "Process for agent '%s' (PID: %s) in workspace '%s' failed to terminate within %ss. "
                "Workspace ownership and lockfile are retained.",
                owner.agent_name,
                owner.pid,
                resolved_workspace,
                timeout,
            )
            return False

        self.workspace_manager.release_ownership(resolved_workspace)
        return True

    def get_status(self, workspace: Path) -> dict[str, Any]:
        """Return a structured status summary for the workspace and available agents."""
        resolved = workspace.expanduser().resolve()
        owner = self.get_active_owner(resolved)

        return {
            "workspace": resolved,
            "has_active_agent": owner is not None,
            "active_agent_name": owner.agent_name if owner else None,
            "active_pid": owner.pid if owner else None,
            "acquired_at": owner.acquired_at if owner else None,
            "available_agents": [
                {
                    "name": a.name,
                    "display_name": a.display_name,
                    "command": a.command,
                    "available": a.is_available(),
                    "executable_path": a.get_executable_path(),
                }
                for a in self.registry.list_all()
            ],
        }
