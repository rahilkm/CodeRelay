"""Project registration and management using a lightweight configuration-based registry."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from coderelay.context import CONTEXT_FILENAME, init_context
from coderelay.exceptions import (
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
)
from coderelay.git import (
    clone_repo,
    ensure_git_repo,
    get_default_branch,
    get_remote_url,
)

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_FILENAME = "projects.json"


def get_default_registry_path() -> Path:
    """Return default path to projects.json under user home or CODERELAY_HOME."""
    home_dir = os.environ.get("CODERELAY_HOME")
    if home_dir:
        base = Path(home_dir).expanduser().resolve()
    else:
        base = Path.home() / ".coderelay"
    base.mkdir(parents=True, exist_ok=True)
    return base / DEFAULT_REGISTRY_FILENAME


@dataclass(slots=True)
class Project:
    """A registered project managed by CodeRelay."""

    name: str
    path: Path
    remote_url: Optional[str] = None
    default_branch: str = "main"
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).astimezone())
    context_file: str = CONTEXT_FILENAME

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path.resolve()),
            "remote_url": self.remote_url,
            "default_branch": self.default_branch,
            "registered_at": self.registered_at.isoformat(),
            "context_file": self.context_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        reg_at = data.get("registered_at")
        registered_at = (
            datetime.fromisoformat(reg_at)
            if reg_at
            else datetime.now(timezone.utc).astimezone()
        )
        return cls(
            name=data["name"],
            path=Path(data["path"]),
            remote_url=data.get("remote_url"),
            default_branch=data.get("default_branch", "main"),
            registered_at=registered_at,
            context_file=data.get("context_file", CONTEXT_FILENAME),
        )


class ProjectRegistry:
    """Lightweight JSON configuration-based project registry."""

    def __init__(self, registry_file: Optional[Path] = None) -> None:
        self.registry_file = (
            registry_file.expanduser().resolve()
            if registry_file
            else get_default_registry_path()
        )
        self._projects: dict[str, Project] = {}
        self._load()

    def _load(self) -> None:
        """Load projects from disk if registry file exists."""
        if not self.registry_file.is_file():
            self._projects = {}
            return

        try:
            raw = self.registry_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            projects_dict = data.get("projects", {})
            self._projects = {
                name: Project.from_dict(proj_data)
                for name, proj_data in projects_dict.items()
            }
        except Exception as err:
            logger.warning("Failed to load project registry from %s: %s", self.registry_file, err)
            self._projects = {}

    def _save(self) -> None:
        """Persist registered projects to disk."""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "projects": {
                name: proj.to_dict()
                for name, proj in self._projects.items()
            },
        }
        self.registry_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def register_existing(self, path: Path, name: Optional[str] = None) -> Project:
        """Register an existing local Git repository."""
        root = ensure_git_repo(path)
        proj_name = name.strip() if (name and name.strip()) else root.name

        # Check for duplicates by name or path
        for existing in self._projects.values():
            if existing.path.resolve() == root.resolve():
                return existing

        remote_url = get_remote_url(root)
        default_branch = get_default_branch(root)

        # Initialize context file preserving existing contents
        init_context(root, project_name=proj_name)

        project = Project(
            name=proj_name,
            path=root,
            remote_url=remote_url,
            default_branch=default_branch,
        )
        self._projects[proj_name] = project
        self._save()
        return project

    def clone_and_register(
        self,
        url: str,
        destination: Path,
        name: Optional[str] = None,
    ) -> Project:
        """Clone a remote repository and register it."""
        dest = destination.expanduser().resolve()
        clone_repo(url, dest)
        return self.register_existing(dest, name=name)

    def list_projects(self) -> list[Project]:
        """Return list of all registered projects."""
        return list(self._projects.values())

    def get_project(self, name_or_path: str | Path) -> Optional[Project]:
        """Look up a registered project by its name or path."""
        target_str = str(name_or_path).strip()
        # Look up by name (case-insensitive)
        for name, proj in self._projects.items():
            if name.lower() == target_str.lower():
                return proj

        # Look up by path
        try:
            target_path = Path(name_or_path).expanduser().resolve()
            for proj in self._projects.values():
                if proj.path.resolve() == target_path:
                    return proj
        except Exception:
            pass

        return None

    def remove_project(self, name_or_path: str | Path) -> bool:
        """Remove a project from the registry without deleting its local files."""
        proj = self.get_project(name_or_path)
        if proj is None:
            return False

        self._projects.pop(proj.name, None)
        self._save()
        return True
