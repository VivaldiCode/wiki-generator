"""Verification of `file:line` citations against the repository.

Once invented claims are eliminated, the error that remains is the misaligned
citation. Part of it is mechanically detectable: a file that does not exist, or a
line past the end of the file, are unambiguously wrong.

Honest limit: a citation pointing at an existing but wrong line (the most common
case, `pubspec.yaml:62` when it is `:41`) is not detectable without semantic
verification — this module does not catch it and does not pretend to.
"""

from __future__ import annotations

import re
from pathlib import Path

# `path/file.ext:123` inside inline code, which is how the prompts instruct
# references to be written.
CITATION = re.compile(r"`([\w./\-]+\.[A-Za-z0-9]{1,10}):(\d+)(?:-(\d+))?`")


def check(wiki_root: Path, repo_root: Path, repo_files: list[str] | None = None) -> dict:
    """Check each citation against the real file. Changes nothing.

    Distinguishes an invented path from a merely unrooted one: if the cited
    suffix matches exactly one file in the repository, the file exists and what
    is missing is the prefix — a different problem with a different fix.
    """
    known = repo_files
    if known is None:
        # Fallback: without the scan list, walk the repository applying the same
        # filters — otherwise `node_modules` and worktrees produce dozens of false
        # candidates for the same suffix.
        from .scanner import IGNORE_DIRS, IGNORE_PATH_FRAGMENTS

        known = []
        for f in repo_root.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(repo_root).as_posix())
            if any(part in IGNORE_DIRS for part in rel.split("/")[:-1]):
                continue
            if any(frag in f"/{rel}" for frag in IGNORE_PATH_FRAGMENTS):
                continue
            known.append(rel)
    line_counts: dict[str, int | None] = {}

    def lines_in(rel: str) -> int | None:
        if rel not in line_counts:
            path = repo_root / rel
            if not path.is_file():
                line_counts[rel] = None
            else:
                try:
                    with path.open("rb") as handle:
                        line_counts[rel] = sum(1 for _ in handle)
                except OSError:
                    line_counts[rel] = None
        return line_counts[rel]

    def suffix_matches(rel: str) -> list[str]:
        needle = "/" + rel
        return [k for k in known if k == rel or k.endswith(needle)]

    total = 0
    unrooted: list[tuple[str, str, str]] = []
    missing_file: list[tuple[str, str]] = []
    out_of_range: list[tuple[str, str, int, int]] = []

    for page in sorted(wiki_root.rglob("*.md")):
        page_rel = str(page.relative_to(wiki_root))
        for match in CITATION.finditer(page.read_text(encoding="utf-8")):
            rel, start, end = match.group(1), int(match.group(2)), match.group(3)
            # Only counts as a citation if the path exists in the repository or
            # looks like a code path — avoids matching `version:1.2` and similar.
            if "/" not in rel and not (repo_root / rel).exists():
                continue
            total += 1
            count = lines_in(rel)
            if count is None:
                candidates = suffix_matches(rel)
                if len(candidates) == 1:
                    unrooted.append((page_rel, rel, candidates[0]))
                else:
                    missing_file.append((page_rel, rel))
            else:
                last = int(end) if end else start
                if last > count or start < 1:
                    out_of_range.append((page_rel, rel, last, count))

    return {
        "checked": total,
        "unrooted": unrooted,
        "missing_file": missing_file,
        "out_of_range": out_of_range,
        "invalid": len(missing_file) + len(out_of_range),
        "unrooted_count": len(unrooted),
    }


def format_report(result: dict, limit: int = 10) -> str:
    if not result["invalid"] and not result["unrooted_count"]:
        return (
            f"Citations: {result['checked']} checked, all point at existing "
            "files and lines."
        )
    lines = [
        f"Citations: {result['checked']} checked, "
        f"{result['invalid']} invalid, "
        f"{result['unrooted_count']} with a path not rooted at the repository",
    ]
    for page, rel, real in result["unrooted"][:limit]:
        lines.append(f"  ~ {page}: `{rel}` -> should be `{real}`")
    for page, rel in result["missing_file"][:limit]:
        lines.append(f"  ! {page}: `{rel}` does not exist in the repository")
    for page, rel, cited, count in result["out_of_range"][:limit]:
        lines.append(f"  ! {page}: `{rel}:{cited}` but the file has {count} lines")
    remaining = result["invalid"] - min(limit, len(result["missing_file"])) - min(
        limit, len(result["out_of_range"])
    )
    if remaining > 0:
        lines.append(f"  ... (+{remaining})")
    lines.append(
        "  Note: citations that are in range but point at the wrong line are not "
        "detectable here."
    )
    return "\n".join(lines)
