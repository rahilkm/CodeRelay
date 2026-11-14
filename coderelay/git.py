"""Safe Git CLI wrapper and operations."""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from coderelay.exceptions import (
    GitError,
    GitNotInstalledError,
    GitSyncError,
    MergeConflictError,
    NotAGitRepositoryError,
)

logger = logging.getLogger(__name__)

# Patterns for sensitive files that must never be staged or committed
PROTECTED_PATTERNS = [
    ".env",
    ".env.*",
    "*.env",
    "*credential*",
    "*secret*",
    "*token*",
    "*.pem",
    "*.key",
    "*.pkcs12",
    "*.pfx",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    "id_dsa*",
    "*.p8",
    "auth.json",
    "credentials.json",
    "client_secret*.json",
    "coderelay.json",
    "*coderelay.json",
]


@dataclass(slots=True)
class GitResult:
    """Outcome of a Git command execution."""

    success: bool
    command: list[str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


@dataclass(slots=True)
class GitStatus:
    """Detailed parsed representation of Git working tree status."""

    current_branch: str
    tracking_branch: Optional[str] = None
    is_clean: bool = True
    ahead: int = 0
    behind: int = 0
    staged_files: list[str] = field(default_factory=list)
    unstaged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    conflicted_files: list[str] = field(default_factory=list)

    @property
    def has_uncommitted_changes(self) -> bool:
        """Check if there are any staged or unstaged modifications (excluding untracked)."""
        return bool(self.staged_files or self.unstaged_files or self.conflicted_files)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicted_files)


def is_git_installed() -> bool:
    """Check if Git CLI is available in PATH."""
    return shutil.which("git") is not None


def ensure_git_installed() -> None:
    """Raise GitNotInstalledError if Git is missing."""
    if not is_git_installed():
        raise GitNotInstalledError("Git CLI was not found in PATH. Please install Git.")


def run_git(
    args: list[str],
    cwd: Optional[Path] = None,
    check: bool = False,
    timeout: float = 30.0,
) -> GitResult:
    """Safely execute a Git CLI command using subprocess."""
    ensure_git_installed()

    cmd = ["git"] + args
    cwd_str = str(cwd.resolve()) if cwd else None

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd_str,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise GitError(f"Git command '{' '.join(cmd)}' timed out after {timeout}s") from err
    except Exception as err:
        raise GitError(f"Failed to execute Git command '{' '.join(cmd)}': {err}") from err

    result = GitResult(
        success=(proc.returncode == 0),
        command=cmd,
        stdout=proc.stdout.strip(),
        stderr=proc.stderr.strip(),
        exit_code=proc.returncode,
    )

    if check and not result.success:
        err_msg = result.stderr or result.stdout or f"exit code {result.exit_code}"
        raise GitError(f"Git command '{' '.join(cmd)}' failed: {err_msg}")

    return result


def is_git_repo(path: Path) -> bool:
    """Check if the path is inside a valid Git repository."""
    if not path.exists():
        return False
    res = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return res.success and res.stdout == "true"


def ensure_git_repo(path: Path) -> Path:
    """Ensure path is inside a Git repository and return repo root."""
    resolved = path.expanduser().resolve()
    if not is_git_repo(resolved):
        raise NotAGitRepositoryError(f"'{resolved}' is not a valid Git repository.")
    res = run_git(["rev-parse", "--show-toplevel"], cwd=resolved, check=True)
    return Path(res.stdout).resolve()


def get_current_branch(repo_path: Path) -> str:
    """Get the active branch name, or HEAD commit if detached."""
    ensure_git_repo(repo_path)
    res = run_git(["branch", "--show-current"], cwd=repo_path)
    if res.success and res.stdout:
        return res.stdout
    # Fallback for detached HEAD
    res = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, check=True)
    return res.stdout


