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

from .journal import iter_pages
from .links import mask_fences

# `path/file.ext:123` inside inline code, which is how the prompts instruct
# references to be written.
CITATION = re.compile(r"`([\w./\-]+\.[A-Za-z0-9]{1,10}):(\d+)(?:-(\d+))?`")


def _looks_like_host(segment: str) -> bool:
    """`checkmarx.jfrog.io` yes; `.github` and `src` no."""
    if not segment or segment.startswith("."):
        return False
    head, _, tail = segment.rpartition(".")
    return bool(head) and len(tail) >= 2 and tail.isalpha()


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

    ends_with_newline: dict[str, bool] = {}

    def trailing_newline(rel: str) -> bool:
        if rel not in ends_with_newline:
            try:
                with (repo_root / rel).open("rb") as handle:
                    handle.seek(0, 2)
                    if handle.tell() == 0:
                        ends_with_newline[rel] = False
                    else:
                        handle.seek(-1, 2)
                        ends_with_newline[rel] = handle.read(1) == b"\n"
            except OSError:
                ends_with_newline[rel] = False
        return ends_with_newline[rel]

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
    phantom_line: list[tuple[str, str, int, int]] = []

    for page in iter_pages(wiki_root):
        page_rel = str(page.relative_to(wiki_root))
        # The verification report quotes invalid citations on purpose; counting
        # them here would make the invalid total grow with every error found.
        if page_rel.startswith(("09-verification/", "08-verification/")):
            continue
        # Citations inside fenced examples are illustrations, not claims about
        # this repository — counting them inflates the invalid total.
        for match in CITATION.finditer(mask_fences(page.read_text(encoding="utf-8"))):
            rel, start, end = match.group(1), int(match.group(2)), match.group(3)
            # Only counts as a citation if the path exists in the repository or
            # looks like a code path — avoids matching `version:1.2` and similar.
            if "/" not in rel and not (repo_root / rel).exists():
                continue
            # A container image with a dotted version and a numeric tag has the
            # same shape as a citation: `registry.example.io/img-9.7.7:977`
            # parses as file `...img-9.7.7` at line 977. Nothing in a repository
            # is addressed by hostname, so a leading host segment settles it.
            if _looks_like_host(rel.split("/")[0]) and len(rel.split("/")) > 1:
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
                # A file ending in a newline has a phantom empty line after it:
                # `wc -l` and this counter say N, an editor shows N+1. A citation
                # of N+1 is pointing at what the model was shown, not inventing
                # a location — two models from different vendors produced the
                # identical `ci.yml:234` on a 233-line file. Reporting that as
                # "invalid", next to "this file does not exist", overstates it.
                if last == count + 1 and trailing_newline(rel):
                    phantom_line.append((page_rel, rel, last, count))
                elif last > count or start < 1:
                    out_of_range.append((page_rel, rel, last, count))

    return {
        "checked": total,
        "unrooted": unrooted,
        "missing_file": missing_file,
        "out_of_range": out_of_range,
        "invalid": len(missing_file) + len(out_of_range),
        "unrooted_count": len(unrooted),
        "phantom_line": phantom_line,
    }


def format_report(result: dict, limit: int = 10) -> str:
    if not result["invalid"] and not result["unrooted_count"]:
        return (
            f"Citations: {result['checked']} checked, all point at existing "
            "files and lines."
        )
    phantom = len(result.get("phantom_line") or ())
    if not result["invalid"] and not result["unrooted_count"] and not phantom:
        return (
            f"Citations: {result['checked']} checked, all point at existing "
            "files and lines."
        )
    lines = [
        f"Citations: {result['checked']} checked, "
        f"{result['invalid']} invalid, "
        f"{result['unrooted_count']} with a path not rooted at the repository"
        + (f", {phantom} on the empty line a trailing newline creates" if phantom else ""),
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
