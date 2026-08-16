"""Wiki navigation files. Plain Markdown, Obsidian wikilinks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import WikiConfig
from .models import PageResult, RepoScan
from .planner import SECTION_ORDER
from .i18n import translator
from .utils import human_size, wikilink


# Enough to see the pattern, short enough that the index stays an index.
FAILED_PAGE_CAP = 10


def _failure_state(result: PageResult, t) -> str:
    if not result.kept_previous:
        return t("index.failed.missing")
    if result.previous_is_current:
        return t("index.failed.current")
    return t("index.failed.stale")


def _one_line(text: str, limit: int = 220) -> str:
    """Collapse an error to a single readable line, safe inside a table or list."""
    flat = " ".join(str(text).split()).replace("|", "\\|")
    if not flat:
        return "no diagnostic"
    return flat[:limit] + ("..." if len(flat) > limit else "")


def _group_by_section(results: list[PageResult]) -> dict[str, list[PageResult]]:
    grouped: dict[str, list[PageResult]] = {}
    for result in results:
        grouped.setdefault(result.spec.section, []).append(result)
    for pages in grouped.values():
        pages.sort(key=lambda r: r.spec.order)
    return grouped


def _ordered_sections(grouped: dict[str, list[PageResult]]) -> list[str]:
    known = [s for s in SECTION_ORDER if s in grouped]
    extra = sorted(s for s in grouped if s not in SECTION_ORDER)
    return known + extra


# ----------------------------------------------------------------------
def write_index(
    config: WikiConfig, scan: RepoScan, results: list[PageResult]
) -> Path:
    # A page whose file is on disk belongs in the index even if this run failed to
    # refresh it: excluding it would silently shrink the wiki because of a
    # transient error, and the reader would lose a page that opens perfectly well.
    usable = [r for r in results if r.readable]
    grouped = _group_by_section(usable)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    languages = ", ".join(
        f"{lang} ({count})"
        for lang, count in sorted(scan.languages.items(), key=lambda kv: -kv[1])[:6]
    ) or "n/d"
    total_bytes = sum(f.size for f in scan.files)

    t = translator(config.language)
    lines = [
        f"# {t('index.title', project=config.resolved_project_name)}",
        "",
        t("index.subtitle"),
        "",
        "| | |",
        "|---|---|",
        f"| {t('index.repository')} | `{scan.root}` |",
        f"| {t('index.files')} | {len(scan.files)} ({human_size(total_bytes)}) |",
        f"| {t('index.lines')} | {scan.total_lines} |",
        f"| {t('index.languages')} | {languages} |",
        f"| {t('index.modules')} | {len(scan.modules)} |",
        f"| {t('index.pages')} | {len(usable)} |",
        f"| {t('index.model')} | `{config.model}` |",
        f"| {t('index.generated_at')} | {generated_at} |",
        "",
        f"## {t('index.contents')}",
        "",
    ]

    for section in _ordered_sections(grouped):
        lines.append(f"### {t(section)}")
        lines.append("")
        for result in grouped[section]:
            spec = result.spec
            summary = f" — {spec.summary}" if spec.summary else ""
            lines.append(f"- {wikilink(spec.path, spec.title)}{summary}")
        lines.append("")

    failed = [r for r in results if r.status == "failed"]
    if failed:
        lines += [f"### {t('index.failed')}", "", t("index.failed.intro"), ""]
        # One error repeated over nineteen pages is one error, not nineteen. A run
        # usually fails for a single reason, and listing it once per page buries
        # what the reader needs to fix.
        groups: dict[str, list[PageResult]] = {}
        for result in failed:
            groups.setdefault(_one_line(result.error), []).append(result)
        for error, pages in groups.items():
            states = {_failure_state(result, t) for result in pages}
            # When every page in the group failed the same way, the state belongs in
            # the heading once, not repeated down twenty bullet points.
            shared = states.pop() if len(states) == 1 else ""
            lines += [f"**{error}**" + (f" — {shared}" if shared else ""), ""]
            for result in pages[:FAILED_PAGE_CAP]:
                suffix = "" if shared else f" — {_failure_state(result, t)}"
                lines.append(
                    f"- {wikilink(result.spec.path, result.spec.title)}{suffix}"
                )
            if len(pages) > FAILED_PAGE_CAP:
                lines.append(
                    t("index.failed.more", count=len(pages) - FAILED_PAGE_CAP)
                )
            lines.append("")

        if any(not r.previous_is_current for r in failed):
            lines += [t("index.failed.retry", section=t("index.regenerate")), ""]
        missing = [r for r in failed if not r.kept_previous]
        if missing:
            lines += [t("index.failed.links", count=len(missing)), ""]

    lines += [
        f"## {t('index.regenerate')}",
        "",
        "```bash",
        f"wiki-generator --source {scan.root} --output {config.output_path.parent}",
        "```",
        "",
        t("index.incremental"),
        "",
        t("index.disclaimer"),
    ]

    target = config.output_path / "README.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ----------------------------------------------------------------------
def write_summary(config: WikiConfig, results: list[PageResult]) -> Path:
    """SUMMARY.md — linear index, useful as an entry note in a vault."""
    t = translator(config.language)
    grouped = _group_by_section([r for r in results if r.readable])
    lines = [f"# {t('summary.title')}", "", f"- {wikilink('README', t('summary.index'))}", ""]
    for section in _ordered_sections(grouped):
        lines.append(f"## {t(section)}")
        lines.append("")
        for result in grouped[section]:
            lines.append(f"- {wikilink(result.spec.path, result.spec.title)}")
        lines.append("")
    target = config.output_path / "SUMMARY.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ----------------------------------------------------------------------
def assemble(config: WikiConfig, scan: RepoScan, results: list[PageResult]) -> list[Path]:
    return [
        write_index(config, scan, results),
        write_summary(config, results),
    ]
