"""Modelos de dados partilhados entre scanner, planner e generator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileInfo:
    rel_path: str
    size: int
    lines: int
    language: str
    is_source: bool
    content_hash: str

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]


@dataclass
class ModuleInfo:
    key: str  # ex: "src/services"
    slug: str  # ex: "src-services"
    title: str  # ex: "src/services"
    files: list[FileInfo] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def languages(self) -> list[str]:
        counts: dict[str, int] = {}
        for f in self.files:
            counts[f.language] = counts.get(f.language, 0) + 1
        return [lang for lang, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


@dataclass
class RepoScan:
    root: str
    files: list[FileInfo]
    source_files: list[FileInfo]
    modules: list[ModuleInfo]
    languages: dict[str, int]  # linguagem -> nº de ficheiros
    key_files: list[str]  # manifestos, CI, docker, etc.
    entrypoints: list[str]
    doc_files: list[str]
    test_files: list[str]
    manifest_excerpts: dict[str, str]  # caminho -> conteudo truncado
    is_git_repo: bool
    sensitive_skipped: list[str]  # ficheiros com aspeto de credenciais, excluidos

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def primary_languages(self) -> list[str]:
        return [lang for lang, _ in sorted(self.languages.items(), key=lambda kv: -kv[1])][:6]


@dataclass
class PageSpec:
    key: str  # id estavel, usado no manifesto de cache
    path: str  # caminho relativo dentro da wiki, ex "02-architecture/overview.md"
    title: str
    section: str  # seccao do indice
    kind: str  # overview|architecture|module|reference|guide|operations
    order: int
    prompt: str
    scope_files: list[str] = field(default_factory=list)
    summary: str = ""  # descricao curta usada no indice
    # Strings que tem obrigatoriamente de aparecer na pagina gerada. Se faltarem,
    # o gerador repete a chamada a exigir explicitamente o que ficou de fora.
    required_markers: list[str] = field(default_factory=list)


@dataclass
class PageResult:
    spec: PageSpec
    status: str  # generated | cached | skipped | failed
    markdown: str = ""
    error: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0

    @property
    def ok(self) -> bool:
        return self.status in {"generated", "cached"}
