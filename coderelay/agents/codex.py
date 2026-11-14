"""Codex CLI agent adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from coderelay.agents.base import SubprocessAgent
from coderelay.process import locate_codex_cli


class CodexAgent(SubprocessAgent):
    """Adapter for the Codex coding agent CLI."""

    def __init__(self, command: str = "codex") -> None:
        super().__init__(
            command=command,
            name="codex",
            display_name="Codex CLI",
        )

    def get_executable_path(self) -> str | None:
        try:
            return locate_codex_cli(self.command)
        except Exception:
            return None

    def _build_launch_command(
        self,
        executable: str,
        workspace: Path,
        instruction: Optional[str],
    ) -> str:
        """Build command line for launching Codex CLI on Windows."""
        # Codex CLI accepts: codex -C <DIR> [PROMPT]
        args = [executable, "-C", str(workspace)]
        if instruction and instruction.strip():
            args.append(instruction.strip())

        return subprocess.list2cmdline(args)
