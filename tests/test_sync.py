"""Tests for safe synchronization service (Phase 4)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.exceptions import GitSyncError, MergeConflictError, UnsafeSyncError
from coderelay.git import GitResult, GitStatus
from coderelay.sync import GitSyncService, SyncStatus


def test_sync_refuses_on_uncommitted_changes(tmp_path: Path):
    with patch("coderelay.sync.ensure_git_repo", return_value=tmp_path), \
         patch("coderelay.sync.get_current_branch", return_value="main"), \
         patch("coderelay.sync.get_git_status") as mock_status:

        mock_status.return_value = GitStatus(current_branch="main", unstaged_files=["file.txt"])
        service = GitSyncService(tmp_path)

        with pytest.raises(UnsafeSyncError, match="uncommitted changes"):
            service.synchronize()


def test_sync_refuses_on_conflicts(tmp_path: Path):
    with patch("coderelay.sync.ensure_git_repo", return_value=tmp_path), \
         patch("coderelay.sync.get_current_branch", return_value="main"), \
         patch("coderelay.sync.get_git_status") as mock_status:

        mock_status.return_value = GitStatus(current_branch="main", conflicted_files=["conflict.py"])
        service = GitSyncService(tmp_path)

        with pytest.raises(UnsafeSyncError, match="Merge conflicts"):
            service.synchronize()



def test_sync_up_to_date(tmp_path: Path):
    status_clean = GitStatus(current_branch="main", ahead=0, behind=0)
    with patch("coderelay.sync.ensure_git_repo", return_value=tmp_path), \
         patch("coderelay.sync.get_current_branch", return_value="main"), \
         patch("coderelay.sync.get_remote_url", return_value="https://github.com/repo.git"), \
         patch("coderelay.sync.fetch_remote"), \
         patch("coderelay.sync.get_git_status", return_value=status_clean), \
         patch("coderelay.sync.run_git", return_value=GitResult(success=True, command=[], stdout="commit123")):

        service = GitSyncService(tmp_path)
        res = service.synchronize()
        assert res.status == SyncStatus.UP_TO_DATE
        assert "up to date" in res.message


def test_sync_diverged(tmp_path: Path):
    status_clean_local = GitStatus(current_branch="main", ahead=0, behind=0)
    status_diverged = GitStatus(current_branch="main", ahead=2, behind=3)

    with patch("coderelay.sync.ensure_git_repo", return_value=tmp_path), \
         patch("coderelay.sync.get_current_branch", return_value="main"), \
         patch("coderelay.sync.get_remote_url", return_value="https://github.com/repo.git"), \
         patch("coderelay.sync.fetch_remote"), \
         patch("coderelay.sync.get_git_status", side_effect=[status_clean_local, status_diverged]), \
         patch("coderelay.sync.run_git", return_value=GitResult(success=True, command=[], stdout="commit123")):

        service = GitSyncService(tmp_path)
        res = service.synchronize()
        assert res.status == SyncStatus.DIVERGED
        assert res.ahead == 2
        assert res.behind == 3


def test_sync_successful_fast_forward(tmp_path: Path):
    status_clean_local = GitStatus(current_branch="main", ahead=0, behind=0)
    status_behind = GitStatus(current_branch="main", ahead=0, behind=2)

    with patch("coderelay.sync.ensure_git_repo", return_value=tmp_path), \
         patch("coderelay.sync.get_current_branch", return_value="main"), \
         patch("coderelay.sync.get_remote_url", return_value="https://github.com/repo.git"), \
         patch("coderelay.sync.fetch_remote"), \
         patch("coderelay.sync.pull_fast_forward") as mock_pull, \
         patch("coderelay.sync.get_git_status", side_effect=[status_clean_local, status_behind]), \
         patch("coderelay.sync.run_git") as mock_run:

        # Mock HEAD commits before and after
        mock_run.side_effect = [
            GitResult(success=True, command=[], stdout="commit_old"),  # initial HEAD
            GitResult(success=True, command=[], stdout="commit_new"),  # new HEAD
            GitResult(success=True, command=[], stdout="commit_new"),  # remote HEAD
        ]

        service = GitSyncService(tmp_path)
        res = service.synchronize()

        assert res.status == SyncStatus.SYNCHRONIZED
        assert res.synced_commit == "commit_new"
        mock_pull.assert_called_once()
