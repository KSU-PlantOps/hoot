"""Release metadata for HOOT: the version, and its CHANGELOG section.

    python .github/scripts/release_notes.py --version   print the version
    python .github/scripts/release_notes.py --check     validate, print nothing
    python .github/scripts/release_notes.py             print the release notes

Used by the Release workflow, and by CI on every pull request so a release that
would fail is caught before it is merged.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$")


class ReleaseError(Exception):
    pass


def project_version(root: Path = ROOT) -> str:
    """The version in pyproject.toml, after checking hoot/__init__.py agrees."""
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    match = re.search(r'^__version__ = "([^"]+)"', (root / "hoot/__init__.py").read_text(), re.M)
    if not match:
        raise ReleaseError("hoot/__init__.py has no __version__")
    if match.group(1) != version:
        raise ReleaseError(
            f"version mismatch: pyproject.toml says {version}, "
            f"hoot/__init__.py says {match.group(1)}"
        )
    return version


def changelog_section(version: str, text: str) -> str:
    """The body of the ``## [version] - date`` section, without its heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and m.group("version") == version:
            if not m.group("date"):
                raise ReleaseError(f"CHANGELOG heading for {version} needs a date: "
                                   f"'## [{version}] - YYYY-MM-DD'")
            start = i + 1
            break
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section")
    end = next((j for j in range(start, len(lines)) if HEADING.match(lines[j])), len(lines))
    body = "\n".join(lines[start:end]).strip()
    if not body:
        raise ReleaseError(f"CHANGELOG section for {version} is empty")
    return body + "\n"


def is_prerelease(version: str) -> bool:
    return re.search(r"[a-z]", version) is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--version", action="store_true", help="print the version")
    mode.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args(argv)
    try:
        version = project_version()
        notes = changelog_section(version, (ROOT / "CHANGELOG.md").read_text())
    except ReleaseError as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1
    if args.version:
        print(version)
    elif not args.check:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
