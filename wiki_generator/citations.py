"""Verificacao das citacoes `ficheiro:linha` contra o repositorio.

Depois de as afirmacoes inventadas serem eliminadas, o erro que sobra e a citacao
desalinhada. Parte dela e mecanicamente detetavel: um ficheiro que nao existe, ou
uma linha para la do fim do ficheiro, sao errados sem ambiguidade.

Limite honesto: uma citacao que aponte para uma linha existente mas errada (o caso
mais comum, `pubspec.yaml:62` quando e `:41`) nao e detetavel sem verificacao
semantica — este modulo nao a apanha e nao finge apanhar.
"""

from __future__ import annotations

import re
from pathlib import Path

# `caminho/ficheiro.ext:123` dentro de codigo inline, que e como os prompts
# mandam escrever as referencias.
CITATION = re.compile(r"`([\w./\-]+\.[A-Za-z0-9]{1,10}):(\d+)(?:-(\d+))?`")


def check(wiki_root: Path, repo_root: Path, repo_files: list[str] | None = None) -> dict:
    """Confronta cada citacao com o ficheiro real. Nao altera nada.

    Distingue um caminho inventado de um caminho apenas mal enraizado: se o
    sufixo citado corresponder a exatamente um ficheiro do repositorio, o
    ficheiro existe e o que falta e o prefixo — outro problema, outra correcao.
    """
    known = repo_files
    if known is None:
        # Fallback: sem a lista do scan, varre o repositorio aplicando os mesmos
        # filtros — caso contrario `node_modules` e worktrees produzem dezenas de
        # candidatos falsos para o mesmo sufixo.
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
            # So conta como citacao se o caminho existir no repositorio ou
            # parecer um caminho de codigo — evita apanhar `versao:1.2` e afins.
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
            f"Citacoes: {result['checked']} verificadas, todas apontam para "
            "ficheiros e linhas existentes."
        )
    lines = [
        f"Citacoes: {result['checked']} verificadas, "
        f"{result['invalid']} invalidas, "
        f"{result['unrooted_count']} com caminho nao enraizado no repositorio",
    ]
    for page, rel, real in result["unrooted"][:limit]:
        lines.append(f"  ~ {page}: `{rel}` -> deveria ser `{real}`")
    for page, rel in result["missing_file"][:limit]:
        lines.append(f"  ! {page}: `{rel}` nao existe no repositorio")
    for page, rel, cited, count in result["out_of_range"][:limit]:
        lines.append(f"  ! {page}: `{rel}:{cited}` mas o ficheiro tem {count} linhas")
    remaining = result["invalid"] - min(limit, len(result["missing_file"])) - min(
        limit, len(result["out_of_range"])
    )
    if remaining > 0:
        lines.append(f"  ... (+{remaining})")
    lines.append(
        "  Nota: citacoes dentro do intervalo mas desalinhadas nao sao detetaveis aqui."
    )
    return "\n".join(lines)
