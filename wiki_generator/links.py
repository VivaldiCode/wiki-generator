"""Validacao dos wikilinks depois de a wiki estar escrita.

Um link partido e pior do que nenhum link: no Obsidian fica a apontar para uma
nota que nunca vai existir. Como a resolucao depende de todas as paginas ja
escritas (incluindo a cartografia), a verificacao corre no fim e nao durante a
geracao.
"""

from __future__ import annotations

import re
from pathlib import Path

WIKILINK = re.compile(r"\[\[([^\]\[]+)\]\]")
FENCED_BLOCK = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def _mask_code(text: str) -> str:
    """Substitui codigo por espacos, preservando os offsets.

    O Obsidian nao interpreta wikilinks dentro de codigo, e o bash usa `[[ ]]`
    como sintaxe de teste — sem isto, um `[[ -f x ]]` num exemplo seria tratado
    como link partido.
    """
    masked = FENCED_BLOCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), masked)


def collect_notes(wiki_root: Path) -> tuple[set[str], dict[str, list[str]]]:
    """Notas existentes: por caminho a partir da raiz, e por nome de ficheiro."""
    paths = {
        str(p.relative_to(wiki_root).as_posix())[:-3] for p in wiki_root.rglob("*.md")
    }
    by_name: dict[str, list[str]] = {}
    for path in paths:
        by_name.setdefault(path.split("/")[-1], []).append(path)
    return paths, by_name


def _resolves(target: str, paths: set[str], by_name: dict[str, list[str]]) -> bool:
    if target in paths:
        return True
    # O Obsidian tambem resolve por nome de nota quando este e unico no vault.
    candidates = by_name.get(target.split("/")[-1], [])
    return target not in paths and len(candidates) == 1 and "/" not in target


def validate_and_fix(wiki_root: Path, *, fix: bool = True) -> dict:
    """Verifica todos os wikilinks; opcionalmente degrada os partidos para texto.

    Devolve um relatorio com os totais e a lista de links nao resolvidos.
    """
    paths, by_name = collect_notes(wiki_root)
    checked = 0
    broken: list[tuple[str, str]] = []

    for page in sorted(wiki_root.rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        masked = _mask_code(text)
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
