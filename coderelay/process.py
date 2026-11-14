"""Windows process management for the Codex App Server."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

from coderelay.exceptions import CodexNotFoundError, CodexProcessError

logger = logging.getLogger(__name__)


def locate_codex_cli(custom_command: Optional[str] = None) -> str:
    """Locate the Codex CLI executable on Windows."""
    target = custom_command or "codex"
    found = shutil.which(target)
    if not found:
        raise CodexNotFoundError(
            f"Codex CLI '{target}' was not found in PATH. Please ensure Codex CLI is installed."
        )
    return found


def start_codex_app_server(
    codex_path: str,
    stderr: int | None = subprocess.DEVNULL,
) -> subprocess.Popen:
    """Start the Codex App Server preserving the verified Windows process-launching approach.

    Do not replace this working invocation with subprocess.Popen(["codex", ...]).
    """
    cmd = f'"{codex_path}" app-server'
    executable = os.environ.get("COMSPEC", "cmd.exe")

    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            executable=executable,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except Exception as err:
        raise CodexProcessError(f"Failed to start Codex App Server: {err}") from err

    if proc.poll() is not None:
        raise CodexProcessError(
            f"Codex App Server process terminated immediately with exit code {proc.returncode}."
        )

    return proc


def terminate_codex_process(proc: subprocess.Popen, timeout: float = 3.0) -> None:
    """Terminate the Codex App Server and its child processes completely to avoid orphans."""
    if proc is None:
        return

    pid = proc.pid
    if proc.poll() is not None:
        # Process already terminated, ensure pipes are closed
        _close_pipes(proc)
        return

    # On Windows, shell=True runs cmd.exe which spawns the node/codex child.
    # taskkill /F /T terminates the process and any child processes it started.
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except Exception as e:
            logger.debug("taskkill failed for pid %s: %s", pid, e)

    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=1.0)
    except Exception:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1.0)
        except Exception:
            pass
    finally:
        _close_pipes(proc)


def _close_pipes(proc: subprocess.Popen) -> None:
    """Safely close stdin/stdout/stderr pipes of a process."""
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None and not pipe.closed:
            try:
                pipe.close()
            except Exception:
                pass
