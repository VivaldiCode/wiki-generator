"""Gera os ficheiros de navegacao da wiki. Markdown puro, wikilinks do Obsidian."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import WikiConfig
from .models import PageResult, RepoScan
from .planner import SECTION_ORDER
from .utils import human_size, wikilink


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
    usable = [r for r in results if r.ok]
    grouped = _group_by_section(usable)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    languages = ", ".join(
        f"{lang} ({count})"
        for lang, count in sorted(scan.languages.items(), key=lambda kv: -kv[1])[:6]
    ) or "n/d"
    total_bytes = sum(f.size for f in scan.files)

    lines = [
        f"# Wiki — {config.resolved_project_name}",
        "",
        "Documentacao gerada automaticamente a partir do codigo-fonte.",
        "",
        "| | |",
        "|---|---|",
        f"| Repositorio | `{scan.root}` |",
        f"| Ficheiros analisados | {len(scan.files)} ({human_size(total_bytes)}) |",
        f"| Linhas de codigo | {scan.total_lines} |",
        f"| Linguagens | {languages} |",
        f"| Modulos documentados | {len(scan.modules)} |",
        f"| Paginas | {len(usable)} |",
        f"| Modelo | `{config.model}` |",
        f"| Gerado em | {generated_at} |",
        "",
        "## Indice",
        "",
    ]

    for section in _ordered_sections(grouped):
        lines.append(f"### {section}")
        lines.append("")
        for result in grouped[section]:
            spec = result.spec
            summary = f" — {spec.summary}" if spec.summary else ""
            lines.append(f"- {wikilink(spec.path, spec.title)}{summary}")
        lines.append("")

    failed = [r for r in results if r.status == "failed"]
    if failed:
        lines += ["### Paginas com falha", ""]
        lines += [f"- `{r.spec.path}` — {r.error[:200]}" for r in failed]
        lines.append("")

    lines += [
        "## Como regerar",
        "",
        "```bash",
        f"wiki-generator --repo {scan.root} --out {config.output_path}",
        "```",
        "",
        "Apenas as paginas cujos ficheiros de origem mudaram sao regeradas. "
        "Usa `--force` para regerar tudo.",
        "",
        "> Estas paginas sao geradas por um modelo a partir do codigo. "
        "Trata-as como um mapa util, nao como a fonte de verdade — o codigo e que manda.",
    ]

    target = config.output_path / "README.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ----------------------------------------------------------------------
def write_summary(config: WikiConfig, results: list[PageResult]) -> Path:
    """SUMMARY.md — indice linear, util como nota de entrada num vault."""
    grouped = _group_by_section([r for r in results if r.ok])
    lines = ["# Summary", "", f"- {wikilink('README', 'Indice')}", ""]
    for section in _ordered_sections(grouped):
        lines.append(f"## {section}")
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
