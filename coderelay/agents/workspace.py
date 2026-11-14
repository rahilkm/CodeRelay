"""In-memory workspace ownership management."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from coderelay.agents.base import BaseAgent
from coderelay.exceptions import WorkspaceOccupiedError

logger = logging.getLogger(__name__)
LOCK_FILENAME = ".coderelay_owner.json"


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is currently active."""
    if pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


@dataclass(slots=True)
class WorkspaceOwnership:
    """Represents ownership of a workspace by a running agent."""

    workspace_path: Path
    agent_name: str
    pid: Optional[int]
    acquired_at: datetime
    agent: Optional[BaseAgent] = None

    def is_alive(self) -> bool:
        """Check if the owning agent process is still alive."""
        if self.agent is not None:
            return self.agent.is_running()
        if self.pid is not None:
            return is_pid_alive(self.pid)
        return False


class WorkspaceManager:
    """Manages workspace ownership, preventing concurrent agent execution in one workspace."""

    def __init__(self) -> None:
        self._ownerships: dict[Path, WorkspaceOwnership] = {}
        self._lock = threading.RLock()

    def get_active_owner(self, workspace: Path) -> Optional[WorkspaceOwnership]:
        """Return the current active owner of the workspace, or None if unoccupied or stale."""
        resolved = workspace.expanduser().resolve()

        with self._lock:
            # 1. Check in-memory ownership
            owner = self._ownerships.get(resolved)
            if owner is not None:
                if owner.is_alive():
                    return owner
                # Stale process: clean up
                self._ownerships.pop(resolved, None)
                self._remove_lockfile(resolved)
                return None

            # 2. Check disk lockfile (for cross-command state without a database)
            disk_owner = self._read_lockfile(resolved)
            if disk_owner is not None:
                if disk_owner.is_alive():
                    self._ownerships[resolved] = disk_owner
                    return disk_owner
                # Stale disk lock: clean up
                self._remove_lockfile(resolved)
                return None

            return None

    def acquire_ownership(
        self,
        workspace: Path,
        agent: BaseAgent,
    ) -> WorkspaceOwnership:
        """Acquire ownership of a workspace for an agent. Raises WorkspaceOccupiedError if busy."""
        resolved = workspace.expanduser().resolve()

        with self._lock:
            active_owner = self.get_active_owner(resolved)
            if active_owner is not None:
                raise WorkspaceOccupiedError(
                    f"Workspace '{resolved}' is already occupied by agent '{active_owner.agent_name}' "
                    f"(PID: {active_owner.pid})."
                )

            ownership = WorkspaceOwnership(
                workspace_path=resolved,
                agent_name=agent.name,
                pid=agent.get_pid(),
                acquired_at=datetime.now(timezone.utc).astimezone(),
                agent=agent,
            )
            self._ownerships[resolved] = ownership
            self._write_lockfile(ownership)
            return ownership

    def release_ownership(self, workspace: Path) -> None:
        """Release ownership of the workspace."""
        resolved = workspace.expanduser().resolve()
        with self._lock:
            self._ownerships.pop(resolved, None)
            self._remove_lockfile(resolved)

    def _read_lockfile(self, workspace: Path) -> Optional[WorkspaceOwnership]:
        """Read lightweight lockfile from workspace if present."""
        lockfile = workspace / LOCK_FILENAME
        if not lockfile.is_file():
            return None

        try:
            data = json.loads(lockfile.read_text(encoding="utf-8"))
            pid = data.get("pid")
            agent_name = data.get("agent_name", "unknown")
            acquired_str = data.get("acquired_at")
            acquired_at = (
                datetime.fromisoformat(acquired_str)
                if acquired_str
                else datetime.now(timezone.utc).astimezone()
            )
            return WorkspaceOwnership(
                workspace_path=workspace,
                agent_name=agent_name,
                pid=pid,
                acquired_at=acquired_at,
                agent=None,
            )
        except Exception:
            return None

    def _write_lockfile(self, ownership: WorkspaceOwnership) -> None:
        """Write lightweight lockfile to workspace."""
        if not ownership.workspace_path.is_dir():
            return

        lockfile = ownership.workspace_path / LOCK_FILENAME
        try:
            payload = {
                "workspace": str(ownership.workspace_path),
                "agent_name": ownership.agent_name,
                "pid": ownership.pid,
                "acquired_at": ownership.acquired_at.isoformat(),
            }
            lockfile.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to write workspace lockfile: %s", e)

    def _remove_lockfile(self, workspace: Path) -> None:
        """Safely delete the workspace lockfile."""
        lockfile = workspace / LOCK_FILENAME
        if lockfile.is_file():
            try:
                lockfile.unlink()
            except Exception:
                pass


# Global default workspace manager
default_workspace_manager = WorkspaceManager()
