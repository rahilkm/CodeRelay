"""Safe Git synchronization service implementing fast-forward-only behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from coderelay.exceptions import GitSyncError, MergeConflictError, UnsafeSyncError
from coderelay.git import (
    ensure_git_repo,
    fetch_remote,
    get_current_branch,
    get_git_status,
    get_remote_url,
    pull_fast_forward,
    run_git,
)

logger = logging.getLogger(__name__)


class SyncStatus(str, Enum):
    """Synchronization outcome status."""

    UP_TO_DATE = "up_to_date"
    SYNCHRONIZED = "synchronized"
    UNSAFE_LOCAL_CHANGES = "unsafe_local_changes"
    DIVERGED = "diverged"
    CONFLICTS = "conflicts"
    NO_REMOTE = "no_remote"
    ERROR = "error"


@dataclass(slots=True)
class SyncResult:
    """Detailed summary of the synchronization attempt."""

    status: SyncStatus
    message: str
    current_branch: str
    initial_commit: str
    synced_commit: Optional[str] = None
    ahead: int = 0
    behind: int = 0


class GitSyncService:
    """Orchestrates non-destructive Git synchronization."""

    def __init__(self, repo_path: Path, remote: str = "origin") -> None:
        self.repo_path = ensure_git_repo(repo_path)
        self.remote = remote

    def check_safe_to_sync(self) -> tuple[bool, str]:
        """Check if working tree is clean and has no uncommitted changes."""
        status = get_git_status(self.repo_path)
        if status.has_conflicts:
            return False, f"Merge conflicts detected: {', '.join(status.conflicted_files)}"
        if status.has_uncommitted_changes:
            staged = len(status.staged_files)
            unstaged = len(status.unstaged_files)
            return (
                False,
                f"Working tree has uncommitted changes ({staged} staged, {unstaged} unstaged). "
                "Synchronization refused to prevent data loss.",
            )
        return True, "Working tree is clean."

    def synchronize(self) -> SyncResult:
        """Execute the safe synchronization workflow:

        1. Checks the local working tree.
        2. Refuses unsafe synchronization when uncommitted changes could be overwritten.
        3. Fetches the remote branch.
        4. Checks whether fast-forward synchronization is possible.
        5. Pulls only when safe (--ff-only).
        6. Verifies the resulting commit.
        """
        branch = get_current_branch(self.repo_path)
        head_res = run_git(["rev-parse", "HEAD"], cwd=self.repo_path)
        initial_commit = head_res.stdout if head_res.success else "unknown"

        # 1 & 2. Check local working tree safety
        safe, reason = self.check_safe_to_sync()
        if not safe:
            raise UnsafeSyncError(reason)

        # Verify remote configured
        remote_url = get_remote_url(self.repo_path, self.remote)
        if not remote_url:
            return SyncResult(
                status=SyncStatus.NO_REMOTE,
                message=f"No remote URL configured for '{self.remote}'.",
                current_branch=branch,
                initial_commit=initial_commit,
            )

        # 3. Fetch remote updates
        try:
            fetch_remote(self.repo_path, self.remote)
        except Exception as err:
            raise GitSyncError(f"Failed to fetch updates from remote '{self.remote}': {err}") from err

        # 4. Check status and ahead/behind counts
        status = get_git_status(self.repo_path)

        if status.has_conflicts:
            raise MergeConflictError(
                f"Merge conflicts detected in repository: {', '.join(status.conflicted_files)}"
            )

        # If already up to date
        if status.behind == 0:
            return SyncResult(
                status=SyncStatus.UP_TO_DATE,
                message=f"Branch '{branch}' is up to date with '{self.remote}'.",
                current_branch=branch,
                initial_commit=initial_commit,
                synced_commit=initial_commit,
                ahead=status.ahead,
                behind=0,
            )

        # If diverged (both ahead and behind)
        if status.ahead > 0 and status.behind > 0:
            return SyncResult(
                status=SyncStatus.DIVERGED,
                message=(
                    f"Branch '{branch}' has diverged from '{self.remote}' "
                    f"({status.ahead} ahead, {status.behind} behind). "
                    "Fast-forward synchronization is not possible without manual merge."
                ),
                current_branch=branch,
                initial_commit=initial_commit,
                ahead=status.ahead,
                behind=status.behind,
            )

        # 5. Fast-forward pull
        try:
            pull_fast_forward(self.repo_path)
        except Exception as err:
            raise GitSyncError(f"Fast-forward synchronization failed: {err}") from err

        # 6. Verify resulting commit
        new_head_res = run_git(["rev-parse", "HEAD"], cwd=self.repo_path)
        synced_commit = new_head_res.stdout if new_head_res.success else "unknown"

        # Remote branch commit
        remote_head_res = run_git(["rev-parse", f"{self.remote}/{branch}"], cwd=self.repo_path)
        if remote_head_res.success and synced_commit != remote_head_res.stdout:
            raise GitSyncError(
                f"Verification failed: resulting commit '{synced_commit}' does not match "
                f"remote commit '{remote_head_res.stdout}'."
            )

        return SyncResult(
            status=SyncStatus.SYNCHRONIZED,
            message=f"Successfully synchronized '{branch}' with '{self.remote}' (new commit: {synced_commit[:8]}).",
            current_branch=branch,
            initial_commit=initial_commit,
            synced_commit=synced_commit,
            ahead=0,
            behind=0,
        )
