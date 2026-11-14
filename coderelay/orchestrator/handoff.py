"""Handoff execution service for CodeRelay (Phase 5B)."""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from coderelay.agents.base import BaseAgent
from coderelay.agents.manager import AgentManager
from coderelay.context import CONTEXT_FILENAME, read_context, validate_context
from coderelay.exceptions import (
    HandoffCommitError,
    HandoffContextError,
    HandoffError,
    HandoffGitStateError,
    HandoffProcessShutdownError,
    HandoffPushError,
    HandoffTaskCompletionError,
    HandoffTestFailureError,
    HandoffVerificationError,
)
from coderelay.git import (
    commit_changes,
    ensure_git_repo,
    get_current_branch,
    get_git_status,
    is_protected_file,
    push_changes,
    run_git,
    verify_remote_commit,
    verify_remote_file,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HandoffRequest:
    """Represents an approved request to hand off a workspace between agents."""

    from_agent: str
    to_agent: str
    workspace_path: Path
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc).astimezone())
    status: str = "initiated"
    commit_hash: Optional[str] = None
    error: Optional[str] = None


class HandoffInterface(ABC):
    """Abstract interface defining the handoff boundary."""

    @abstractmethod
    def initiate_handoff(self, request: HandoffRequest) -> HandoffRequest:
        """Initiate the handoff workflow."""
        ...


class Phase5AHandoffInitiator(HandoffInterface):
    """Phase 5A handoff initiator implementation.

    Records the approved switch and prepares the handoff boundary for full execution in 5B/5C.
    """

    def __init__(self) -> None:
        self.last_request: HandoffRequest | None = None

    def initiate_handoff(self, request: HandoffRequest) -> HandoffRequest:
        logger.info(
            "Handoff workflow initiated: '%s' -> '%s' in %s (reason: %s)",
            request.from_agent,
            request.to_agent,
            request.workspace_path,
            request.reason,
        )
        self.last_request = request
        request.status = "initiated"
        return request


