"""Wiki generation configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# Simple models first — the project default is the cheapest and fastest.
DEFAULT_MODEL = "haiku"


@dataclass
class WikiConfig:
    # --- targets ---
    repo_path: Path
    output_path: Path

    # --- model / execution ---
    model: str = DEFAULT_MODEL
    fallback_model: str | None = None
    concurrency: int = 4
    timeout: int = 600
    max_retries: int = 2
    permission_mode: str = "bypassPermissions"
    tools: tuple[str, ...] = ("Read", "Glob", "Grep")
    max_budget_usd: float | None = None
    isolated: bool = True
    claude_bin: str = "claude"
    log_dir: Path | None = None

    # --- content ---
    language: str = "en"
    project_name: str | None = None
    audience: str = "engineers who will work on this repository"

    # --- structure / scan ---
    module_depth: int = 2
    min_files_per_module: int = 2
    max_modules: int = 25
    files_per_reference_page: int = 6
    max_reference_pages: int = 60
    max_file_size_bytes: int = 400_000
    # Below this, a repository is skipped: a wiki built from a one-line
    # README is 20 pages of "Gaps / Open questions" and no information.
    min_lines: int = 50
    include_reference: bool = True
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()

    # --- behaviour ---
    force: bool = False
    dry_run: bool = False
    only: tuple[str, ...] = ()
    verbose: bool = False

    # filled at runtime; never read from the config file
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.repo_path = Path(self.repo_path).expanduser().resolve()
        self.output_path = Path(self.output_path).expanduser().resolve()
        if self.log_dir is not None:
            self.log_dir = Path(self.log_dir).expanduser().resolve()
        if isinstance(self.tools, list):
            self.tools = tuple(self.tools)
        if isinstance(self.only, list):
            self.only = tuple(self.only)
        if isinstance(self.include_globs, list):
            self.include_globs = tuple(self.include_globs)
        if isinstance(self.exclude_globs, list):
            self.exclude_globs = tuple(self.exclude_globs)

    # ------------------------------------------------------------------
    @property
    def resolved_project_name(self) -> str:
        return self.project_name or self.repo_path.name

    def to_dict(self) -> dict:
        data = asdict(self)
        data["repo_path"] = str(self.repo_path)
        data["output_path"] = str(self.output_path)
        data["log_dir"] = str(self.log_dir) if self.log_dir else None
        data.pop("extra", None)
        return data

    def fingerprint_fields(self) -> dict:
        """Options whose change must invalidate the page cache."""
        return {
            "model": self.model,
            "language": self.language,
            "audience": self.audience,
            "project_name": self.resolved_project_name,
            "module_depth": self.module_depth,
            "files_per_reference_page": self.files_per_reference_page,
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path, **overrides) -> "WikiConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"Unknown keys in {path}: {', '.join(sorted(unknown))}"
            )
        raw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**raw)
