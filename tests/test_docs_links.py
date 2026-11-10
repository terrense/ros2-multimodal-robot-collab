"""The documentation's links must resolve.

This repository has grown a lot of cross-references — between the READMEs, the
phase specs, the audit notes and the code. A link that points at a file that was
renamed is invisible in review and useless to a reader, and the docs are the
main entry point for anyone picking this up.

The check is deliberately cheap: relative links to files must exist, and
in-page anchors must match a heading in the target file.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Directories whose markdown is not ours to police.
SKIP_DIRS = {"third_party", "install", "build", "log", ".git", "node_modules"}

LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def _markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.md"):
        relative = path.relative_to(REPO_ROOT)
        if SKIP_DIRS & set(relative.parts):
            continue
        files.append(path)
    return sorted(files)


def _anchor(text: str) -> str:
    """GitHub's anchor rule, close enough: lowercase, spaces to dashes."""
    cleaned = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"\s+", "-", cleaned)


def _anchors(path: Path) -> set[str]:
    """Every heading anchor in a file, plus the explicit ones."""
    text = path.read_text(encoding="utf-8")
    found = {_anchor(match) for match in HEADING_PATTERN.findall(text)}
    found |= set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text))
    return found


def _links(path: Path) -> list[tuple[str, int]]:
    text = path.read_text(encoding="utf-8")
    out: list[tuple[str, int]] = []
    for match in LINK_PATTERN.finditer(text):
        target = match.group(1).strip()
        # Skip images, external links and mailto.
        if target.startswith(("http://", "https://", "mailto:", "#")) or target.startswith("!"):
            continue
        out.append((target, text.count("\n", 0, match.start()) + 1))
    return out


def test_relative_links_resolve() -> None:
    """Every relative link in every markdown file points at something that exists."""
    broken: list[str] = []
    for path in _markdown_files():
        for target, line in _links(path):
            file_part, _, anchor = target.partition("#")
            if not file_part:
                continue
            resolved = (path.parent / file_part).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(REPO_ROOT)} line {line}: {target} (no such file)")
                continue
            if anchor and resolved.is_file() and resolved.suffix == ".md":
                if anchor not in _anchors(resolved):
                    broken.append(
                        f"{path.relative_to(REPO_ROOT)} line {line}: {target} (no such heading)"
                    )
    assert not broken, "\n".join(broken)


def test_the_repository_readme_points_at_the_real_quality_gate() -> None:
    """The gate script has to be mentioned, or nobody runs it."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/check_all.sh" in readme
    assert (REPO_ROOT / "scripts" / "check_all.sh").exists()


def test_every_phase_spec_has_a_banner_naming_its_line() -> None:
    """The phase numbering is ambiguous without it; that was the whole point."""
    for spec in sorted((REPO_ROOT / "docs" / "specs").glob("phase*/spec.md")):
        text = spec.read_text(encoding="utf-8")
        assert "OpenRUA assimilation — Phase O" in text, spec
        assert "Status: **complete" in text, spec


def test_the_specs_index_exists_and_lists_every_phase() -> None:
    index = REPO_ROOT / "docs" / "specs" / "README.md"
    assert index.exists()
    text = index.read_text(encoding="utf-8")
    for phase in range(8):
        assert f"**O{phase}**" in text, f"O{phase} is not listed in the specs index"
