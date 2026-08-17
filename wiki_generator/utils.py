"""Pure helpers (no network I/O, no global state)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_FENCE_START = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n")
_FENCE_END = re.compile(r"\n\s*```\s*$")
# `[text](target.md)` — does not match images `![...](...)`
_MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+\.md(?:#[^)\s]*)?)\)")


def slugify(value: str, max_len: int = 60) -> str:
    """Turn a path or title into a stable slug usable as a filename."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    if not slug:
        slug = "item"
    if len(slug) > max_len:
        # Keep the start readable and add a deterministic suffix to avoid collisions.
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[: max_len - 7].rstrip('-')}-{digest}"
    return slug


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def sha1_file(path: Path, max_bytes: int = 2_000_000) -> str:
    """Hash of the (truncated) file contents — used for change detection."""
    digest = hashlib.sha1()
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(max_bytes))
    except OSError:
        return "missing"
    return digest.hexdigest()


def hash_and_lines(path: Path, line_limit_bytes: int,
                   max_bytes: int = 2_000_000) -> tuple[str, int]:
    """Both facts about a file from one read.

    Scanning read every file twice — once to hash it, once to count its lines —
    and on a large repository over network storage that read is the whole cost.
    The hash is byte-identical to `sha1_file`, deliberately: it feeds every
    page's fingerprint, and changing it would mark every existing wiki stale.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            data = handle.read(max_bytes)
    except OSError:
        return "missing", 0
    digest = hashlib.sha1(data).hexdigest()
    if size > line_limit_bytes:
        return digest, 0  # matches _count_lines: too big to count
    lines = data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)
    return digest, lines


def read_text(path: Path, max_chars: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n... [truncated]"
    return text


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def strip_code_fence(text: str) -> str:
    """Strip a ```markdown ... ``` fence wrapping the entire response.

    Only strips when the fence opens on the first line and closes on the last —
    otherwise it would be destructive to pages that legitimately start with code.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    if stripped.count("```") % 2 != 0:
        return stripped
    without_start = _FENCE_START.sub("", stripped, count=1)
    if without_start == stripped:
        return stripped
    without_end = _FENCE_END.sub("", without_start, count=1)
    if without_end == without_start:
        return stripped
    return without_end.strip()


_PREAMBLE_OPENERS = (
    "now i", "let me", "i'll ", "i will ", "here is", "here's", "based on",
    "i have", "i've ", "looking at", "after reading", "ok,", "okay,", "great,",
    "perfect,", "agora ", "vou ", "aqui esta", "com base",
)


def trim_preamble(markdown: str, max_lookahead: int = 20) -> str:
    """Drop meta-commentary the model writes before the page actually starts.

    If there is an H1 in the first lines, everything before it is preamble.
    Otherwise it drops at most the leading lines opening with a typical narration
    formula — deliberately conservative, so it never eats real content.
    """
    lines = markdown.lstrip().splitlines()
    if not lines:
        return markdown

    for index, line in enumerate(lines[:max_lookahead]):
        if line.startswith("# "):
            return "\n".join(lines[index:]).strip()

    while lines:
        candidate = lines[0].strip().lower()
        if candidate and candidate.startswith(_PREAMBLE_OPENERS) and not candidate.startswith("#"):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def dedupe_leading_heading(markdown: str) -> str:
    """Collapse two identical consecutive H1s at the top of the page."""
    lines = markdown.splitlines()
    heads = [i for i, line in enumerate(lines[:6]) if line.startswith("# ")]
    if len(heads) >= 2 and lines[heads[0]].strip() == lines[heads[1]].strip():
        if all(not lines[i].strip() for i in range(heads[0] + 1, heads[1])):
            del lines[heads[0] : heads[1]]
    return "\n".join(lines)


def ensure_heading(markdown: str, title: str) -> str:
    """Ensure the page starts with an H1 (keeps the wiki uniform)."""
    text = markdown.lstrip()
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("# "):
            return text
        break
    return f"# {title}\n\n{text}"


def render_tree(paths: list[str], max_entries: int = 400) -> str:
    """Render an ASCII directory tree from relative paths."""
    tree: dict = {}
    for rel in sorted(paths):
        node = tree
        for part in Path(rel).parts:
            node = node.setdefault(part, {})

    lines: list[str] = []
    truncated = False

    def walk(node: dict, prefix: str) -> None:
        nonlocal truncated
        entries = sorted(node.items(), key=lambda kv: (not kv[1], kv[0].lower()))
        for index, (name, child) in enumerate(entries):
            if len(lines) >= max_entries:
                truncated = True
                return
            last = index == len(entries) - 1
            connector = "`-- " if last else "|-- "
            suffix = "/" if child else ""
            lines.append(f"{prefix}{connector}{name}{suffix}")
            if child:
                walk(child, prefix + ("    " if last else "|   "))

    walk(tree, "")
    if truncated:
        lines.append("... [tree truncated]")
    return "\n".join(lines)


def wikilink(target: str, display: str | None = None, *, in_table: bool = False) -> str:
    """Obsidian-style link: `[[path]]` or `[[path|text]]`.

    `target` is the path from the wiki root; the `.md` extension is stripped
    because Obsidian resolves notes without it.

    Inside a Markdown table the alias pipe would close the cell, so it has to be
    escaped — Obsidian accepts `\\|` inside a wikilink.
    """
    target = target[:-3] if target.endswith(".md") else target
    if not display or display == target:
        return f"[[{target}]]"
    separator = r"\|" if in_table else "|"
    return f"[[{target}{separator}{display}]]"


def markdown_links_to_wikilinks(text: str, base_dir: str = "") -> str:
    """Convert `[text](path.md)` into `[[path|text]]`.

    `base_dir` is the page's directory, used to resolve relative paths against
    the wiki root. External links (http, mailto) are left untouched.
    """

    def replace(match: re.Match) -> str:
        display, href = match.group(1), match.group(2)
        if href.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)
        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
            anchor = f"#{anchor}"
        resolved = _normalize_relative(f"{base_dir}/{href}" if base_dir else href)
        target = resolved[:-3] if resolved.endswith(".md") else resolved
        return f"[[{target}{anchor}|{display}]]"

    return _MD_LINK.sub(replace, text)


def _normalize_relative(path: str) -> str:
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def bullet_list(items: list[str], limit: int = 60) -> str:
    shown = items[:limit]
    lines = [f"- {item}" for item in shown]
    if len(items) > limit:
        lines.append(f"- ... (+{len(items) - limit} more)")
    return "\n".join(lines) if lines else "- (none)"


def chunked(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]
