"""Generated blocks inside a hand-edited document.

`docs/results.md` may carry prose that people edit by hand. Everything between
markers is owned by the generator and overwritten; everything outside survives.
"""

from __future__ import annotations


def begin_marker(name: str) -> str:
    return f"<!-- BEGIN GENERATED: {name} -->"


def end_marker(name: str) -> str:
    return f"<!-- END GENERATED: {name} -->"


def extract_block(document: str, name: str) -> str | None:
    begin, end = begin_marker(name), end_marker(name)
    start = document.find(begin)
    stop = document.find(end)
    if start == -1 or stop == -1 or stop < start:
        return None
    return document[start + len(begin) : stop]


def replace_block(document: str, name: str, body: str) -> str:
    """Replace the generated block, or append it when the document has none."""
    block = f"{begin_marker(name)}\n{body.rstrip()}\n{end_marker(name)}"
    existing = extract_block(document, name)
    if existing is None:
        separator = "" if document.endswith("\n\n") else "\n\n"
        return f"{document.rstrip()}{separator}{block}\n"
    begin = begin_marker(name)
    start = document.index(begin)
    stop = document.index(end_marker(name), start) + len(end_marker(name))
    return document[:start] + block + document[stop:]
