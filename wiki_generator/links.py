"""Wikilink validation, run once the wiki is written.

A broken link is worse than no link: in Obsidian it points at a note that will
never exist. Because resolution depends on every page already being written
(cartography included), the check runs at the end rather than during generation.
"""

from __future__ import annotations

import re
from pathlib import Path

from .journal import iter_pages

WIKILINK = re.compile(r"\[\[([^\]\[]+)\]\]")
FENCED_BLOCK = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def mask_fences(text: str) -> str:
    """Replace fenced blocks with spaces, preserving offsets and inline code.

    For citations the inline code IS the claim (`path/file.py:12`), so masking it
    like `mask_code` does would silence the check entirely instead of only
    ignoring the examples.
    """
    return FENCED_BLOCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def mask_code(text: str) -> str:
    """Replace code with spaces, preserving offsets.

    Obsidian does not interpret wikilinks inside code, and bash uses `[[ ]]` as
    test syntax — without this, a `[[ -f x ]]` in an example would be treated as
    a broken link.
    """
    masked = FENCED_BLOCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), masked)


def collect_notes(wiki_root: Path) -> tuple[set[str], dict[str, list[str]]]:
    """Existing notes: by path from the root, and by file name."""
    paths = {
        str(p.relative_to(wiki_root).as_posix())[:-3] for p in iter_pages(wiki_root)
    }
    by_name: dict[str, list[str]] = {}
    for path in paths:
        by_name.setdefault(path.split("/")[-1], []).append(path)
    return paths, by_name


def _resolves(target: str, paths: set[str], by_name: dict[str, list[str]]) -> bool:
    if target in paths:
        return True
    # Obsidian also resolves by note name when that name is unique in the vault.
    candidates = by_name.get(target.split("/")[-1], [])
    return target not in paths and len(candidates) == 1 and "/" not in target


def validate_and_fix(wiki_root: Path, *, fix: bool = True) -> dict:
    """Check every wikilink; optionally degrade broken ones to plain text.

    Returns a report with the totals and the list of unresolved links.
    """
    paths, by_name = collect_notes(wiki_root)
    checked = 0
    broken: list[tuple[str, str]] = []

    for page in iter_pages(wiki_root):
        text = page.read_text(encoding="utf-8")
        masked = mask_code(text)
        replacements: list[tuple[int, int, str]] = []

        for match in WIKILINK.finditer(masked):
            raw = text[match.start() + 2 : match.end() - 2]
            target = raw.split(r"\|")[0].split("|")[0].split("#")[0].strip()
            checked += 1
            if not target or not _resolves(target, paths, by_name):
                broken.append((str(page.relative_to(wiki_root)), target or "(vazio)"))
                display = raw.split(r"\|", 1)[-1] if r"\|" in raw else (
                    raw.split("|", 1)[-1] if "|" in raw else target.split("/")[-1]
                )
                replacements.append((match.start(), match.end(), display.strip()))

        if fix and replacements:
            for start, end, display in reversed(replacements):
                text = text[:start] + display + text[end:]
            page.write_text(text, encoding="utf-8")

    return {
        "checked": checked,
        "broken": len(broken),
        "details": broken,
        "notes": len(paths),
    }
