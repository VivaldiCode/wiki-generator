"""Static repository analysis — no model involved, only filesystem and git."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from .config import WikiConfig
from .models import FileInfo, ModuleInfo, RepoScan
from .utils import hash_and_lines, read_text, sha1_file, slugify

class EmptyRepositoryError(ValueError):
    """The repository has nothing a wiki could be built from."""


IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    "dist", "build", "out", "target", "vendor", "coverage", "htmlcov",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".cache",
    ".idea", ".vscode", ".gradle", ".terraform", "Pods", "DerivedData",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    "site-packages", "bower_components", ".dart_tool", "Carthage",
    ".wrangler", ".vercel", ".netlify", ".serverless", ".output", ".angular",
}

# Directory sequences to ignore. Unlike IGNORE_DIRS these are context-dependent:
# a bare `worktrees/` may be legitimate, while `.claude/worktrees/` holds full
# repository copies made by Claude Code — documenting them would duplicate the
# whole repository N times over.
IGNORE_PATH_FRAGMENTS = (
    "/.claude/worktrees/",
    "/.git/worktrees/",
)

# Generated-output directories whose name varies by suffix
# (e.g. `playwright-report`, `playwright-report-smoke`, `test-results-e2e`).
IGNORE_DIR_PREFIXES = (
    "playwright-report", "test-results", "allure-report", "cypress/screenshots",
    "cypress/videos", "storybook-static",
)

# Credential-looking files. Excluded from the scan by default: the generator
# gives a model read access to the repository and writes documentation from it,
# so nothing should point at secrets. The exclusion is reported to the user
# rather than being silent.
SENSITIVE_PATTERNS = (
    "*service-account*.json", "*serviceaccount*.json", "*-adminsdk-*.json",
    "*credentials*.json", "*client_secret*.json", "*.pem", "*.p12", "*.pfx",
    "*.jks", "*.keystore", "id_rsa*", "id_ed25519*", "*.ppk",
    ".env", ".env.*",
)

# Files matching the patterns above that are configuration documentation rather
# than secrets — exactly what the configuration page needs to read.
SENSITIVE_ALLOWLIST = {
    ".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults",
}


def is_sensitive(rel: str) -> bool:
    name = Path(rel).name
    if name in SENSITIVE_ALLOWLIST:
        return False
    return any(fnmatch.fnmatch(name.lower(), pattern) for pattern in SENSITIVE_PATTERNS)

IGNORE_FILE_SUFFIXES = {
    ".lock", ".log", ".min.js", ".min.css", ".map", ".pyc", ".pyo", ".class",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".jar", ".war", ".zip", ".tar",
    ".gz", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".pdf",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wav", ".db", ".sqlite",
}

LANGUAGE_BY_EXT = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".fs": "F#",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".hh": "C++",
    ".swift": "Swift", ".m": "Objective-C", ".mm": "Objective-C++",
    ".scala": "Scala", ".clj": "Clojure", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".lua": "Lua", ".dart": "Dart",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protobuf",
    ".vue": "Vue", ".svelte": "Svelte",
    ".tf": "Terraform", ".hcl": "HCL",
    ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".toml": "TOML",
    ".xml": "XML", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".less": "LESS",
    ".md": "Markdown", ".mdx": "Markdown", ".rst": "reStructuredText",
}

# Extensions that count as "source code" for module and reference purposes.
SOURCE_LANGUAGES = {
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "Kotlin", "Ruby",
    "PHP", "C#", "F#", "C", "C++", "Swift", "Objective-C", "Objective-C++",
    "Scala", "Clojure", "Elixir", "Erlang", "Haskell", "Lua", "Dart", "Shell",
    "Vue", "Svelte", "SQL", "GraphQL", "Protobuf", "Terraform",
}

KEY_FILE_NAMES = {
    "package.json", "package-lock.json", "pnpm-workspace.yaml", "tsconfig.json",
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "mix.exs", "pubspec.yaml", "Package.swift",
    "Makefile", "Justfile", "Taskfile.yml", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "compose.yaml", "Procfile", "serverless.yml",
    ".env.example", ".env.sample", "README.md", "CONTRIBUTING.md", "ARCHITECTURE.md",
    "CHANGELOG.md", "LICENSE", "CLAUDE.md", "AGENTS.md",
}

# Manifests whose contents are worth passing to the model directly.
MANIFEST_FILES = {
    "package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json",
    "mix.exs", "pubspec.yaml", "Makefile", "docker-compose.yml", "compose.yaml",
    "Dockerfile", ".env.example",
}

ENTRYPOINT_PATTERNS = (
    "main.py", "__main__.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "main.go", "main.rs", "main.ts", "main.js", "index.ts", "index.js",
    "server.ts", "server.js", "app.ts", "Program.cs", "Main.java", "main.kt",
)

TEST_MARKERS = ("test", "tests", "spec", "__tests__", "e2e")
DOC_MARKERS = ("docs", "doc", "documentation", "wiki")


# ----------------------------------------------------------------------
def find_repositories(root: Path, max_depth: int = 3) -> list[Path]:
    """Discover the git repositories under `root`.

    If `root` is itself a repository it is returned alone — one repo, one wiki.
    Otherwise child repositories are searched for, so that a folder aggregating
    several projects yields one wiki per project instead of a single mixed one.
    """
    root = Path(root)
    if (root / ".git").exists():
        return [root]

    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for entry in entries:
            if entry.name in IGNORE_DIRS:
                continue
            if (entry / ".git").exists():
                found.append(entry)  # do not descend: sub-repos stay with the parent
                continue
            walk(entry, depth + 1)

    walk(root, 1)
    return found


def substance(scan: RepoScan) -> tuple[int, int]:
    """How much there actually is to document: (content lines, content files).

    Lock files, generated output and binaries are already out of the scan, so
    what remains is what a reader would call the repository's content. A repo
    holding only a one-line README scores (1, 1) and is not worth a wiki.
    """
    return scan.total_lines, len(scan.files)


def count_repo_files(repo: Path, cap: int = 20_000) -> int:
    """Cheap file count for a repository, used to order a multi-repo run.

    Uses `git ls-files` when available because it is near-instant and already
    honours `.gitignore`; falls back to a bounded walk so a repository without
    git — or with a broken index — still gets a usable number instead of zero.
    """
    listing = _git_files(repo)
    if listing is not None:
        return sum(
            1 for rel in listing
            if not any(part in IGNORE_DIRS for part in rel.split("/")[:-1])
        )

    count = 0
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.relative_to(repo).parts[:-1]):
            continue
        count += 1
        if count >= cap:
            break
    return count


def _git_files(repo: Path) -> list[str] | None:
    """List files via git (honours .gitignore). None if this is not a git repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _walk_files(repo: Path) -> list[str]:
    results: list[str] = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo).parts
        if any(part in IGNORE_DIRS for part in rel_parts[:-1]):
            continue
        results.append("/".join(rel_parts))
    return results


