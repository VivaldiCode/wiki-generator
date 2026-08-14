"""Code cartography: static file-to-file dependency graph.

Extracts imports/includes/requires per language and resolves them to real files
in the repository. It is deterministic and complete — the model is never involved —
because a link graph is only worth anything if every edge is true.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import WikiConfig
from .models import FileInfo, RepoScan
from .i18n import Translator, translator
from .utils import chunked, read_text, slugify, wikilink

# ----------------------------------------------------------------------
# Import-specifier extraction per language
# ----------------------------------------------------------------------
PATTERNS: dict[str, list[re.Pattern]] = {
    "Python": [
        re.compile(r"^\s*from\s+([.\w]+)\s+import\s+", re.M),
        re.compile(r"^\s*import\s+([.\w]+(?:\s*,\s*[.\w]+)*)", re.M),
    ],
    "JavaScript": [
        re.compile(r"""^\s*import\s+(?:[\w*{}\s,$]+\s+from\s+)?['"]([^'"]+)['"]""", re.M),
        re.compile(r"""^\s*export\s+(?:\*|{[^}]*})\s+from\s+['"]([^'"]+)['"]""", re.M),
        re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)"""),
        re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)"""),
    ],
    "Go": [
        re.compile(r'^\s*import\s+"([^"]+)"', re.M),
        re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"\s*$', re.M),  # inside import ( ... )
    ],
    "Java": [re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.M)],
    "C": [re.compile(r'^\s*#\s*include\s+["<]([^">]+)[">]', re.M)],
    "Rust": [
        re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*;", re.M),
        re.compile(r"^\s*(?:pub\s+)?use\s+((?:crate|super|self)::[\w:]+)", re.M),
    ],
    "Ruby": [re.compile(r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]""", re.M)],
    "PHP": [
        re.compile(r"^\s*use\s+([\w\\]+)\s*;", re.M),
        re.compile(r"""\b(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+)['"]"""),
    ],
    "C#": [re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.M)],
    "Shell": [re.compile(r"^\s*(?:source|\.)\s+([^\s;]+)", re.M)],
    "Dart": [
        re.compile(r"""^\s*(?:import|export|part)\s+['"]([^'"]+)['"]""", re.M),
    ],
}

# Linguagens reconhecidas como codigo-fonte mas sem extrator de imports: o grafo
# nao consegue ter arestas para elas, e isso e reportado em vez de silenciado.
LANGUAGES_WITHOUT_EXTRACTOR = {
    "Swift", "C#", "F#", "Elixir", "Erlang", "Haskell", "Lua", "Clojure",
    "SQL", "GraphQL", "Protobuf", "Terraform",
}

# Languages sharing the same extractor
LANGUAGE_ALIASES = {
    "TypeScript": "JavaScript",
    "Vue": "JavaScript",
    "Svelte": "JavaScript",
    "Kotlin": "Java",
    "Scala": "Java",
    "C++": "C",
    "Objective-C": "C",
    "Objective-C++": "C",
}

# Extensions tried when resolving an extensionless path (JS/TS style)
RESOLVE_EXTENSIONS = (
    "", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".vue", ".svelte",
    ".py", ".go", ".rb", ".php",
)
RESOLVE_INDEXES = (
    "index.ts", "index.tsx", "index.js", "index.jsx", "index.mjs",
    "__init__.py", "mod.rs", "index.php",
)

MAX_SCAN_BYTES = 300_000

# Readability limits for the cartography pages of large repositories.
MODULE_PAGE_MAX_FILES = 180  # files per module page before splitting into parts
NEIGHBOUR_CAP = 120  # neighbours from other modules drawn in the diagram


@dataclass
class GraphNode:
    rel_path: str
    module: str
    language: str
    lines: int


