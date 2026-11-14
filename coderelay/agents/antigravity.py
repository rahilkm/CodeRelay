"""Antigravity CLI (agy) agent adapter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from coderelay.agents.base import SubprocessAgent


class AntigravityAgent(SubprocessAgent):
    """Adapter for the Antigravity coding agent CLI (agy)."""

    def __init__(self, command: str = "agy") -> None:
        super().__init__(
            command=command,
            name="antigravity",
            display_name="Antigravity CLI",
        )

    def get_executable_path(self) -> str | None:
        return shutil.which(self.command)

    def _build_launch_command(
        self,
        executable: str,
        workspace: Path,
        instruction: Optional[str],
    ) -> str:
        """Build command line for launching Antigravity CLI (agy).

        Verified CLI options:
        - '--prompt-interactive <text>' (or -i): Run an initial prompt interactively and continue
        """
        args = [executable]
        if instruction and instruction.strip():
            args.extend(["--prompt-interactive", instruction.strip()])

        return subprocess.list2cmdline(args)
