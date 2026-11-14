"""Tests for Git CLI wrapper and operations (Phase 4)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.exceptions import (
    GitError,
    GitNotInstalledError,
    GitSyncError,
    MergeConflictError,
    NotAGitRepositoryError,
)
from coderelay.git import (
    GitResult,
    GitStatus,
    commit_changes,
    ensure_git_installed,
    ensure_git_repo,
    get_current_branch,
    get_git_status,
    get_remote_url,
    is_git_installed,
    is_git_repo,
    is_protected_file,
    pull_fast_forward,
    push_changes,
    run_git,
    verify_remote_commit,
)


def test_is_git_installed():
    with patch("shutil.which", return_value="C:\\bin\\git.exe"):
        assert is_git_installed() is True
    with patch("shutil.which", return_value=None):
        assert is_git_installed() is False
        with pytest.raises(GitNotInstalledError):
            ensure_git_installed()


def test_is_git_repo(tmp_path: Path):
    with patch("coderelay.git.run_git") as mock_run:
        mock_run.return_value = GitResult(success=True, command=[], stdout="true")
        assert is_git_repo(tmp_path) is True

        mock_run.return_value = GitResult(success=False, command=[], stdout="")
        assert is_git_repo(tmp_path) is False


def test_ensure_git_repo_raises_not_a_repo(tmp_path: Path):
    with patch("coderelay.git.is_git_repo", return_value=False):
        with pytest.raises(NotAGitRepositoryError, match="not a valid Git repository"):
            ensure_git_repo(tmp_path)


def test_get_current_branch_and_remote(tmp_path: Path):
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.run_git") as mock_run:

        # Branch
        mock_run.return_value = GitResult(success=True, command=[], stdout="main")
        branch = get_current_branch(tmp_path)
        assert branch == "main"

        # Remote URL
        mock_run.return_value = GitResult(success=True, command=[], stdout="https://github.com/org/repo.git")
        url = get_remote_url(tmp_path)
        assert url == "https://github.com/org/repo.git"


def test_get_git_status_parsing(tmp_path: Path):
    sample_porcelain = (
        "# branch.oid 1234567890abcdef\n"
        "# branch.head main\n"
        "# branch.upstream origin/main\n"
        "# branch.ab +2 -1\n"
        "1 .M N... 100644 100644 100644 a1b2 c3d4 modified.txt\n"
        "1 M. N... 100644 100644 100644 e5f6 g7h8 staged.txt\n"
        "u 01 N... 100644 100644 100644 100644 a b c conflicted.txt\n"
        "? untracked.py\n"
    )
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.get_current_branch", return_value="main"), \
         patch("coderelay.git.run_git") as mock_run:

        mock_run.return_value = GitResult(success=True, command=[], stdout=sample_porcelain)
        st = get_git_status(tmp_path)

        assert st.current_branch == "main"
        assert st.tracking_branch == "origin/main"
        assert st.ahead == 2
        assert st.behind == 1
        assert "staged.txt" in st.staged_files
        assert "modified.txt" in st.unstaged_files
        assert "conflicted.txt" in st.conflicted_files
        assert "untracked.py" in st.untracked_files
        assert st.is_clean is False
        assert st.has_uncommitted_changes is True
        assert st.has_conflicts is True


def test_secret_file_protection():
    assert is_protected_file(".env") is True
    assert is_protected_file(".env.local") is True
    assert is_protected_file("config.env") is True
    assert is_protected_file("credentials.json") is True
    assert is_protected_file("auth_token.txt") is True
    assert is_protected_file("id_rsa") is True
    assert is_protected_file("server.key") is True
    assert is_protected_file("cert.pem") is True
    assert is_protected_file("main.py") is False
    assert is_protected_file("README.md") is False


def test_commit_rejects_empty_message(tmp_path: Path):
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.run_git", return_value=GitResult(success=True, command=[], stdout=str(tmp_path))):
        with pytest.raises(GitError, match="Commit message cannot be empty"):
            commit_changes(tmp_path, message="  ")


def test_commit_rejects_secret_files(tmp_path: Path):
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.run_git", return_value=GitResult(success=True, command=[], stdout=str(tmp_path))), \
         patch("coderelay.git.get_git_status", return_value=GitStatus(current_branch="main")):

        with pytest.raises(GitError, match="Refusing to stage/commit protected sensitive file"):
            commit_changes(tmp_path, message="Update env", files=[".env"])


def test_commit_rejects_conflicts(tmp_path: Path):
    status = GitStatus(current_branch="main", conflicted_files=["conflict.txt"])
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.run_git", return_value=GitResult(success=True, command=[], stdout=str(tmp_path))), \
         patch("coderelay.git.get_git_status", return_value=status):

        with pytest.raises(MergeConflictError, match="merge conflicts exist"):
            commit_changes(tmp_path, message="Resolve", files=["file.py"])


def test_push_rejects_behind_remote(tmp_path: Path):
    status = GitStatus(current_branch="main", behind=3)
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.run_git", return_value=GitResult(success=True, command=[], stdout=str(tmp_path))), \
         patch("coderelay.git.get_git_status", return_value=status):

        with pytest.raises(GitSyncError, match="behind remote by 3 commits"):
            push_changes(tmp_path)


def test_pull_fast_forward_rejects_uncommitted(tmp_path: Path):
    status = GitStatus(current_branch="main", unstaged_files=["app.py"])
    with patch("coderelay.git.is_git_repo", return_value=True), \
         patch("coderelay.git.run_git", return_value=GitResult(success=True, command=[], stdout=str(tmp_path))), \
         patch("coderelay.git.get_git_status", return_value=status):

        with pytest.raises(GitSyncError, match="uncommitted modifications"):
            pull_fast_forward(tmp_path)


def test_verify_remote_commit(tmp_path: Path):
    def fake_git(args, cwd=None, **kwargs):
        if "rev-parse" in args and "--is-inside-work-tree" in args:
            return GitResult(success=True, command=args, stdout="true")
        if "rev-parse" in args and "--show-toplevel" in args:
            return GitResult(success=True, command=args, stdout=str(tmp_path))
        if "branch" in args and "--show-current" in args:
            return GitResult(success=True, command=args, stdout="main")
        if "rev-parse" in args and "HEAD" in args:
            return GitResult(success=True, command=args, stdout="abc1234")
        if "rev-parse" in args and "origin/main" in args:
            return GitResult(success=True, command=args, stdout="abc1234")
        if "fetch" in args:
            return GitResult(success=True, command=args, stdout="")
        return GitResult(success=True, command=args, stdout="")

    with patch("coderelay.git.run_git", side_effect=fake_git):
        assert verify_remote_commit(tmp_path, remote="origin", branch="main") is True

