"""The release workflow's metadata script (.github/scripts/release_notes.py)."""

import importlib.util
import re
from pathlib import Path

import pytest

import hoot

_SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/release_notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SCRIPT)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

## [1.1.0] - 2026-09-28

### Added

- A thing.

## [1.0.0] - 2026-09-01

First public release.
"""


def test_repo_version_is_consistent_and_has_release_notes():
    """The same check CI runs: a merge to main must be releasable."""
    version = release_notes.project_version()
    assert version == hoot.__version__
    assert release_notes.main(["--check"]) == 0


def test_extracts_only_the_requested_section():
    assert release_notes.changelog_section("1.1.0", CHANGELOG) == "### Added\n\n- A thing.\n"
    assert release_notes.changelog_section("1.0.0", CHANGELOG) == "First public release.\n"


def test_missing_section_is_an_error():
    with pytest.raises(release_notes.ReleaseError, match=re.escape("no '## [2.0.0]")):
        release_notes.changelog_section("2.0.0", CHANGELOG)


def test_undated_or_empty_section_is_an_error():
    with pytest.raises(release_notes.ReleaseError, match="needs a date"):
        release_notes.changelog_section("1.2.0", "## [1.2.0]\n\n- x\n")
    with pytest.raises(release_notes.ReleaseError, match="empty"):
        release_notes.changelog_section("1.2.0", "## [1.2.0] - 2026-10-01\n\n## [1.1.0] - 2026-09-28\n- x\n")


def test_version_mismatch_is_an_error(tmp_path):
    (tmp_path / "hoot").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n')
    (tmp_path / "hoot/__init__.py").write_text('__version__ = "1.0.0"\n')
    with pytest.raises(release_notes.ReleaseError, match="version mismatch"):
        release_notes.project_version(tmp_path)


def test_prerelease_detection():
    assert release_notes.is_prerelease("1.2.0rc1")
    assert not release_notes.is_prerelease("1.2.0")
