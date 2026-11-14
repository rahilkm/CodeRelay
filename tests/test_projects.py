"""Tests for project registration and management (Phase 4)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coderelay.context import CONTEXT_FILENAME
from coderelay.exceptions import NotAGitRepositoryError
from coderelay.projects import Project, ProjectRegistry


def test_project_registry_register_existing_success(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()

    with patch("coderelay.projects.ensure_git_repo", return_value=repo_dir), \
         patch("coderelay.projects.get_remote_url", return_value="https://github.com/test/repo.git"), \
         patch("coderelay.projects.get_default_branch", return_value="main"):

        reg = ProjectRegistry(registry_file=reg_file)
        proj = reg.register_existing(repo_dir, name="MyCustomRepo")

        assert proj.name == "MyCustomRepo"
        assert proj.path == repo_dir
        assert proj.remote_url == "https://github.com/test/repo.git"
        assert proj.default_branch == "main"
        assert (repo_dir / CONTEXT_FILENAME).is_file()

        # Check retrieval
        assert reg.get_project("MyCustomRepo") is not None
        assert reg.get_project(repo_dir) is not None


def test_project_registry_register_invalid_repo_fails(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    non_repo = tmp_path / "plain_dir"
    non_repo.mkdir()

    reg = ProjectRegistry(registry_file=reg_file)
    with pytest.raises(NotAGitRepositoryError):
        reg.register_existing(non_repo)


def test_project_registry_clone_and_register(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    dest_dir = tmp_path / "cloned_repo"

    with patch("coderelay.projects.clone_repo") as mock_clone, \
         patch("coderelay.projects.ensure_git_repo", return_value=dest_dir), \
         patch("coderelay.projects.get_remote_url", return_value="https://github.com/org/cloned.git"), \
         patch("coderelay.projects.get_default_branch", return_value="develop"):

        reg = ProjectRegistry(registry_file=reg_file)
        dest_dir.mkdir()
        proj = reg.clone_and_register("https://github.com/org/cloned.git", dest_dir)

        mock_clone.assert_called_once()
        assert proj.remote_url == "https://github.com/org/cloned.git"
        assert proj.default_branch == "develop"


def test_project_registry_persistence(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    repo_dir = tmp_path / "repo1"
    repo_dir.mkdir()

    with patch("coderelay.projects.ensure_git_repo", return_value=repo_dir), \
         patch("coderelay.projects.get_remote_url", return_value=None), \
         patch("coderelay.projects.get_default_branch", return_value="main"):

        reg1 = ProjectRegistry(registry_file=reg_file)
        reg1.register_existing(repo_dir, name="Project1")

        # Load fresh registry instance from same file
        reg2 = ProjectRegistry(registry_file=reg_file)
        projects = reg2.list_projects()
        assert len(projects) == 1
        assert projects[0].name == "Project1"


def test_project_remove_does_not_delete_files(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    repo_dir = tmp_path / "repo_keep"
    repo_dir.mkdir()
    sample_file = repo_dir / "keep_me.txt"
    sample_file.write_text("important", encoding="utf-8")

    with patch("coderelay.projects.ensure_git_repo", return_value=repo_dir), \
         patch("coderelay.projects.get_remote_url", return_value=None), \
         patch("coderelay.projects.get_default_branch", return_value="main"):

        reg = ProjectRegistry(registry_file=reg_file)
        reg.register_existing(repo_dir, name="KeepRepo")

        # Remove project registration
        removed = reg.remove_project("KeepRepo")
        assert removed is True
        assert reg.get_project("KeepRepo") is None

        # Verify files are NOT deleted on disk
        assert repo_dir.is_dir()
        assert sample_file.is_file()
        assert sample_file.read_text(encoding="utf-8") == "important"