def default_test_runner(workspace: Path) -> tuple[bool, str]:
    """Execute pytest in the workspace to verify test suite passes."""
    try:
        proc = subprocess.run(
            ["pytest", "-q"],
            cwd=str(workspace.resolve()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120.0,
            check=False,
        )
        output = proc.stdout or proc.stderr
        return (proc.returncode == 0), output
    except Exception as err:
        return False, str(err)


class HandoffService(HandoffInterface):
    """Phase 5B Outgoing Agent Handoff Execution Service.

    Executes the approved handoff workflow for the outgoing agent:
    1. Stops assigning new work to the outgoing agent.
    2. Requires a verified task completion handler for active interactive agents.
    3. Independently verifies CODERELAY_CONTEXT.md validity.
    4. Executes test suite to verify code quality.
    5. Isolates handoff changes from pre-existing user modifications; rejects protected files.
    6. Stages safe handoff files and creates a descriptive handoff commit.
    7. Pushes changes to the remote branch; retains local commit on failure.
    8. Independently verifies commit and context file exist on remote.
    9. Confirms outgoing agent process has terminated before releasing workspace ownership.
    10. On any failure, preserves local workspace and aborts without launching incoming agent.
    """

    def __init__(
        self,
        agent_manager: Optional[AgentManager] = None,
        test_runner: Optional[Callable[[Path], tuple[bool, str]]] = None,
        task_completion_handler: Optional[Callable[[BaseAgent | None, Path, str], bool]] = None,
        remote: str = "origin",
        branch: Optional[str] = None,
        shutdown_timeout: float = 5.0,
        handoff_files: Optional[list[str]] = None,
    ) -> None:
        self.agent_manager = agent_manager
        self.test_runner = test_runner or default_test_runner
        self.task_completion_handler = task_completion_handler
        self.remote = remote
        self.branch = branch
        self.shutdown_timeout = shutdown_timeout
        self.handoff_files = handoff_files
        self.last_request: Optional[HandoffRequest] = None

    def initiate_handoff(self, request: HandoffRequest) -> HandoffRequest:
        """Execute the complete Phase 5B outgoing agent handoff workflow."""
        self.last_request = request
        workspace = request.workspace_path.expanduser().resolve()
        logger.info(
            "Executing Phase 5B handoff for outgoing agent '%s' in '%s'",
            request.from_agent,
            workspace,
        )

        commit_hash: Optional[str] = None

        try:
            # 1. Identify active agent
            active_agent: Optional[BaseAgent] = None
            if self.agent_manager:
                active_agent = self.agent_manager.get_active_agent(workspace)

            # 2. Stop assigning work, request verified task completion and context update
            self._request_outgoing_agent_completion(active_agent, workspace, request)

            # 3. Independently verify CODERELAY_CONTEXT.md
            self._verify_context_file(workspace)

            # 4. Require relevant tests to run and pass
            self._run_tests(workspace)

            # 5. Inspect Git status, isolate handoff changes, enforce security, and commit
            commit_hash = self._commit_handoff_changes(workspace, request)
            request.commit_hash = commit_hash

            # 6. Push to remote (retains commit on failure)
            self._push_handoff_changes(workspace, commit_hash)

            # 7. Independently verify commit and context file on remote
            self._verify_remote_state(workspace, commit_hash)

            # 8. Confirm outgoing agent process has stopped before releasing ownership
            self._confirm_process_stopped(active_agent, workspace, request.from_agent)

            request.status = "completed"
            logger.info("Phase 5B handoff completed successfully for '%s'.", request.from_agent)
            return request

        except Exception as err:
            logger.error("Phase 5B handoff failed: %s", err)
            request.status = "failed"
            request.error = str(err)
            if commit_hash:
                request.commit_hash = commit_hash
            # Local workspace is preserved without destructive actions
            raise

    def _request_outgoing_agent_completion(
        self,
        agent: Optional[BaseAgent],
        workspace: Path,
        request: HandoffRequest,
    ) -> None:
        """Instruct the outgoing agent to finish current work and update context."""
        instruction = (
            f"CodeRelay Handoff Request:\n"
            f"A handoff from '{request.from_agent}' to '{request.to_agent}' has been approved "
            f"(reason: {request.reason}).\n"
            f"Please finish your current operation immediately. Do not start new tasks.\n"
            f"Update {CONTEXT_FILENAME} with Completed Tasks, Current Task and Progress, "
            f"Pending Tasks, and Next Agent Instructions.\n"
            f"Run relevant tests and review all changes before finishing."
        )

        if self.task_completion_handler is not None:
            success = self.task_completion_handler(agent, workspace, instruction)
            if not success:
                raise HandoffTaskCompletionError(
                    f"Outgoing agent '{request.from_agent}' failed or declined task completion."
                )
        else:
            # Writing to anonymous subprocess pipe stdin does not reliably communicate
            # with an interactive Codex session or guarantee completion.
            if agent is not None and agent.is_running():
                raise HandoffTaskCompletionError(
                    f"Cannot establish reliable communication with active agent '{request.from_agent}'. "
                    "Interactive Codex sessions do not support reliable stdin completion signals. "
                    "Handoff requires a supported communication handler."
                )

    def _verify_context_file(self, workspace: Path) -> None:
        """Verify CODERELAY_CONTEXT.md exists, is readable, and contains all required sections."""
        context_path = workspace / CONTEXT_FILENAME
        if not context_path.is_file():
            raise HandoffContextError(
                f"Context file '{CONTEXT_FILENAME}' is missing in workspace '{workspace}'."
            )

        try:
            content = read_context(workspace)
        except Exception as err:
            raise HandoffContextError(f"Failed to read '{CONTEXT_FILENAME}': {err}") from err

        valid, missing = validate_context(content)
        if not valid:
            raise HandoffContextError(
                f"Context file '{CONTEXT_FILENAME}' is missing required sections: {', '.join(missing)}"
            )

    def _run_tests(self, workspace: Path) -> None:
        """Require relevant test suite to execute and pass."""
        success, output = self.test_runner(workspace)
        if not success:
            raise HandoffTestFailureError(
                f"Test suite verification failed during handoff: {output.strip()}"
            )

    def _commit_handoff_changes(self, workspace: Path, request: HandoffRequest) -> str:
        """Inspect Git state, isolate handoff changes, enforce security exclusions, and commit."""
        ensure_git_repo(workspace)
        status = get_git_status(workspace)

        # 1. Reject merge conflicts
        if status.has_conflicts:
            raise HandoffGitStateError(
                f"Cannot proceed with handoff: merge conflicts exist in "
                f"{', '.join(status.conflicted_files)}."
            )

        # 2. Reject sensitive / protected files in working tree
        all_dirty = status.staged_files + status.unstaged_files + status.untracked_files
        for filepath in all_dirty:
            if is_protected_file(filepath):
                raise HandoffGitStateError(
                    f"Refusing handoff commit: protected sensitive file is modified in workspace: {filepath}"
                )

        # 3. Define allowed handoff files (by default, strictly CODERELAY_CONTEXT.md)
        designated_handoff_files = set(self.handoff_files or [CONTEXT_FILENAME])

        # 4. Check for pre-existing staged user changes
        pre_existing_staged = [f for f in status.staged_files if f not in designated_handoff_files]
        if pre_existing_staged:
            raise HandoffGitStateError(
                f"Working tree contains pre-existing staged changes in {', '.join(pre_existing_staged)}. "
                "Handoff cannot silently rewrite or include user staged changes. "
                "Please commit, unstage, or stash user changes before handoff."
            )

        # 5. Check for pre-existing unstaged user changes
        pre_existing_unstaged = [f for f in status.unstaged_files if f not in designated_handoff_files]
        if pre_existing_unstaged and self.handoff_files is None:
            raise HandoffGitStateError(
                f"Working tree contains uncommitted user modifications in {', '.join(pre_existing_unstaged)}. "
                "Cannot reliably distinguish them from handoff changes. "
                "Please commit or stash user changes before handoff."
            )

        # 6. Only stage handoff-generated changes
        files_to_stage: list[str] = []
        for hf in designated_handoff_files:
            if is_protected_file(hf):
                raise HandoffGitStateError(f"Handoff file '{hf}' is a protected sensitive file.")
            if (
                hf in status.unstaged_files
                or hf in status.untracked_files
                or hf in status.staged_files
            ):
                files_to_stage.append(hf)

        # If no handoff files were modified and index is clean
        if not files_to_stage and not status.staged_files:
            res = run_git(["rev-parse", "HEAD"], cwd=workspace, check=True)
            return res.stdout

        commit_msg = f"[CodeRelay] Handoff from {request.from_agent} to {request.to_agent}: {request.reason}"

        try:
            commit_changes(workspace, message=commit_msg, files=files_to_stage if files_to_stage else None)
        except Exception as err:
            raise HandoffCommitError(f"Handoff commit failed: {err}") from err

        head_res = run_git(["rev-parse", "HEAD"], cwd=workspace, check=True)
        return head_res.stdout

    def _push_handoff_changes(self, workspace: Path, commit_hash: str) -> None:
        """Push handoff commit to remote repository, preserving local commit on failure."""
        target_branch = self.branch
        if not target_branch:
            try:
                target_branch = get_current_branch(workspace)
            except Exception:
                target_branch = "main"

        try:
            push_changes(workspace, remote=self.remote, branch=self.branch)
        except Exception as err:
            raise HandoffPushError(
                f"Handoff push to remote '{self.remote}/{target_branch}' failed: {err}. "
                f"Local handoff commit '{commit_hash}' was retained. "
                f"Manual recovery: 'git push {self.remote} {target_branch}'."
            ) from err

    def _verify_remote_state(self, workspace: Path, expected_commit: str) -> None:
        """Independently verify commit and updated context file exist on remote."""
        target_branch = self.branch
        if not target_branch:
            try:
                target_branch = get_current_branch(workspace)
            except Exception:
                target_branch = "main"

        try:
            commit_ok = verify_remote_commit(workspace, remote=self.remote, branch=self.branch)
        except Exception as err:
            raise HandoffVerificationError(
                f"Remote commit verification failed: {err}. Local commit '{expected_commit}' was retained."
            ) from err

        if not commit_ok:
            raise HandoffVerificationError(
                f"Remote verification failed: commit '{expected_commit}' does not exist on remote '{self.remote}'. "
                f"Local commit '{expected_commit}' was retained. "
                f"Manual recovery: inspect remote branch '{self.remote}/{target_branch}'."
            )

        try:
            context_ok = verify_remote_file(
                workspace,
                filename=CONTEXT_FILENAME,
                remote=self.remote,
                branch=self.branch,
            )
        except Exception as err:
            raise HandoffVerificationError(
                f"Remote context file verification failed: {err}. Local commit '{expected_commit}' was retained."
            ) from err

        if not context_ok:
            raise HandoffVerificationError(
                f"Remote verification failed: '{CONTEXT_FILENAME}' not found or out of date on remote '{self.remote}'. "
                f"Local commit '{expected_commit}' was retained."
            )

    def _confirm_process_stopped(
        self,
        agent: Optional[BaseAgent],
        workspace: Path,
        agent_name: str,
    ) -> None:
        """Confirm the outgoing agent process has terminated and release ownership."""
        if self.agent_manager:
            owner = self.agent_manager.get_active_owner(workspace)
            if owner is not None:
                stopped = self.agent_manager.stop_agent(workspace, timeout=self.shutdown_timeout)
                if not stopped:
                    raise HandoffProcessShutdownError(
                        f"Outgoing agent '{agent_name}' process (PID: {owner.pid}) failed to terminate "
                        f"within {self.shutdown_timeout}s timeout. Workspace ownership is retained."
                    )
        elif agent is not None and agent.is_running():
            agent.stop(timeout=self.shutdown_timeout)
            if agent.is_running():
                raise HandoffProcessShutdownError(
                    f"Outgoing agent '{agent_name}' failed to terminate within "
                    f"{self.shutdown_timeout}s timeout. Workspace ownership is retained."
                )


Phase5BHandoffService = HandoffService