def _output_prefix(config: WikiConfig) -> str | None:
    """Relative prefix of the wiki when it is written inside the repository itself.

    Without this, a second run would document the output of the first.
    """
    try:
        relative = config.output_path.relative_to(config.repo_path)
    except ValueError:
        return None
    return str(relative).replace("\\", "/").strip("/") or None


def _should_keep(rel: str, config: WikiConfig, output_prefix: str | None = None) -> bool:
    if output_prefix and (rel == output_prefix or rel.startswith(output_prefix + "/")):
        return False
    parts = rel.split("/")
    if any(part in IGNORE_DIRS for part in parts[:-1]):
        return False
    if any(fragment in f"/{rel}" for fragment in IGNORE_PATH_FRAGMENTS):
        return False
    if any(part.startswith(IGNORE_DIR_PREFIXES) for part in parts[:-1]):
        return False
    name = parts[-1]
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in IGNORE_FILE_SUFFIXES):
        return False
    if config.include_globs and not any(
        fnmatch.fnmatch(rel, pattern) for pattern in config.include_globs
    ):
        return False
    if any(fnmatch.fnmatch(rel, pattern) for pattern in config.exclude_globs):
        return False
    return True


def _count_lines(path: Path, max_bytes: int) -> int:
    try:
        if path.stat().st_size > max_bytes:
            return 0
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _language_for(rel: str) -> str:
    suffix = Path(rel).suffix.lower()
    if suffix:
        return LANGUAGE_BY_EXT.get(suffix, "Other")
    name = Path(rel).name
    if name in {"Makefile", "Dockerfile", "Justfile", "Procfile"}:
        return name
    return "Other"


