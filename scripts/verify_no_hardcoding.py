"""Grep the codebase for hardcoded category/location strings from config/*.yaml.

Run whenever a new vertical config is added, to confirm the pipeline stays
generic. Exits non-zero if any hardcoded reference is found outside config/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

# Files/dirs that are allowed to mention these strings (config data itself,
# generated caches, and this script's own source).
EXCLUDED_PATHS = {
    CONFIG_DIR,
    Path(__file__).resolve(),
}
EXCLUDED_DIR_NAMES = {".venv", "__pycache__", ".git", "data"}

# Only scan source files; README/docs are allowed to reference the example
# vertical for illustration.
SCAN_GLOBS = ["*.py"]


def _collect_config_strings() -> List[str]:
    strings: set[str] = set()
    for yaml_path in CONFIG_DIR.glob("*.yaml"):
        with yaml_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        for key in ("category", "location"):
            value = raw.get(key)
            if value:
                strings.add(str(value))
        for sub_area in raw.get("sub_areas", []) or []:
            strings.add(str(sub_area))
    # Ignore very short strings (e.g. accidental substrings of common words).
    return sorted(s for s in strings if len(s) >= 4)


def _iter_source_files() -> List[Path]:
    files: List[Path] = []
    for pattern in SCAN_GLOBS:
        for path in REPO_ROOT.rglob(pattern):
            if path.resolve() in EXCLUDED_PATHS:
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            if CONFIG_DIR in path.resolve().parents:
                continue
            files.append(path)
    return files


def find_hardcoded_references() -> List[Tuple[Path, int, str, str]]:
    """Returns (file, line_number, matched_string, line_text) tuples."""
    needles = _collect_config_strings()
    if not needles:
        return []

    pattern = re.compile(
        "|".join(re.escape(n) for n in needles), flags=re.IGNORECASE
    )

    hits: List[Tuple[Path, int, str, str]] = []
    for source_file in _iter_source_files():
        try:
            text = source_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = pattern.search(line)
            if match:
                hits.append(
                    (source_file.relative_to(REPO_ROOT), line_no, match.group(0), line.strip())
                )
    return hits


def main() -> int:
    hits = find_hardcoded_references()

    if not hits:
        print("OK: no hardcoded category/location/sub_area strings found outside config/.")
        return 0

    print("FOUND hardcoded vertical-specific strings outside config/:\n")
    for file_path, line_no, needle, line_text in hits:
        print(f"  {file_path}:{line_no}: matched '{needle}' -> {line_text}")
    print(f"\n{len(hits)} hardcoded reference(s) found. Remove or parameterize these.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
