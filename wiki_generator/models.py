"""Data models shared by the scanner, planner and generator."""

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
    key: str  # e.g. "src/services"
    slug: str  # e.g. "src-services"
    title: str  # e.g. "src/services"
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
    languages: dict[str, int]  # language -> file count
    key_files: list[str]  # manifests, CI, docker, etc.
    entrypoints: list[str]
    doc_files: list[str]
    test_files: list[str]
    manifest_excerpts: dict[str, str]  # path -> truncated contents
    is_git_repo: bool
    sensitive_skipped: list[str]  # credential-looking files that were excluded

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def primary_languages(self) -> list[str]:
        return [lang for lang, _ in sorted(self.languages.items(), key=lambda kv: -kv[1])][:6]


@dataclass
class PageSpec:
    key: str  # stable id, used in the cache manifest
    path: str  # path relative to the wiki root, e.g. "02-architecture/overview.md"
    title: str
    section: str  # index section key
    kind: str  # overview|architecture|module|reference|guide|operations
    order: int
    prompt: str
    scope_files: list[str] = field(default_factory=list)
    summary: str = ""  # short description used in the index
    # Strings that must appear in the generated page. If any is missing, the
    # generator repeats the call demanding explicitly what was left out.
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