# ----------------------------------------------------------------------
def _build_modules(source_files: list[FileInfo], config: WikiConfig) -> list[ModuleInfo]:
    """Group source files into modules by directory prefix.

    Modules below the minimum are promoted to the parent directory until they
    reach it or hit the root — this avoids hundreds of one-file pages.
    """
    depth = max(1, config.module_depth)

    def key_at(rel: str, level: int) -> str:
        dirs = Path(rel).parts[:-1]
        if not dirs or level <= 0:
            return "(root)"
        return "/".join(dirs[:level])

    assignment = {f.rel_path: key_at(f.rel_path, depth) for f in source_files}

    for level in range(depth, 0, -1):
        counts: dict[str, int] = {}
        for key in assignment.values():
            counts[key] = counts.get(key, 0) + 1
        small = {k for k, n in counts.items() if n < config.min_files_per_module}
        if not small:
            break
        for rel, key in list(assignment.items()):
            if key in small:
                assignment[rel] = key_at(rel, level - 1)

    buckets: dict[str, list[FileInfo]] = {}
    for file in source_files:
        buckets.setdefault(assignment[file.rel_path], []).append(file)

    modules = [
        ModuleInfo(
            key=key,
            slug=slugify(key if key != "(root)" else "root"),
            title=key,
            files=sorted(files, key=lambda f: f.rel_path),
        )
        for key, files in buckets.items()
    ]
    modules.sort(key=lambda m: (-m.file_count, m.key))
    return modules[: config.max_modules]


# ----------------------------------------------------------------------
def scan_repo(config: WikiConfig) -> RepoScan:
    repo = config.repo_path
    if not repo.is_dir():
        raise NotADirectoryError(f"Repository not found: {repo}")

    git_listing = _git_files(repo)
    is_git = git_listing is not None
    raw_paths = git_listing if is_git else _walk_files(repo)

    output_prefix = _output_prefix(config)
    files: list[FileInfo] = []
    sensitive_skipped: list[str] = []
    for rel in sorted(set(raw_paths)):
        if not _should_keep(rel, config, output_prefix):
            continue
        if is_sensitive(rel):
            sensitive_skipped.append(rel)
            continue
        abs_path = repo / rel
        if not abs_path.is_file():
            continue
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size > config.max_file_size_bytes:
            continue
        # One read, both facts. Files are read once instead of twice, which on a
        # large repository over network storage is most of the scan.
        content_hash, lines = hash_and_lines(abs_path, config.max_file_size_bytes)
        language = _language_for(rel)
        files.append(
            FileInfo(
                rel_path=rel,
                size=size,
                lines=lines,
                language=language,
                is_source=language in SOURCE_LANGUAGES,
                content_hash=content_hash,
            )
        )

    if not files:
        raise EmptyRepositoryError(
            f"no analysable files (check --include/--exclude, or the directory is empty)"
        )

    def is_test(rel: str) -> bool:
        lowered = rel.lower()
        parts = lowered.split("/")
        return any(marker in parts for marker in TEST_MARKERS) or any(
            Path(lowered).name.startswith(p) or Path(lowered).stem.endswith(f"_{p}")
            or Path(lowered).stem.endswith(f".{p}")
            for p in ("test", "spec")
        )

    def is_doc(rel: str) -> bool:
        parts = rel.lower().split("/")
        return parts[0] in DOC_MARKERS or rel.lower().endswith((".md", ".mdx", ".rst"))

    source_files = [f for f in files if f.is_source and not is_test(f.rel_path)]
    if not source_files:  # test/docs-only repos: do not leave the wiki empty
        source_files = [f for f in files if f.is_source] or files

    languages: dict[str, int] = {}
    for file in files:
        if file.is_source:
            languages[file.language] = languages.get(file.language, 0) + 1

    key_files = [f.rel_path for f in files if Path(f.rel_path).name in KEY_FILE_NAMES]
    key_files += [
        f.rel_path for f in files
        if f.rel_path.startswith((".github/workflows/", ".gitlab-ci", "k8s/", "helm/",
                                  "infra/", "terraform/", "deploy/"))
    ]
    key_files = sorted(set(key_files))

    entrypoints = sorted(
        f.rel_path for f in files if Path(f.rel_path).name in ENTRYPOINT_PATTERNS
    )

    manifest_excerpts = {
        f.rel_path: read_text(repo / f.rel_path, max_chars=4000)
        for f in files
        if Path(f.rel_path).name in MANIFEST_FILES
    }

    return RepoScan(
        root=str(repo),
        files=files,
        source_files=source_files,
        modules=_build_modules(source_files, config),
        languages=languages,
        key_files=key_files,
        entrypoints=entrypoints,
        doc_files=sorted(f.rel_path for f in files if is_doc(f.rel_path))[:80],
        test_files=sorted(f.rel_path for f in files if is_test(f.rel_path))[:120],
        manifest_excerpts=manifest_excerpts,
        is_git_repo=is_git,
        sensitive_skipped=sensitive_skipped,
    )