def get_remote_url(repo_path: Path, remote: str = "origin") -> Optional[str]:
    """Get remote URL for origin or return None if not set."""
    ensure_git_repo(repo_path)
    res = run_git(["remote", "get-url", remote], cwd=repo_path)
    if res.success and res.stdout:
        return res.stdout
    # Try git config fallback
    res = run_git(["config", "--get", f"remote.{remote}.url"], cwd=repo_path)
    if res.success and res.stdout:
        return res.stdout
    return None


def get_default_branch(repo_path: Path, remote: str = "origin") -> str:
    """Determine the default branch (e.g. main/master) for the repository."""
    ensure_git_repo(repo_path)
    res = run_git(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], cwd=repo_path)
    if res.success and res.stdout:
        # e.g. refs/remotes/origin/main -> main
        return res.stdout.split("/")[-1]

    # Fallback to current branch or standard 'main'
    curr = get_current_branch(repo_path)
    return curr if curr and curr != "HEAD" else "main"


def get_git_status(repo_path: Path) -> GitStatus:
    """Get comprehensive, parsed working tree status using porcelain format."""
    root = ensure_git_repo(repo_path)
    branch = get_current_branch(root)

    # 1. Get branch tracking and ahead/behind counts via `git status --porcelain=v2 --branch`
    res = run_git(["status", "--porcelain=v2", "--branch"], cwd=root, check=True)
    lines = res.stdout.splitlines()

    tracking_branch: Optional[str] = None
    ahead = 0
    behind = 0
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []

    for line in lines:
        if line.startswith("# branch.upstream "):
            tracking_branch = line.split("# branch.upstream ")[1].strip()
        elif line.startswith("# branch.ab "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    ahead = int(parts[2].lstrip("+"))
                    behind = int(parts[3].lstrip("-"))
                except ValueError:
                    pass
        elif line.startswith("1 ") or line.startswith("2 "):
            # Format: '1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>'
            parts = line.split(maxsplit=8)
            if len(parts) >= 9:
                xy = parts[1]
                filepath = parts[8]
                if xy[0] not in (".", "?"):
                    staged.append(filepath)
                if xy[1] not in (".", "?"):
                    unstaged.append(filepath)
        elif line.startswith("u "):
            # Unmerged / conflicted
            parts = line.split(maxsplit=10)
            if len(parts) >= 11:
                conflicted.append(parts[10])
        elif line.startswith("? "):
            untracked.append(line[2:].strip())

    is_clean = not (staged or unstaged or conflicted or untracked)

    return GitStatus(
        current_branch=branch,
        tracking_branch=tracking_branch,
        is_clean=is_clean,
        ahead=ahead,
        behind=behind,
        staged_files=staged,
        unstaged_files=unstaged,
        untracked_files=untracked,
        conflicted_files=conflicted,
    )


def is_protected_file(filepath: str) -> bool:
    """Check if a file matches sensitive / credential patterns."""
    basename = Path(filepath).name
    lower_name = basename.lower()
    for pat in PROTECTED_PATTERNS:
        if fnmatch.fnmatch(lower_name, pat.lower()):
            return True
    return False


def clone_repo(url: str, destination: Path) -> GitResult:
    """Clone a Git repository using existing credentials. Never stores tokens."""
    ensure_git_installed()
    dest_path = destination.expanduser().resolve()

    if dest_path.exists() and any(dest_path.iterdir()):
        raise GitError(f"Clone destination directory '{dest_path}' already exists and is not empty.")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    res = run_git(["clone", url, str(dest_path)], timeout=180.0)
    if not res.success:
        err = res.stderr or res.stdout
        raise GitError(f"Failed to clone repository '{url}': {err}")

    return res


def fetch_remote(repo_path: Path, remote: str = "origin") -> GitResult:
    """Fetch updates from remote repository."""
    root = ensure_git_repo(repo_path)
    res = run_git(["fetch", remote, "--prune"], cwd=root)
    if not res.success:
        raise GitError(f"Failed to fetch from remote '{remote}': {res.stderr or res.stdout}")
    return res


def commit_changes(
    repo_path: Path,
    message: str,
    files: Optional[list[str]] = None,
) -> GitResult:
    """Safely commit changes without blindly staging or including protected files."""
    root = ensure_git_repo(repo_path)

    if not message or not message.strip():
        raise GitError("Commit message cannot be empty.")

    status = get_git_status(root)
    if status.has_conflicts:
        raise MergeConflictError(
            f"Cannot commit while merge conflicts exist: {', '.join(status.conflicted_files)}"
        )

    # Validate provided files or inspect changes
    if files:
        for f in files:
            if is_protected_file(f):
                raise GitError(f"Refusing to stage/commit protected sensitive file: {f}")
        # Stage only specified files
        for f in files:
            run_git(["add", f], cwd=root, check=True)
    else:
        # Check currently staged files for protected files
        for f in status.staged_files:
            if is_protected_file(f):
                raise GitError(f"Protected sensitive file is already staged for commit: {f}")
        if not status.staged_files:
            raise GitError("No files staged for commit. Specify files or stage them explicitly.")

    # Execute commit
    res = run_git(["commit", "-m", message.strip()], cwd=root)
    if not res.success:
        raise GitError(f"Commit failed: {res.stderr or res.stdout}")

    return res


def push_changes(
    repo_path: Path,
    remote: str = "origin",
    branch: Optional[str] = None,
) -> GitResult:
    """Push commits to remote branch. Destructive force-pushes are strictly disallowed."""
    root = ensure_git_repo(repo_path)
    target_branch = branch or get_current_branch(root)

    status = get_git_status(root)
    if status.behind > 0:
        raise GitSyncError(
            f"Cannot push: local branch '{target_branch}' is behind remote by {status.behind} commits. "
            "Synchronize first."
        )

    # Standard non-destructive push
    res = run_git(["push", remote, target_branch], cwd=root)
    if not res.success:
        raise GitError(f"Failed to push to '{remote}/{target_branch}': {res.stderr or res.stdout}")

    return res


def pull_fast_forward(repo_path: Path) -> GitResult:
    """Pull changes using strict fast-forward behavior only."""
    root = ensure_git_repo(repo_path)
    status = get_git_status(root)

    if status.has_uncommitted_changes:
        raise GitSyncError("Cannot pull: working tree has uncommitted modifications.")

    res = run_git(["pull", "--ff-only"], cwd=root)
    if not res.success:
        raise GitSyncError(f"Fast-forward pull failed: {res.stderr or res.stdout}")

    return res


def verify_remote_commit(repo_path: Path, remote: str = "origin", branch: Optional[str] = None) -> bool:
    """Verify that local HEAD exists on the remote branch."""
    root = ensure_git_repo(repo_path)
    target_branch = branch or get_current_branch(root)

    # Get local HEAD hash
    local_head = run_git(["rev-parse", "HEAD"], cwd=root)
    if not local_head.success:
        return False

    # Fetch latest remote references
    fetch_remote(root, remote)

    # Get remote commit hash
    remote_head = run_git(["rev-parse", f"{remote}/{target_branch}"], cwd=root)
    if not remote_head.success:
        return False

    return local_head.stdout == remote_head.stdout


def verify_remote_file(
    repo_path: Path,
    filename: str,
    remote: str = "origin",
    branch: Optional[str] = None,
) -> bool:
    """Verify that a specific file exists on the remote branch and matches local HEAD."""
    root = ensure_git_repo(repo_path)
    target_branch = branch or get_current_branch(root)

    # Fetch latest remote references
    fetch_remote(root, remote)

    local_blob = run_git(["rev-parse", f"HEAD:{filename}"], cwd=root)
    if not local_blob.success:
        return False

    remote_blob = run_git(["rev-parse", f"{remote}/{target_branch}:{filename}"], cwd=root)
    if not remote_blob.success:
        return False

    return bool(local_blob.stdout and local_blob.stdout == remote_blob.stdout)

