"""Helpers puros (sem I/O de rede, sem estado global)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_FENCE_START = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n")
_FENCE_END = re.compile(r"\n\s*```\s*$")
# `[texto](destino.md)` — nao apanha imagens `![...](...)`
_MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+\.md(?:#[^)\s]*)?)\)")


def slugify(value: str, max_len: int = 60) -> str:
    """Converte um caminho/titulo num slug estavel usavel como nome de ficheiro."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    if not slug:
        slug = "item"
    if len(slug) > max_len:
        # Mantem o inicio legivel e junta um sufixo determinista para evitar colisoes.
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[: max_len - 7].rstrip('-')}-{digest}"
    return slug


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def sha1_file(path: Path, max_bytes: int = 2_000_000) -> str:
    """Hash do conteudo do ficheiro (truncado) — usado para deteccao de alteracoes."""
    digest = hashlib.sha1()
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(max_bytes))
    except OSError:
        return "missing"
    return digest.hexdigest()


def read_text(path: Path, max_chars: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n... [truncado]"
    return text


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def strip_code_fence(text: str) -> str:
    """Remove uma cerca ```markdown ... ``` que envolva a resposta inteira.

    So remove quando a cerca abre na primeira linha e fecha na ultima — caso
    contrario seria destrutivo para paginas que legitimamente comecam com codigo.
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
    """Remove comentario meta que o modelo escreva antes do inicio real da pagina.

    Se existir um H1 nas primeiras linhas, tudo antes dele e preambulo. Caso
    contrario, remove no maximo as linhas iniciais que abrem com uma formula
    tipica de narracao — conservador de proposito, para nao comer conteudo.
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
    """Colapsa dois H1 identicos consecutivos no topo da pagina."""
    lines = markdown.splitlines()
    heads = [i for i, line in enumerate(lines[:6]) if line.startswith("# ")]
    if len(heads) >= 2 and lines[heads[0]].strip() == lines[heads[1]].strip():
        if all(not lines[i].strip() for i in range(heads[0] + 1, heads[1])):
            del lines[heads[0] : heads[1]]
    return "\n".join(lines)


def ensure_heading(markdown: str, title: str) -> str:
    """Garante que a pagina comeca com um H1 (padroniza a wiki)."""
    text = markdown.lstrip()
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("# "):
            return text
        break
    return f"# {title}\n\n{text}"


def render_tree(paths: list[str], max_entries: int = 400) -> str:
    """Renderiza uma arvore de diretorios ASCII a partir de caminhos relativos."""
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
        lines.append("... [arvore truncada]")
    return "\n".join(lines)


def wikilink(target: str, display: str | None = None, *, in_table: bool = False) -> str:
    """Link no estilo Obsidian: `[[caminho]]` ou `[[caminho|texto]]`.

    `target` e o caminho a partir da raiz da wiki; a extensao `.md` e removida
    porque o Obsidian resolve as notas sem ela.

    Dentro de uma tabela markdown o `|` do alias fecharia a celula, por isso tem
    de ser escapado — o Obsidian aceita `\\|` no interior de um wikilink.
    """
    target = target[:-3] if target.endswith(".md") else target
    if not display or display == target:
        return f"[[{target}]]"
    separator = r"\|" if in_table else "|"
    return f"[[{target}{separator}{display}]]"


def markdown_links_to_wikilinks(text: str, base_dir: str = "") -> str:
    """Converte `[texto](caminho.md)` em `[[caminho|texto]]`.

    `base_dir` e o diretorio da pagina, para resolver caminhos relativos contra
    a raiz da wiki. Links externos (http, mailto) ficam intactos.
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
        lines.append(f"- ... (+{len(items) - limit} mais)")
    return "\n".join(lines) if lines else "- (nenhum)"


def chunked(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]