@dataclass
class CodeGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)  # (source, target)
    external: dict[str, int] = field(default_factory=dict)  # package -> import count
    unresolved: dict[str, list[str]] = field(default_factory=dict)  # file -> specs
    # Languages present in the repository with no import extractor: their files
    # appear in the graph without any edges.
    languages_without_extractor: list[str] = field(default_factory=list)

    # -- metrics --------------------------------------------------------
    @property
    def out_degree(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for src, _ in self.edges:
            counts[src] += 1
        return counts

    @property
    def in_degree(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for _, dst in self.edges:
            counts[dst] += 1
        return counts

    def orphans(self) -> list[str]:
        """Files with no edges in either direction."""
        touched = {n for edge in self.edges for n in edge}
        return sorted(set(self.nodes) - touched)

    def entry_candidates(self) -> list[str]:
        """Files that import others but that nobody imports."""
        incoming = self.in_degree
        outgoing = self.out_degree
        return sorted(
            path for path in self.nodes
            if incoming.get(path, 0) == 0 and outgoing.get(path, 0) > 0
        )

    def cycles(self, limit: int = 25) -> list[list[str]]:
        """Dependency cycles (iterative DFS with a path stack)."""
        adjacency: dict[str, list[str]] = defaultdict(list)
        for src, dst in sorted(self.edges):
            adjacency[src].append(dst)

        found: list[list[str]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        color: dict[str, int] = {}  # 0=unvisited, 1=in progress, 2=done

        for start in sorted(self.nodes):
            if color.get(start, 0) != 0:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            path: list[str] = []
            on_path: set[str] = set()
            while stack:
                node, index = stack[-1]
                if index == 0:
                    color[node] = 1
                    path.append(node)
                    on_path.add(node)
                if index < len(adjacency[node]):
                    stack[-1] = (node, index + 1)
                    neighbour = adjacency[node][index]
                    if neighbour in on_path:
                        cycle = path[path.index(neighbour):] + [neighbour]
                        signature = tuple(sorted(set(cycle)))
                        if signature not in seen_signatures and len(found) < limit:
                            seen_signatures.add(signature)
                            found.append(cycle)
                    elif color.get(neighbour, 0) == 0:
                        stack.append((neighbour, 0))
                else:
                    color[node] = 2
                    on_path.discard(node)
                    path.pop()
                    stack.pop()
        return found


# ----------------------------------------------------------------------
def _extract_specs(text: str, language: str) -> list[str]:
    key = LANGUAGE_ALIASES.get(language, language)
    patterns = PATTERNS.get(key)
    if not patterns:
        return []
    specs: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            for part in raw.split(","):
                part = part.strip().split(" as ")[0].strip()
                if part:
                    specs.append(part)
    return specs


class _Resolver:
    """Resolves import specifiers to real repository paths."""

    def __init__(self, scan: RepoScan, repo: Path) -> None:
        self.repo = repo
        self.paths = {f.rel_path for f in scan.files}
        self.by_stem: dict[str, list[str]] = defaultdict(list)
        for path in self.paths:
            self.by_stem[Path(path).stem].append(path)
        self.source_roots = self._detect_source_roots()
        self.go_module = self._detect_go_module(scan)
        self.dart_package = self._detect_dart_package(scan)

    def _detect_source_roots(self) -> list[str]:
        roots = ["", "src", "lib", "app", "pkg", "internal", "source", "packages"]
        return [r for r in roots if r == "" or any(p.startswith(r + "/") for p in self.paths)]

    def _detect_dart_package(self, scan: RepoScan) -> str:
        content = scan.manifest_excerpts.get("pubspec.yaml", "")
        match = re.search(r"^name:\s*(\S+)", content, re.M)
        return match.group(1).strip("'\"") if match else ""

    def _detect_go_module(self, scan: RepoScan) -> str:
        content = scan.manifest_excerpts.get("go.mod", "")
        match = re.search(r"^module\s+(\S+)", content, re.M)
        return match.group(1) if match else ""

    # ------------------------------------------------------------------
    def _try_paths(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            normalized = str(Path(candidate)).replace("\\", "/").lstrip("./")
            if normalized in self.paths:
                return normalized
        return None

    def _expand(self, base: str) -> list[str]:
        """Generate candidates for an extensionless path (file or index)."""
        options = [base + ext for ext in RESOLVE_EXTENSIONS]
        options += [f"{base}/{index}" for index in RESOLVE_INDEXES]
        return options

    # ------------------------------------------------------------------
    def resolve(self, source: str, spec: str, language: str) -> str | None:
        key = LANGUAGE_ALIASES.get(language, language)
        source_dir = str(Path(source).parent)
        if source_dir == ".":
            source_dir = ""

        # Relative paths: common to JS/TS, Ruby, PHP, C, Shell.
        if spec.startswith((".", "/")) and key != "Python":
            base = str((Path(source_dir) / spec).resolve().relative_to(Path("/").resolve())) \
                if spec.startswith("/") else str(Path(source_dir) / spec)
            base = _normalize(base)
            return self._try_paths(self._expand(base))

        if key == "Dart":
            return self._resolve_dart(source_dir, spec)
        if key == "Python":
            return self._resolve_python(source, source_dir, spec)
        if key == "Go":
            return self._resolve_go(spec)
        if key == "Java":
            return self._resolve_java(spec)
        if key == "C":
            return self._resolve_c(source_dir, spec)
        if key == "Rust":
            return self._resolve_rust(source, source_dir, spec)
        if key == "C#":
            return None  # C# namespaces do not map to files reliably
        return self._resolve_generic(spec)

    # ------------------------------------------------------------------
    def _resolve_python(self, source: str, source_dir: str, spec: str) -> str | None:
        if spec.startswith("."):
            level = len(spec) - len(spec.lstrip("."))
            remainder = spec[level:]
            base = Path(source_dir)
            for _ in range(level - 1):
                base = base.parent
            target = base / Path(*remainder.split(".")) if remainder else base
            return self._try_paths([f"{target}.py", f"{target}/__init__.py"])

        parts = spec.split(".")
        for root in self.source_roots:
            prefix = f"{root}/" if root else ""
            for depth in range(len(parts), 0, -1):
                target = prefix + "/".join(parts[:depth])
                found = self._try_paths([f"{target}.py", f"{target}/__init__.py"])
                if found:
                    return found
        return None

    def _resolve_dart(self, source_dir: str, spec: str) -> str | None:
        if spec.startswith("dart:"):
            return None  # SDK library
        if spec.startswith("package:"):
            remainder = spec[len("package:"):]
            package, _, path = remainder.partition("/")
            if not self.dart_package or package != self.dart_package:
                return None  # external dependency (flutter, riverpod, ...)
            # `package:<own>/x.dart` maps to `lib/x.dart`
            return self._try_paths([f"lib/{path}"])
        # Dart relative imports carry no `./`: they are relative to the file.
        return self._try_paths([_normalize(str(Path(source_dir) / spec)), spec])

    def _resolve_go(self, spec: str) -> str | None:
        if self.go_module and spec.startswith(self.go_module):
            directory = spec[len(self.go_module):].strip("/")
        elif "/" in spec and not spec.split("/")[0].count("."):
            directory = spec  # internal import without a module prefix
        else:
            return None
        matches = sorted(
            p for p in self.paths
            if p.endswith(".go") and str(Path(p).parent) == (directory or ".")
        )
        # A Go package is several files: link to the most representative one.
        return matches[0] if matches else None

    def _resolve_java(self, spec: str) -> str | None:
        parts = spec.split(".")
        if len(parts) < 2:
            return None
        tail = "/".join(parts[-3:])
        for extension in (".java", ".kt", ".scala"):
            candidates = sorted(p for p in self.paths if p.endswith(tail + extension))
            if candidates:
                return candidates[0]
        return self._resolve_generic(parts[-1])

    def _resolve_c(self, source_dir: str, spec: str) -> str | None:
        found = self._try_paths([_normalize(str(Path(source_dir) / spec)), spec])
        if found:
            return found
        name = Path(spec).name
        candidates = sorted(p for p in self.paths if Path(p).name == name)
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_rust(self, source: str, source_dir: str, spec: str) -> str | None:
        if "::" in spec:
            parts = [p for p in spec.split("::") if p not in {"crate", "self", "super"}]
            if not parts:
                return None
            target = "src/" + "/".join(parts)
            return self._try_paths([f"{target}.rs", f"{target}/mod.rs"])
        base = str(Path(source_dir) / spec)
        return self._try_paths([f"{base}.rs", f"{base}/mod.rs"])

    def _resolve_generic(self, spec: str) -> str | None:
        stem = Path(spec.replace("\\", "/")).stem
        candidates = self.by_stem.get(stem, [])
        return candidates[0] if len(candidates) == 1 else None


def _external_name(spec: str) -> str:
    """External package name derived from the import specifier."""
    for prefix in ("package:", "dart:", "node:", "npm:", "jsr:"):
        if spec.startswith(prefix):
            spec = spec[len(prefix):]
            break
    root = spec.split("/")[0].split("::")[0].lstrip("@")
    # `os.path` -> `os`, but keep dotted names that are the package (e.g. `github.com`)
    if "." in root and not root.startswith("."):
        head = root.split(".")[0]
        root = root if head in {"github", "gitlab", "gopkg", "golang"} else head
    return root


def _normalize(path: str) -> str:
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


# ----------------------------------------------------------------------
def build_graph(scan: RepoScan, config: WikiConfig) -> CodeGraph:
    repo = config.repo_path
    graph = CodeGraph()

    module_of: dict[str, str] = {}
    for module in scan.modules:
        for file in module.files:
            module_of[file.rel_path] = module.key

    analysable: list[FileInfo] = [f for f in scan.files if f.is_source]
    for file in analysable:
        graph.nodes[file.rel_path] = GraphNode(
            rel_path=file.rel_path,
            module=module_of.get(file.rel_path, str(Path(file.rel_path).parent) or "(root)"),
            language=file.language,
            lines=file.lines,
        )

    missing_support = {
        f.language for f in analysable
        if f.language in LANGUAGES_WITHOUT_EXTRACTOR
    }
    if missing_support:
        graph.languages_without_extractor = sorted(missing_support)

    resolver = _Resolver(scan, repo)

    for file in analysable:
        text = read_text(repo / file.rel_path, max_chars=MAX_SCAN_BYTES)
        if not text:
            continue
        for spec in _extract_specs(text, file.language):
            target = resolver.resolve(file.rel_path, spec, file.language)
            if target and target != file.rel_path and target in graph.nodes:
                graph.edges.add((file.rel_path, target))
            elif target is None:
                if spec.startswith(".") or spec.startswith("/"):
                    graph.unresolved.setdefault(file.rel_path, []).append(spec)
                else:
                    root = _external_name(spec)
                    if root:
                        graph.external[root] = graph.external.get(root, 0) + 1

    return graph


# ----------------------------------------------------------------------
def _mermaid_id(rel_path: str) -> str:
    return "n_" + slugify(rel_path, max_len=48).replace("-", "_")


def _mermaid_label(rel_path: str, full: bool) -> str:
    return rel_path if full else Path(rel_path).name


def render_mermaid(
    graph: CodeGraph,
    nodes: list[str] | None = None,
    *,
    group_by_module: bool = True,
    full_labels: bool = False,
    direction: str = "LR",
    focus_module: str | None = None,
    page_of_path: dict[str, str] | None = None,
    t: Translator | None = None,
) -> str:
    """Emit a mermaid `flowchart` block for the given subset of nodes.

    `focus_module` highlights the current page's module; `page_of_path` makes nodes
    from other modules clickable, linking to that module's cartography page.
    """
    t = t or translator("en")
    selected = set(nodes if nodes is not None else graph.nodes)
    if not selected:
        return '```mermaid\nflowchart LR\n  empty["' + t("carto.empty") + '"]\n```'

    lines = [f"flowchart {direction}"]
    external_nodes: list[tuple[str, str]] = []  # (id, href)

    if group_by_module:
        grouped: dict[str, list[str]] = defaultdict(list)
        for path in sorted(selected):
            grouped[graph.nodes[path].module].append(path)
        for index, (module, paths) in enumerate(sorted(grouped.items())):
            is_focus = focus_module is not None and module == focus_module
            title = module if focus_module is None else (
                f"{module} ({t('carto.this_module')})" if is_focus
                else f"{module} ({t('carto.neighbour')})"
            )
            lines.append(f'  subgraph sg{index}["{title}"]')
            lines.append("    direction TB")
            for path in paths:
                node_id = _mermaid_id(path)
                lines.append(f'    {node_id}["{_mermaid_label(path, full_labels)}"]')
                if not is_focus and page_of_path and path in page_of_path:
                    external_nodes.append((node_id, page_of_path[path]))
            lines.append("  end")
    else:
        for path in sorted(selected):
            lines.append(f'  {_mermaid_id(path)}["{_mermaid_label(path, full_labels)}"]')

    for src, dst in sorted(graph.edges):
        if src in selected and dst in selected:
            lines.append(f"  {_mermaid_id(src)} --> {_mermaid_id(dst)}")

    if external_nodes:
        lines.append("  classDef neighbour stroke-dasharray: 4 3;")
        lines.append(
            "  class " + ",".join(node_id for node_id, _ in external_nodes) + " neighbour;"
        )
        for node_id, href in external_nodes:
            lines.append('  click ' + node_id + ' "' + href + '" "' + t('carto.click_tooltip') + '"')

    return "```mermaid\n" + "\n".join(lines) + "\n```"


def render_module_mermaid(
    graph: CodeGraph, page_of_module: dict[str, str] | None = None,
    t: Translator | None = None,
) -> str:
    """Module-level aggregated graph — readable even in large repositories."""
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for src, dst in graph.edges:
        a, b = graph.nodes[src].module, graph.nodes[dst].module
        if a != b:
            weights[(a, b)] += 1

    t = t or translator("en")
    modules = sorted({node.module for node in graph.nodes.values()})
    lines = ["flowchart LR"]
    for module in modules:
        count = sum(1 for n in graph.nodes.values() if n.module == module)
        label = f"{module}<br/>{count} " + t("carto.th.files").lower()
        lines.append(f'  {_mermaid_id(module)}["{label}"]')
    for (a, b), weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        label = f"|{weight}|" if weight > 1 else ""
        lines.append(f"  {_mermaid_id(a)} -->{label} {_mermaid_id(b)}")
    if page_of_module:
        for module in modules:
            href = page_of_module.get(module)
            if href:
                lines.append(
                    '  click ' + _mermaid_id(module) + ' "' + href + '" "' + t('carto.click_tooltip') + '"'
                )
    return "```mermaid\n" + "\n".join(lines) + "\n```"


# ----------------------------------------------------------------------
def graph_context(graph: CodeGraph, limit: int = 300) -> str:
    """Textual summary of the graph, injected into the model prompts."""
    incoming, outgoing = graph.in_degree, graph.out_degree
    hubs = sorted(
        graph.nodes,
        key=lambda p: -(incoming.get(p, 0) + outgoing.get(p, 0)),
    )[:20]

    edge_lines = [f"{src} -> {dst}" for src, dst in sorted(graph.edges)][:limit]
    externals = sorted(graph.external.items(), key=lambda kv: -kv[1])[:25]
    cycles = graph.cycles(limit=8)

    return f"""<code_cartography>
Statically computed dependency graph (edges are verified, not inferred).
Files in the graph: {len(graph.nodes)} | Internal edges: {len(graph.edges)}

Most connected files (hubs):
{chr(10).join(f"- {p} (in {incoming.get(p, 0)}, out {outgoing.get(p, 0)})" for p in hubs) or "- (none)"}

Entrypoint candidates (they import, nothing imports them):
{chr(10).join(f"- {p}" for p in graph.entry_candidates()[:15]) or "- (none)"}

Orphan files (no edges at all):
{chr(10).join(f"- {p}" for p in graph.orphans()[:15]) or "- (none)"}

Dependency cycles detected:
{chr(10).join("- " + " -> ".join(c) for c in cycles) or "- (none)"}

Most used external dependencies:
{chr(10).join(f"- {name} ({count} imports)" for name, count in externals) or "- (none)"}

Edges (source -> target){f", truncated to the first {limit}" if len(graph.edges) > limit else ""}:
{chr(10).join(edge_lines) or "(none)"}
</code_cartography>
"""


# ----------------------------------------------------------------------
def _write_module_page(
    *,
    graph: CodeGraph,
    module: str,
    slug: str,
    chunk: list[str],
    part: int,
    total_parts: int,
    entries: list[tuple[str, list[str]]],
    imports_map: dict[str, list[str]],
    imported_by: dict[str, list[str]],
    page_of_path: dict[str, str],
    page_of_module: dict[str, str],
    module_out: dict[str, int],
    module_in: dict[str, int],
    link_table,
    target: Path,
    t: Translator,
) -> None:
    """Write one module's cartography page, with navigation to its neighbours."""
    own = set(chunk)
    neighbours = set()
    for path in chunk:
        neighbours.update(imports_map.get(path, ()))
        neighbours.update(imported_by.get(path, ()))
    neighbours -= own

    # A heavily coupled module can drag in thousands of neighbours: keep the ones
    # most connected to it and say how many were left out, instead of lying by omission.
    dropped = 0
    if len(neighbours) > NEIGHBOUR_CAP:
        relevance: dict[str, int] = defaultdict(int)
        for path in chunk:
            for other in imports_map.get(path, ()):
                if other in neighbours:
                    relevance[other] += 1
            for other in imported_by.get(path, ()):
                if other in neighbours:
                    relevance[other] += 1
        ranked = sorted(neighbours, key=lambda p: (-relevance[p], p))
        dropped = len(neighbours) - NEIGHBOUR_CAP
        neighbours = set(ranked[:NEIGHBOUR_CAP])

    title_suffix = (
        t("carto.module.part", part=part, total=total_parts) if total_parts > 1 else ""
    )
    of_total = (
        t("carto.module.of_total", total=sum(len(c) for _, c in entries))
        if total_parts > 1 else ""
    )
    page = [
        f"# {t('carto.module.title', module=module)}{title_suffix}",
        "",
        t("carto.module.intro", count=len(chunk), of_total=of_total),
        "",
    ]

    if total_parts > 1:
        page += [
            t("carto.module.parts")
            + " · ".join(
                f"**{i}**" if i == part
                else wikilink(f"07-cartography/modules/{s}", str(i))
                for i, (s, _) in enumerate(entries, start=1)
            ),
            "",
        ]

    if dropped:
        page += [
            t("carto.module.dropped", dropped=dropped),
            "",
        ]

    page += [
        render_mermaid(
            graph,
            sorted(own | neighbours),
            focus_module=module,
            page_of_path=page_of_path,
            t=t,
        ),
        "",
    ]

    # -- navigation between modules --------------------------------------
    page += [f"## {t('carto.module.neighbours')}", ""]
    if module_out or module_in:
        page += [
            f"| {t('carto.th.module')} | {t('carto.module.th.out')} | "
            f"{t('carto.module.th.in')} |",
            "|---|---|---|",
        ]
        for other in sorted(set(module_out) | set(module_in)):
            # page_of_module is relative to 07-cartography/; this page lives in
            # 07-cartography/modules/, so the file name alone is enough.
            href = page_of_module.get(other, "")
            label = (
                wikilink(f"07-cartography/{href}", other, in_table=True)
                if href else f"`{other}`"
            )
            page.append(f"| {label} | {module_out.get(other, 0)} | {module_in.get(other, 0)} |")
    else:
        page.append(t("carto.module.no_neighbours"))
    page += [""]

    page += [f"## {t('carto.links_table')}", ""] + link_table(sorted(chunk))
    page += [
        "",
        "---",
        "",
        f"{wikilink('07-cartography/file-graph', t('carto.footer.file_graph'))} | "
        f"{wikilink('07-cartography/module-graph', t('carto.footer.module_graph'))} | "
        f"{wikilink('README', t('footer.index'))}  ",
        t("carto.footer.deterministic"),
    ]
    target.write_text("\n".join(page) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
def write_cartography(
    graph: CodeGraph, config: WikiConfig, max_nodes_single_diagram: int = 140
) -> list[Path]:
    t = translator(config.language)
    """Write the deterministic cartography pages and the graph artifacts."""
    out = config.output_path / "07-cartography"
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    incoming, outgoing = graph.in_degree, graph.out_degree
    node_count = len(graph.nodes)

    # Precomputed adjacency: without it, the per-module neighbour computation would
    # be O(files x edges) and take minutes on large repositories.
    imports_map: dict[str, list[str]] = defaultdict(list)
    imported_by: dict[str, list[str]] = defaultdict(list)
    for src, dst in sorted(graph.edges):
        imports_map[src].append(dst)
        imported_by[dst].append(src)

    # -- full graph, always exported untruncated -------------------------
    full_mermaid = render_mermaid(graph, full_labels=True)
    (out / "file-graph.mmd").write_text(
        full_mermaid.removeprefix("```mermaid\n").removesuffix("```") + "\n",
        encoding="utf-8",
    )
    written.append(out / "file-graph.mmd")

    (out / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "path": n.rel_path,
                        "module": n.module,
                        "language": n.language,
                        "lines": n.lines,
                        "in_degree": incoming.get(n.rel_path, 0),
                        "out_degree": outgoing.get(n.rel_path, 0),
                    }
                    for n in sorted(graph.nodes.values(), key=lambda x: x.rel_path)
                ],
                "edges": [{"from": s, "to": d} for s, d in sorted(graph.edges)],
                "external": dict(sorted(graph.external.items(), key=lambda kv: -kv[1])),
                "cycles": graph.cycles(limit=50),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    written.append(out / "graph.json")

    # -- main page: file graph -------------------------------------------
    lines = [
        f"# {t('carto.file.title')}",
        "",
        t("carto.file.intro"),
        "",
        f"| {t('carto.metric')} | {t('carto.value')} |",
        "|---|---|",
        f"| {t('carto.m.nodes')} | {node_count} |",
        f"| {t('carto.m.edges')} | {len(graph.edges)} |",
        f"| {t('carto.m.orphans')} | {len(graph.orphans())} |",
        f"| {t('carto.m.cycles')} | {len(graph.cycles(limit=100))} |",
        f"| {t('carto.m.external')} | {len(graph.external)} |",
        "",
    ]

    def link_table(paths: list[str]) -> list[str]:
        rows = [
            f"| {t('carto.th.file')} | {t('carto.th.imports')} | "
            f"{t('carto.th.imported_by')} |",
            "|---|---|---|",
        ]
        for path in paths:
            outs = imports_map.get(path, [])
            ins = imported_by.get(path, [])
            rows.append(
                f"| `{path}` | "
                f"{'<br>'.join(f'`{p}`' for p in outs) if outs else '—'} | "
                f"{'<br>'.join(f'`{p}`' for p in ins) if ins else '—'} |"
            )
        return rows

    # Only populated when per-module pages exist (large repositories).
    page_of_module: dict[str, str] = {}

    if node_count <= max_nodes_single_diagram:
        lines += [f"## {t('carto.full_graph')}", "", render_mermaid(graph, t=t), ""]
        lines += [f"## {t('carto.links_table')}", ""] + link_table(sorted(graph.nodes)) + [""]
    else:
        # Large repository: one page per module (split into parts when the module
        # is huge) keeps coverage complete without producing a file no renderer
        # can open.
        grouped: dict[str, list[str]] = defaultdict(list)
        for path, node in graph.nodes.items():
            grouped[node.module].append(path)
        for paths in grouped.values():
            paths.sort()

        modules_dir = out / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)

        # 1st pass: decide the pagination and where each file lives, so that cross
        # links always point at the right page.
        pagination: dict[str, list[tuple[str, list[str]]]] = {}  # modulo -> [(slug, paths)]
        page_of_path: dict[str, str] = {}
        for module, paths in sorted(grouped.items()):
            base = slugify(module, max_len=60)
            chunks = chunked(paths, MODULE_PAGE_MAX_FILES)
            entries: list[tuple[str, list[str]]] = []
            for index, chunk in enumerate(chunks, start=1):
                slug = base if len(chunks) == 1 else f"{base}-{index}"
                entries.append((slug, chunk))
                for path in chunk:
                    page_of_path[path] = f"{slug}.md"
            pagination[module] = entries
            page_of_module[module] = f"modules/{entries[0][0]}.md"

        lines += [
            f"## {t('carto.full_graph')}",
            "",
            t("carto.split.intro", nodes=node_count, limit=max_nodes_single_diagram),
            "",
            t("carto.split.b1"),
            t("carto.split.b2"),
            t("carto.split.b3"),
            "",
            f"### {t('carto.aggregated')}",
            "",
            render_module_mermaid(graph, page_of_module, t=t),
            "",
            f"### {t('carto.per_module')}",
            "",
            f"| {t('carto.th.module')} | {t('carto.th.files')} | "
            f"{t('carto.th.imports_from')} | {t('carto.th.imported_by_n')} | "
            f"{t('carto.th.pages')} |",
            "|---|---|---|---|---|",
        ]

        # Per-module coupling, for the neighbour tables.
        module_out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        module_in: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for src, dst in graph.edges:
            a, b = graph.nodes[src].module, graph.nodes[dst].module
            if a != b:
                module_out[a][b] += 1
                module_in[b][a] += 1

        for module, entries in pagination.items():
            paths = grouped[module]
            page_links = " ".join(
                wikilink(f"07-cartography/modules/{slug}",
                         str(index) if len(entries) > 1 else t("carto.view"),
                         in_table=True)
                for index, (slug, _) in enumerate(entries, start=1)
            )
            lines.append(
                f"| `{module}` | {len(paths)} | {len(module_out.get(module, {}))} "
                f"| {len(module_in.get(module, {}))} | {page_links} |"
            )

        for module, entries in pagination.items():
            total_parts = len(entries)
            for index, (slug, chunk) in enumerate(entries, start=1):
                _write_module_page(
                    graph=graph,
                    module=module,
                    slug=slug,
                    chunk=chunk,
                    part=index,
                    total_parts=total_parts,
                    entries=entries,
                    imports_map=imports_map,
                    imported_by=imported_by,
                    page_of_path=page_of_path,
                    page_of_module=page_of_module,
                    module_out=module_out.get(module, {}),
                    module_in=module_in.get(module, {}),
                    link_table=link_table,
                    target=modules_dir / f"{slug}.md",
                    t=t,
                )
                written.append(modules_dir / f"{slug}.md")

        lines.append("")

    # -- diagnostics ------------------------------------------------------
    hubs = sorted(graph.nodes, key=lambda p: -(incoming.get(p, 0) + outgoing.get(p, 0)))[:15]
    lines += [
        f"## {t('carto.hubs')}",
        "",
        t("carto.hubs.intro"),
        "",
        f"| {t('carto.th.file')} | {t('carto.th.imported_by')} | "
        f"{t('carto.th.imports')} | {t('carto.th.total')} |",
        "|---|---|---|---|",
    ]
    for path in hubs:
        i, o = incoming.get(path, 0), outgoing.get(path, 0)
        lines.append(f"| `{path}` | {i} | {o} | {i + o} |")
    lines.append("")

    cycles = graph.cycles(limit=25)
    lines += [f"## {t('carto.cycles')}", ""]
    if cycles:
        lines.append(t("carto.cycles.found"))
        lines.append("")
        lines += [f"- `{' -> '.join(cycle)}`" for cycle in cycles]
    else:
        lines.append(t("carto.cycles.none"))
    lines.append("")

    orphans = graph.orphans()
    lines += [f"## {t('carto.orphans')}", ""]
    if orphans:
        lines.append(t("carto.orphans.intro"))
        lines.append("")
        lines += [f"- `{path}`" for path in orphans]
    else:
        lines.append(t("carto.orphans.none"))
    lines.append("")

    externals = sorted(graph.external.items(), key=lambda kv: -kv[1])[:40]
    lines += [
        f"## {t('carto.external')}",
        "",
        f"| {t('carto.th.package')} | {t('carto.th.imports_count')} |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | {count} |" for name, count in externals] or ["| — | — |"]
    lines.append("")

    if graph.languages_without_extractor:
        lines += [
            f"## {t('carto.no_extractor')}",
            "",
            t("carto.no_extractor.intro"),
            "",
        ]
        lines += [f"- {lang}" for lang in graph.languages_without_extractor]
        lines.append("")

    if graph.unresolved:
        lines += [
            f"## {t('carto.unresolved')}",
            "",
            t("carto.unresolved.intro"),
            "",
        ]
        for path, specs in sorted(graph.unresolved.items())[:40]:
            lines.append(f"- `{path}`: {', '.join(f'`{s}`' for s in sorted(set(specs))[:8])}")
        lines.append("")

    lines += [
        "---",
        "",
        f"{wikilink('07-cartography/module-graph', t('carto.footer.module_graph'))} | "
        f"{wikilink('README', t('footer.index'))}  ",
        t("carto.footer.deterministic"),
    ]

    target = out / "file-graph.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(target)

    # -- module page ------------------------------------------------------
    module_pages = page_of_module
    module_lines = [
        f"# {t('carto.mod.title')}",
        "",
        t("carto.mod.intro") + (t("carto.mod.clickable") if module_pages else ""),
        "",
        render_module_mermaid(graph, module_pages, t=t),
        "",
        f"## {t('carto.mod.coupling')}",
        "",
        f"| {t('carto.th.source')} | {t('carto.th.target')} | "
        f"{t('carto.th.imports_count')} |",
        "|---|---|---|",
    ]

    def module_label(name: str) -> str:
        href = module_pages.get(name)
        return (
            wikilink(f"07-cartography/{href}", name, in_table=True)
            if href else f"`{name}`"
        )

    weights: dict[tuple[str, str], int] = defaultdict(int)
    for src, dst in graph.edges:
        a, b = graph.nodes[src].module, graph.nodes[dst].module
        if a != b:
            weights[(a, b)] += 1
    for (a, b), weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        module_lines.append(f"| {module_label(a)} | {module_label(b)} | {weight} |")
    if not weights:
        module_lines.append("| — | — | — |")
    module_lines += [
        "",
        "---",
        "",
        f"{wikilink('07-cartography/file-graph', t('carto.footer.file_graph'))} | "
        f"{wikilink('README', t('footer.index'))}  ",
        t("carto.footer.deterministic"),
    ]
    module_target = out / "module-graph.md"
    module_target.write_text("\n".join(module_lines) + "\n", encoding="utf-8")
    written.append(module_target)

    return written
