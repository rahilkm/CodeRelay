"""Tests for project context management (Phase 4)."""

from pathlib import Path

import pytest

from coderelay.context import (
    CONTEXT_FILENAME,
    init_context,
    parse_context_sections,
    read_context,
    update_context_section,
    validate_context,
)
from coderelay.exceptions import ContextError


def test_init_context_creates_new_file(tmp_path: Path):
    ctx_file, created = init_context(tmp_path, project_name="TestProject")
    assert created is True
    assert ctx_file.is_file()
    assert ctx_file.name == CONTEXT_FILENAME

    content = ctx_file.read_text(encoding="utf-8")
    assert "# TestProject — CodeRelay Context" in content
    assert "## Project Overview" in content
    assert "## Next Agent Instructions" in content


def test_init_context_preserves_existing_content(tmp_path: Path):
    ctx_file = tmp_path / CONTEXT_FILENAME
    custom_content = "# Custom Project\n\nExisting detailed notes that must not be deleted."
    ctx_file.write_text(custom_content, encoding="utf-8")

    path, created = init_context(tmp_path, project_name="Overwritten?")
    assert created is False
    assert path == ctx_file
    assert ctx_file.read_text(encoding="utf-8") == custom_content


def test_read_context(tmp_path: Path):
    with pytest.raises(ContextError, match="Context file not found"):
        read_context(tmp_path)

    init_context(tmp_path, project_name="ReaderTest")
    content = read_context(tmp_path)
    assert "ReaderTest" in content


def test_validate_context_success_and_missing(tmp_path: Path):
    init_context(tmp_path, project_name="ValidatorTest")
    content = read_context(tmp_path)

    valid, missing = validate_context(content)
    assert valid is True
    assert len(missing) == 0

    # Incomplete content missing sections
    incomplete = "# Title\n\n## Project Overview\nOverview text\n"
    valid_inc, missing_inc = validate_context(incomplete)
    assert valid_inc is False
    assert "Repository Information" in missing_inc
    assert "Next Agent Instructions" in missing_inc


def test_update_context_section_replace_and_append(tmp_path: Path):
    init_context(tmp_path, project_name="UpdateTest")

    # Replace section
    update_context_section(tmp_path, "Current Task and Progress", "Phase 4 implementation in progress")
    content = read_context(tmp_path)
    assert "Phase 4 implementation in progress" in content

    # Append to section
    update_context_section(tmp_path, "Current Task and Progress", "Added unit tests", append=True)
    content2 = read_context(tmp_path)
    assert "Phase 4 implementation in progress" in content2
    assert "Added unit tests" in content2
