"""Every relative link in the README and the docs/ guides resolves.

The guides were split out of the README; a link to a moved section, or
to a heading that was renamed, is what this catches.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _slug(heading: str) -> str:
    text = re.sub(r"[`*]", "", heading.strip()).lower()
    return re.sub(r"[^\w\s-]", "", text).replace(" ", "-")


def _anchors(page: Path) -> set[str]:
    anchors, fence = set(), False
    for line in page.read_text().split("\n"):
        if line.startswith("```"):
            fence = not fence
        elif not fence and re.match(r"#{1,6} ", line):
            anchors.add(_slug(line.lstrip("# ")))
    return anchors


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(page: Path) -> None:
    for target in LINK.findall(page.read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (page.parent / path).resolve() if path else page
        assert file.exists(), f"{page.name}: {target} does not exist"
        if anchor:
            assert file.is_file(), f"{page.name}: {target} has an anchor on a directory"
            assert anchor in _anchors(file), f"{page.name}: no heading for {target}"
