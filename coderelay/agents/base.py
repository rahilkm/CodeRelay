"""Base abstraction for AI coding agents."""

from __future__ import annotations

import collections
import logging
import os
import shutil
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from coderelay.exceptions import AgentNotAvailableError, AgentStartError
from coderelay.process import terminate_codex_process

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract interface defining required capabilities for any coding agent adapter."""

    name: str
    display_name: str
    command: str

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether the agent executable is installed and reachable."""
        ...

    @abstractmethod
    def get_executable_path(self) -> str | None:
        """Return the resolved path of the executable, or None if unavailable."""
        ...

    @abstractmethod
    def start(self, workspace: Path, instruction: str | None = None) -> None:
        """Launch the agent in the specified workspace with an optional initial instruction."""
        ...

    @abstractmethod
    def stop(self, timeout: float = 3.0) -> None:
        """Gracefully stop the running agent process."""
        ...

    @abstractmethod
    def kill(self) -> None:
        """Forcibly kill the running agent and any spawned child processes."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the agent process is actively running."""
        ...

    @abstractmethod
    def get_exit_code(self) -> int | None:
        """Return the process exit code, or None if still running."""
        ...

    @abstractmethod
    def get_pid(self) -> int | None:
        """Return the process ID, or None if not running."""
        ...

    @abstractmethod
    def get_output(self) -> str:
        """Return captured output from stdout and stderr."""
        ...


class SubprocessAgent(BaseAgent):
    """Common subprocess-based implementation for CLI coding agents."""

    def __init__(self, command: str, name: str, display_name: str) -> None:
        self.command = command
        self.name = name
        self.display_name = display_name

        self._process: Optional[subprocess.Popen] = None
        self._workspace: Optional[Path] = None
        self._start_time: Optional[datetime] = None
        self._output_lines: collections.deque[str] = collections.deque(maxlen=2000)
        self._reader_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def get_executable_path(self) -> str | None:
        return shutil.which(self.command)

    def is_available(self) -> bool:
        return self.get_executable_path() is not None

    @abstractmethod
    def _build_launch_command(
        self,
        executable: str,
        workspace: Path,
        instruction: Optional[str],
    ) -> list[str] | str:
        """Construct the launch command string or argument list."""
        ...

    def start(self, workspace: Path, instruction: Optional[str] = None) -> None:
        """Launch the agent process in the specified workspace."""
        with self._lock:
            if self.is_running():
                raise AgentStartError(f"Agent '{self.name}' is already running with PID {self.get_pid()}.")

            executable = self.get_executable_path()
            if not executable:
                raise AgentNotAvailableError(
                    f"Agent executable '{self.command}' not found in PATH for '{self.name}'."
                )

            resolved_workspace = workspace.expanduser().resolve()
            if not resolved_workspace.exists():
                raise AgentStartError(f"Workspace directory does not exist: {resolved_workspace}")

            cmd = self._build_launch_command(executable, resolved_workspace, instruction)

            try:
                # On Windows, handling .CMD or console apps cleanly:
                if isinstance(cmd, str):
                    self._process = subprocess.Popen(
                        cmd,
                        shell=True,
                        executable=os.environ.get("COMSPEC", "cmd.exe"),
                        cwd=str(resolved_workspace),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        bufsize=1,
                    )
                else:
                    self._process = subprocess.Popen(
                        cmd,
                        cwd=str(resolved_workspace),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        bufsize=1,
                    )
            except Exception as err:
                self._process = None
                raise AgentStartError(f"Failed to launch '{self.name}': {err}") from err

            if self._process.poll() is not None:
                code = self._process.returncode
                self._process = None
                raise AgentStartError(
                    f"Agent '{self.name}' terminated immediately with exit code {code}."
                )

            self._workspace = resolved_workspace
            self._start_time = datetime.now(timezone.utc).astimezone()
            self._output_lines.clear()

            # Background thread to capture stdout/stderr
            self._reader_thread = threading.Thread(
                target=self._capture_output_loop,
                name=f"{self.name}-output-reader",
                daemon=True,
            )
            self._reader_thread.start()

    def _capture_output_loop(self) -> None:
        """Read output lines continuously from process stdout/stderr."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return

        try:
            for line in iter(proc.stdout.readline, ""):
                if line:
                    self._output_lines.append(line)
        except Exception:
            pass

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def get_exit_code(self) -> int | None:
        if self._process is None:
            return None
        return self._process.poll()

    def get_pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    def get_output(self) -> str:
        return "".join(self._output_lines)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop the agent process and clean up its process tree."""
        with self._lock:
            if self._process is not None:
                terminate_codex_process(self._process, timeout=timeout)
                self._process = None
                self._workspace = None

    def kill(self) -> None:
        """Forcibly terminate the process."""
        self.stop(timeout=0.5)
