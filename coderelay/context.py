"""Project context file (CODERELAY_CONTEXT.md) generation, maintenance, and validation."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from coderelay.exceptions import ContextError, ContextValidationError
from coderelay.git import (
    get_current_branch,
    get_default_branch,
    get_remote_url,
    is_git_repo,
)

logger = logging.getLogger(__name__)

CONTEXT_FILENAME = "CODERELAY_CONTEXT.md"

REQUIRED_SECTIONS = [
    "Project Overview",
    "Repository Information",
    "Tech Stack and Frameworks",
    "Project Architecture",
    "Important Directories and Files",
    "Completed Tasks",
    "Current Task and Progress",
    "Pending Tasks",
    "Technical Decisions",
    "Known Issues",
    "Testing Instructions and Results",
    "Next Agent Instructions",
]


def detect_tech_stack(workspace: Path) -> list[str]:
    """Inspect repository files to detect tech stack without fabricating."""
    stack: list[str] = []
    if (workspace / "pyproject.toml").exists() or (workspace / "requirements.txt").exists():
        stack.append("Python")
    if (workspace / "package.json").exists():
        stack.append("Node.js / JavaScript")
    if (workspace / "Cargo.toml").exists():
        stack.append("Rust")
    if (workspace / "go.mod").exists():
        stack.append("Go")
    return stack if stack else ["[To be determined]"]


def detect_key_files_and_dirs(workspace: Path) -> list[str]:
    """Inspect root directory entries without assuming architecture."""
    entries: list[str] = []
    for item in sorted(workspace.iterdir()):
        if item.name.startswith(".") or item.name in ("__pycache__", "venv", ".venv"):
            continue
        kind = "dir" if item.is_dir() else "file"
        entries.append(f"- `{item.name}` ({kind})")
    return entries[:15] if entries else ["- [To be determined]"]


def generate_context_template(
    project_name: str,
    workspace: Path,
    remote_url: Optional[str] = None,
    default_branch: Optional[str] = None,
) -> str:
    """Create initial structured template with verified repository details."""
    # Detect repository details if not provided
    if is_git_repo(workspace):
        if not remote_url:
            remote_url = get_remote_url(workspace)
        if not default_branch:
            default_branch = get_default_branch(workspace)

    url_str = remote_url if remote_url else "[Unverified / Local repository]"
    branch_str = default_branch if default_branch else "[Unverified]"

    stack_items = "\n".join(f"- {s}" for s in detect_tech_stack(workspace))
    files_items = "\n".join(detect_key_files_and_dirs(workspace))

    return f"""# {project_name} — CodeRelay Context

## Project Overview
[Brief overview of the project and its goals — To be determined]

## Repository Information
- **Repository URL**: {url_str}
- **Default Branch**: {branch_str}

## Tech Stack and Frameworks
{stack_items}

## Project Architecture
[High-level architecture description — To be determined]

## Important Directories and Files
{files_items}

## Completed Tasks
- [Initial repository setup completed]

## Current Task and Progress
- [To be determined]

## Pending Tasks
- [To be determined]

## Technical Decisions
- [Key architectural and design decisions — To be documented]

## Known Issues
- None documented.

## Testing Instructions and Results
- [Test suite execution instructions — e.g. pytest]

## Next Agent Instructions
- Read this context file thoroughly.
- Verify Git branch and status before making modifications.
- Continue with pending tasks and update this context file upon milestone completion.
"""


def init_context(
    workspace: Path,
    project_name: Optional[str] = None,
    force: bool = False,
) -> tuple[Path, bool]:
    """Initialize CODERELAY_CONTEXT.md in workspace.

    If file already exists and force is False, preserves existing contents.
    Returns (path_to_context_file, was_created).
    """
    resolved_ws = workspace.expanduser().resolve()
    context_file = resolved_ws / CONTEXT_FILENAME

    if context_file.is_file() and not force:
        logger.info("Preserving existing %s at %s", CONTEXT_FILENAME, context_file)
        return context_file, False

    name = project_name or resolved_ws.name
    template = generate_context_template(name, resolved_ws)
    context_file.write_text(template, encoding="utf-8")
    return context_file, True


def read_context(workspace: Path) -> str:
    """Read the full contents of CODERELAY_CONTEXT.md."""
    context_file = workspace.expanduser().resolve() / CONTEXT_FILENAME
    if not context_file.is_file():
        raise ContextError(f"Context file not found: {context_file}")
    return context_file.read_text(encoding="utf-8")


def parse_context_sections(content: str) -> dict[str, str]:
    """Parse markdown content into a dictionary of section title -> section content."""
    sections: dict[str, str] = {}
    pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)

    matches = list(pattern.finditer(content))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[title] = content[start:end].strip()

    return sections


def update_context_section(
    workspace: Path,
    section_name: str,
    new_content: str,
    append: bool = False,
) -> None:
    """Update or append to a specific section in CODERELAY_CONTEXT.md while preserving all other content."""
    context_file = workspace.expanduser().resolve() / CONTEXT_FILENAME
    if not context_file.is_file():
        init_context(workspace)

    full_text = context_file.read_text(encoding="utf-8")
    clean_section = section_name.strip().lstrip("#").strip()

    # Locate section with case-insensitive search
    pattern = re.compile(rf"^(##\s+{re.escape(clean_section)}\s*$)", re.MULTILINE | re.IGNORECASE)
    match = pattern.search(full_text)

    if not match:
        # Append new section at the end if not found
        prefix = "\n\n" if not full_text.endswith("\n\n") else ""
        updated_text = f"{full_text}{prefix}## {clean_section}\n{new_content.strip()}\n"
    else:
        # Find boundary of this section (next '## ' or end of file)
        start_idx = match.end()
        next_section_pattern = re.compile(r"^##\s+", re.MULTILINE)
        next_match = next_section_pattern.search(full_text, start_idx)
        end_idx = next_match.start() if next_match else len(full_text)

        existing_body = full_text[start_idx:end_idx].strip()
        if append and existing_body:
            updated_body = f"\n{existing_body}\n{new_content.strip()}\n\n"
        else:
            updated_body = f"\n{new_content.strip()}\n\n"

        updated_text = full_text[:start_idx] + updated_body + full_text[end_idx:]

    context_file.write_text(updated_text, encoding="utf-8")


def validate_context(content: str) -> tuple[bool, list[str]]:
    """Validate that required sections exist in the context content."""
    sections = parse_context_sections(content)
    missing: list[str] = []

    for req in REQUIRED_SECTIONS:
        # Case-insensitive section matching
        found = any(req.lower() == k.lower() for k in sections.keys())
        if not found:
            missing.append(req)

    is_valid = len(missing) == 0
    return is_valid, missing
