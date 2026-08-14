"""Prompt construction. The wiki structure is defined here, and only here."""

from __future__ import annotations

from .config import WikiConfig
from .i18n import translator
from .models import ModuleInfo, RepoScan
from .utils import bullet_list, human_size, render_tree

LANGUAGE_NAMES = {
    "en": "English",
    "pt": "Portuguese (Portugal)",
    "pt-br": "Portuguese (Brazil)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}


# ----------------------------------------------------------------------
def system_prompt(config: WikiConfig) -> str:
    language = LANGUAGE_NAMES.get(config.language.lower(), config.language)
    return f"""You are a senior software documentation engineer producing one page of a
standardized engineering wiki for the repository at the current working directory.

Non-negotiable rules:
1. GROUND EVERYTHING IN THE CODE. Before writing, use Read/Glob/Grep to inspect the
   actual files. Never invent modules, functions, endpoints, env vars or flows.
2. If something cannot be determined from the code, write a short line under a
   "Gaps / Open questions" heading instead of guessing. Never fabricate.
3. Reference concrete code locations as inline code paths, e.g. `src/api/routes.py:42`.
4. Output the page as pure GitHub-flavored Markdown. The very first character of your
   final message MUST be `#`. No preamble, no "Now I have enough context", no
   "Here is the documentation", no sign-off, no meta-commentary about your process.
5. The required outline defines STRUCTURE, not literal text. Reproduce every heading it
   lists, in the same order and at the same level, and keep a heading even if the answer
   is "not applicable in this repository" — but WRITE EACH HEADING IN {language}. The
   outline is in English only because it is an instruction addressed to you; copying it
   verbatim into a page written in another language is wrong.
6. Use Mermaid fenced blocks (```mermaid) for diagrams when the outline asks for one.
   Keep node labels short and quoted; never put parentheses inside unquoted labels.
7. Be dense and factual. No marketing tone, no filler, no restating the heading.
8. Write the page in {language}. Keep code, identifiers and file paths verbatim.
9. Target audience: {config.audience}.
10. You have read-only tools. Do not attempt to modify anything.
11. LINKS: this wiki is an Obsidian vault of plain Markdown. When you link to another
    wiki page, use an Obsidian wikilink rooted at the vault: `[[02-architecture/overview]]`
    or `[[02-architecture/overview|texto]]` — never `[text](path.md)`, never HTML.
    Inside a Markdown table the alias pipe must be escaped: `[[target\\|texto]]`.
    ONLY link to targets listed in `<wiki_pages>`. Inventing a plausible-looking page
    (`[[04-database/schema]]`) produces a dead link — if the page you want does not
    exist, write plain text instead.
    Source files are NOT wiki pages: keep them as inline code (`src/api/routes.py:42`).
12. NEVER INVENT AN IDENTIFIER. Route paths, endpoint URLs, env var names, CLI flags,
    table names and config keys must be COPIED from the file you read, character for
    character. A plausible-looking path (`/auth/oauth/callback` when the code says
    `/auth/oauth/complete`) is the single most damaging error this wiki can contain,
    because it looks right. If you list routes, you must have opened the router file
    and read the actual route declarations — never reconstruct them from REST habits.
13. NO UNVERIFIED ABSOLUTE NEGATIVES. Do not write "nenhuma", "none", "no violations",
    "does not exist" unless you ran a search that would have found the counterexample,
    and say which search. Otherwise write "nao encontrei X" — the weaker claim is the
    honest one. This applies especially to claims about layering violations, missing
    features and absent configuration.
14. CODE CARTOGRAPHY: when a `<code_cartography>` block is present, it is a statically
    computed, verified dependency graph. Treat its edges as ground truth, use it to
    reason about layering and blast radius, and never contradict it or add edges to it
    that you did not verify by reading the code.
"""


# ----------------------------------------------------------------------
def repo_context(scan: RepoScan, config: WikiConfig, tree_entries: int = 260) -> str:
    """Context block shared by every prompt."""
    languages = ", ".join(
        f"{lang} ({count} files)" for lang, count in
        sorted(scan.languages.items(), key=lambda kv: -kv[1])[:8]
    ) or "unknown"

    modules = "\n".join(
        f"- `{module.key}` — {module.file_count} files, {module.total_lines} lines"
        f" ({', '.join(module.languages[:3])})"
        for module in scan.modules
    ) or "- (no modules detected)"

    manifests = ""
    for path, content in list(scan.manifest_excerpts.items())[:6]:
        manifests += f"\n<manifest path=\"{path}\">\n{content.strip()[:2500]}\n</manifest>\n"

    tree = render_tree([f.rel_path for f in scan.files], max_entries=tree_entries)

    return f"""<repository_context>
Project name: {config.resolved_project_name}
Root: {scan.root} (this is your working directory)
Git repository: {"yes" if scan.is_git_repo else "no"}
Analyzable files: {len(scan.files)} ({len(scan.source_files)} source files, {scan.total_lines} lines total)
Languages: {languages}

Detected entrypoints:
{bullet_list(scan.entrypoints, limit=15)}

Key configuration / build files:
{bullet_list(scan.key_files, limit=30)}

Detected modules (directory groupings used by this wiki):
{modules}

Directory tree (truncated):
```
{tree}
```
{manifests}</repository_context>
"""


# ----------------------------------------------------------------------
def wiki_pages_block(pages: list[tuple[str, str]]) -> str:
    """Index of the pages that will exist, so the model only links what exists."""
    if not pages:
        return ""
    listed = "\n".join(f"- [[{path}]] — {title}" for path, title in pages)
    return f"""<wiki_pages>
Pages in this wiki. These are the ONLY valid wikilink targets:
{listed}
- [[README]] — the wiki index
</wiki_pages>
"""


def _page_prompt(
    *,
    config: WikiConfig,
    context: str,
    title: str,
    goal: str,
    outline: str,
    investigate: str,
    extra_context: str = "",
) -> str:
    return f"""{context}
{extra_context}
<task>
Write the wiki page "{title}".

Goal: {goal}
</task>

<investigate>
Before writing, inspect the repository to answer this page. Suggested starting points:
{investigate}
Read enough files to be accurate. Prefer reading real code over inferring from names.
</investigate>

<required_outline>
The page MUST carry every heading below, in this order and at this level — translated
into the page's language, never copied verbatim from this English outline:

{outline}
</required_outline>

Output the Markdown page now, starting with `# {title}`.
"""


# ----------------------------------------------------------------------
# Fixed pages: (key, path, title_key, summary_key, section, order, goal, outline, investigate)
# ----------------------------------------------------------------------
CORE_PAGES: list[dict] = [
    {
        "key": "overview.introduction",
        "path": "01-overview/introduction.md",
        "title_key": "page.introduction.title",
        "summary_key": "page.introduction.summary",
        "section": "sec.overview",
        "order": 110,
        "goal": "Explain what this project is, the problem it solves, and what it can do — derived strictly from the code and docs present.",
        "outline": """## In one sentence
## Problem and context
## Main capabilities
(bullet list; each bullet points at the file/module that implements it)
## Out of scope
## Maturity
(evidence: tests, CI, versioning, TODOs)
## Gaps / Open questions""",
        "investigate": """- `README.md`, `docs/`, `CONTRIBUTING.md`, `CHANGELOG.md`
- the entrypoints listed above
- dependency manifests, to understand the domain""",
    },
    {
        "key": "overview.tech-stack",
        "path": "01-overview/tech-stack.md",
        "title_key": "page.tech-stack.title",
        "summary_key": "page.tech-stack.summary",
        "section": "sec.overview",
        "order": 120,
        "goal": "Inventory the technology stack: languages, runtimes, frameworks, notable libraries, build/test/lint tooling.",
        "outline": """## Summary
## Languages and runtimes
(table: Language | Required version | Where it is used | Evidence)
## Main frameworks and libraries
(table: Name | Version | What it does in this project | Evidence)
## Build, tests and quality
(table: Tool | Command | Configuration file)
## Infrastructure and external services
## Gaps / Open questions""",
        "investigate": """- dependency manifests (already included above when present)
- lock files, Dockerfile, Makefile, CI workflows
- linter/formatter/test-runner configuration files""",
    },
    {
        "key": "overview.repository-structure",
        "path": "01-overview/repository-structure.md",
        "title_key": "page.repository-structure.title",
        "summary_key": "page.repository-structure.summary",
        "section": "sec.overview",
        "order": 130,
        "goal": "Explain the repository layout: what lives where and why, plus naming/organisation conventions.",
        "outline": """## Directory map
(table: Path | Responsibility | Notes)
## Organisation conventions
(naming, where tests live, where configuration lives, code generation)
## Notable top-level files
## Where to start reading
(3-5 files, in order, with a reason for each)
## Gaps / Open questions""",
        "investigate": """- walk the top-level directories and read 1-2 representative files from each
- look for repeated file-naming patterns""",
    },
    {
        "key": "overview.glossary",
        "path": "01-overview/glossary.md",
        "title_key": "page.glossary.title",
        "summary_key": "page.glossary.summary",
        "section": "sec.overview",
        "order": 140,
        "goal": "Define the domain vocabulary that appears in the code, so a newcomer can read identifiers without guessing.",
        "outline": """## Domain terms
(table: Term | What it means in this project | Where it appears)
## Acronyms and abbreviations
(table: Acronym | Expansion | Context)
## Misleading names
(terms whose meaning here differs from common usage; write "none identified" if there are none)
## Gaps / Open questions""",
        "investigate": """- names of models/entities/tables/types
- comments and docstrings
- names repeated across identifiers and strings""",
    },
    {
        "key": "architecture.overview",
        "path": "02-architecture/overview.md",
        "title_key": "page.architecture-overview.title",
        "summary_key": "page.architecture-overview.summary",
        "section": "sec.architecture",
        "order": 210,
        "goal": "Describe the high-level architecture: architectural style, the main building blocks, and how they fit together.",
        "outline": """## Architectural style
(monolith / layered / hexagonal / microservices / CLI / library — with the evidence that supports the classification)
## Context diagram
(one ```mermaid block with `flowchart TD` showing the system, actors and external systems)
## Main building blocks
(table: Block | Responsibility | Code)
## Component diagram
(one ```mermaid block with `flowchart LR` showing the internal blocks and the dependencies between them)
## Dependency rules
(which layer may call which; observed violations)
To claim there are no violations you must have looked for them: use the graph in
`<code_cartography>` and/or a Grep for imports crossing layers, and state which check
you ran. Without that check, write "not exhaustively verified".
## Gaps / Open questions""",
        "investigate": """- entrypoints and what they instantiate
- top-level source directories and the imports between them
- route/handler/command configuration""",
    },
    {
        "key": "architecture.components",
        "path": "02-architecture/components.md",
        "title_key": "page.components.title",
        "summary_key": "page.components.summary",
        "section": "sec.architecture",
        "order": 220,
        "goal": "Detail each logical component: responsibility, public surface, collaborators, and boundaries.",
        "outline": """## Component inventory
(table: Component | Module/path | Single responsibility | Depends on)
## Detail per component
(one `###` subsection per component with: Responsibility, Public interface, Collaborators, Invariants/assumptions)
## Notable coupling
## Gaps / Open questions""",
        "investigate": """- the modules listed in the repository context
- classes/services/handlers exported by each one""",
    },
    {
        "key": "architecture.data-flow",
        "path": "02-architecture/data-flow.md",
        "title_key": "page.data-flow.title",
        "summary_key": "page.data-flow.summary",
        "section": "sec.architecture",
        "order": 230,
        "goal": "Trace the main end-to-end flows through the system, from entrypoint to persistence/response.",
        "outline": """## Main flows
(list of the 2-5 most important flows, one line each)
## Detail per flow
(one `###` subsection per flow, each with: a summary paragraph, one ```mermaid block with `sequenceDiagram`, and a numbered list of the steps with the file:line of each step)
## Error handling in the flows
## Asynchronous and background work
## Gaps / Open questions""",
        "investigate": """- follow a request/command from the entrypoint to its final effect
- look for handlers, controllers, use cases, jobs, queue consumers""",
    },
    {
        "key": "architecture.data-model",
        "path": "02-architecture/data-model.md",
        "title_key": "page.data-model.title",
        "summary_key": "page.data-model.summary",
        "section": "sec.architecture",
        "order": 240,
        "goal": "Document the data model: entities, their fields and relationships, storage technology and migrations.",
        "outline": """## Persistence technology
## Entities
(table: Entity | Defined in | Description)
## Relationship diagram
(one ```mermaid block with `erDiagram`; if there is no relational model, use `classDiagram` for the main data structures)
## Fields per entity
(one `###` subsection per entity with a table: Field | Type | Constraints | Notes)
## Migrations and schema evolution
## Gaps / Open questions""",
        "investigate": """- ORM models, structs, dataclasses, schemas, .sql files, migrations
- if there is no database, document the in-memory data structures and serialized formats""",
    },
    {
        "key": "architecture.integrations",
        "path": "02-architecture/integrations.md",
        "title_key": "page.integrations.title",
        "summary_key": "page.integrations.summary",
        "section": "sec.architecture",
        "order": 250,
        "goal": "List every external system this project talks to at runtime and how the integration is implemented.",
        "outline": """## Integration inventory
(table: External system | Direction (in/out) | Protocol | Implemented in | Credentials)
## Detail per integration
(one `###` subsection each: what it is for, the contract, authentication, error handling and retries)
## Interfaces exposed by this project
(table: Interface | Method/type | Declared at (file:line) | What it does)
MANDATORY: every row of this table corresponds to a declaration you READ. Open the
route/handler files and copy the paths exactly as they appear in the code, including the
prefix the router is mounted with. If you found no route declarations, say so — do not
fill the table with plausible endpoints.
## Gaps / Open questions""",
        "investigate": """- HTTP clients, SDKs, database drivers, queue producers/consumers
- environment variables holding URLs, hosts, keys
- READ the route files in full and also find where the router is mounted (an endpoint's
  final prefix usually comes from where it is registered, not from the route file)
- confirm each endpoint with a Grep for the literal path before writing it down""",
    },
    {
        "key": "architecture.cross-cutting",
        "path": "02-architecture/cross-cutting.md",
        "title_key": "page.cross-cutting.title",
        "summary_key": "page.cross-cutting.summary",
        "section": "sec.architecture",
        "order": 260,
        "goal": "Document the cross-cutting mechanisms: configuration, error handling, logging, auth, security, concurrency, performance.",
        "outline": """## Configuration
## Error handling
## Logging and observability
## Authentication and authorization
## Security
(secret management, input validation, observed risk surfaces)
## Concurrency and parallelism
## Performance and caching
## Gaps / Open questions""",
        "investigate": """- middleware, decorators, interceptors, exception wrappers
- logging, metrics and tracing configuration
- reads of environment variables and configuration files""",
    },
    {
        "key": "architecture.decisions",
        "path": "02-architecture/decisions.md",
        "title_key": "page.decisions.title",
        "summary_key": "page.decisions.summary",
        "section": "sec.architecture",
        "order": 270,
        "goal": "Surface the significant design decisions visible in the code, with their trade-offs. Infer, but label inference as such.",
        "outline": """## Significant decisions
(one `###` subsection per decision, in the format: **Context** / **Decision** / **Evidence in the code** / **Trade-offs** / **Confidence** (high if documented, medium if only inferred from the code))
## Recurring patterns
## Observable technical debt
(TODOs, FIXMEs, duplication, workarounds — with file:line)
## Gaps / Open questions""",
        "investigate": """- ADRs or design notes in `docs/`
- comments that explain "why"
- TODO/FIXME/HACK/XXX in the code""",
    },
    {
        "key": "guides.getting-started",
        "path": "05-guides/getting-started.md",
        "title_key": "page.getting-started.title",
        "summary_key": "page.getting-started.summary",
        "section": "sec.guides",
        "order": 510,
        "goal": "Give a working local setup path: prerequisites, install, configure, run, verify.",
        "outline": """## Prerequisites
(table: Requirement | Version | How to check)
## Installation
(numbered steps with real command blocks from this repository)
## Minimum configuration
(variables/files required to boot)
## Running the project
## Verifying it works
## Common startup problems
## Gaps / Open questions""",
        "investigate": """- README, Makefile, package.json scripts, docker-compose
- .env.example files
- only document commands that actually exist in the repository""",
    },
    {
        "key": "guides.development",
        "path": "05-guides/development.md",
        "title_key": "page.development.title",
        "summary_key": "page.development.summary",
        "section": "sec.guides",
        "order": 520,
        "goal": "Describe the day-to-day development workflow: code layout conventions, tests, linting, and the local feedback loop.",
        "outline": """## Development loop
## Useful commands
(table: Goal | Command | Where it is defined)
## Tests
(structure, how to run them, how to write a new one, coverage)
## Code quality
(linters, formatters, type checking, pre-commit hooks)
## Code conventions
(observed in the existing code, not invented)
## How to add a new feature
(concrete steps with the files typically touched)
## Gaps / Open questions""",
        "investigate": """- test runner configuration and existing test files
- linter/formatter configuration
- CI workflows, to see what is actually enforced""",
    },
    {
        "key": "operations.configuration",
        "path": "06-operations/configuration.md",
        "title_key": "page.configuration.title",
        "summary_key": "page.configuration.summary",
        "section": "sec.operations",
        "order": 610,
        "goal": "Produce a complete configuration reference: every env var and config option the code actually reads.",
        "outline": """## Configuration sources
(precedence between defaults, files, environment, flags)
## Environment variables
(table: Variable | Required | Default | Description | Read at (file:line))
MANDATORY: a variable only enters this table if you found it being read in the code. The
name must be copied verbatim. A variable that appears only in a `.env.example` but that
nothing reads is a different case: mark it as unused.
## Configuration files
(table: File | Format | Contents | Used by)
## Command-line flags
## Secrets
(which ones are sensitive and how they are supplied — never include real values)
## Gaps / Open questions""",
        "investigate": """- search for environment reads (os.environ, process.env, os.Getenv, System.getenv, ...)
- .env.example, settings/config files
- CLI argument parsers""",
    },
    {
        "key": "operations.deployment",
        "path": "06-operations/deployment.md",
        "title_key": "page.deployment.title",
        "summary_key": "page.deployment.summary",
        "section": "sec.operations",
        "order": 620,
        "goal": "Document how the project is built, packaged, released and deployed, based on CI config and container/infra files.",
        "outline": """## Artifacts produced
## Build process
## CI/CD pipeline
(table: Workflow/job | Trigger | What it does | File)
## Containerization
(images, multi-stage, ports, volumes — if applicable)
## Environments and promotion
## Rollback
## Gaps / Open questions""",
        "investigate": """- `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`, deploy files
- `Dockerfile`, `docker-compose`, k8s/helm/terraform manifests
- release scripts and packaging configuration""",
    },
    {
        "key": "operations.observability",
        "path": "06-operations/observability.md",
        "title_key": "page.observability.title",
        "summary_key": "page.observability.summary",
        "section": "sec.operations",
        "order": 630,
        "goal": "Document what the system emits at runtime and how to diagnose it when it misbehaves.",
        "outline": """## Logs
(format, destinations, levels, most useful events)
## Metrics and tracing
## Health checks and readiness
## Known failure modes
(table: Symptom | Likely cause | Where to look | Mitigation)
## Step-by-step diagnosis
## Gaps / Open questions""",
        "investigate": """- logging configuration and call sites
- health/readiness endpoints, metrics exporters
- exception handling blocks and what they record""",
    },
]


def build_core_prompt(
    page: dict, scan: RepoScan, config: WikiConfig, graph_context: str = "",
    page_index: str = "",
) -> str:
    # The cartography graph only helps pages that discuss structure or flows.
    uses_graph = page["key"].startswith(("architecture.", "overview.repository"))
    t = translator(config.language)
    return _page_prompt(
        config=config,
        context=repo_context(scan, config),
        extra_context=(graph_context if uses_graph else "") + page_index,
        title=t(page["title_key"]),
        goal=page["goal"],
        outline=page["outline"],
        investigate=page["investigate"],
    )


# ----------------------------------------------------------------------
CARTOGRAPHY_PAGE = {
    "key": "cartography.reading-the-map",
    "path": "07-cartography/reading-the-map.md",
    "title_key": "page.reading-the-map.title",
    "summary_key": "page.reading-the-map.summary",
    "section": "sec.cartography",
    "order": 710,
    "goal": (
        "Interpret the pre-computed static dependency graph: explain the shape of the "
        "codebase, which files are load-bearing, where the layering holds or breaks, and "
        "what the graph implies for anyone changing this code."
    ),
    "outline": """## Shape of the graph
(what the topology reveals: layered? a star around one hub? no structure?)
## Observed layers
(table: Layer | Representative files | Depends on | Depended on by)
## Critical files
(table: File | Why it is critical | Blast radius of a change)
## Entrypoints
## Cycles and layering violations
(for each cycle in the graph: why it exists, and what breaking it would cost)
## Orphan files
(for each: dead code, dynamic loading, or entrypoint?)
## Maintenance risks
## Gaps / Open questions""",
    "investigate": """- the graph in `<code_cartography>` is already verified truth: do NOT invent new edges
- read the hub files and the ones involved in cycles to explain *why* each link exists
- confirm each orphan really is dead code before classifying it as such""",
}


def build_cartography_prompt(
    scan: RepoScan, config: WikiConfig, graph_context: str, page_index: str = ""
) -> str:
    return _page_prompt(
        config=config,
        context=repo_context(scan, config, tree_entries=120),
        extra_context=graph_context + page_index,
        title=translator(config.language)("page.reading-the-map.title"),
        goal=CARTOGRAPHY_PAGE["goal"],
        outline=CARTOGRAPHY_PAGE["outline"],
        investigate=CARTOGRAPHY_PAGE["investigate"],
    )


# ----------------------------------------------------------------------
def build_module_prompt(
    module: ModuleInfo, scan: RepoScan, config: WikiConfig, graph_context: str = "",
    page_index: str = "",
) -> str:
    files = "\n".join(
        f"- `{f.rel_path}` ({f.lines} linhas, {human_size(f.size)}, {f.language})"
        for f in module.files[:80]
    )
    if module.file_count > 80:
        files += f"\n- ... (+{module.file_count - 80} files)"

    outline = """## Responsibility
## Files
(table: File | Role | Lines)
## Public interface
(what this module exposes to other modules: functions, classes, types, routes, commands)
## Dependencies
(table: Depends on | Kind (internal/external) | What for)
## Internal diagram
(one ```mermaid block with `flowchart LR` showing the main files/types and the relationships between them)
## Important flows
## Things to watch out for
(pitfalls, shared state, invariants, TODOs)
## Gaps / Open questions"""

    investigate = f"""- read this module's files, starting with the largest and with `__init__`/`index`/`mod`
- use the `<code_cartography>` block to fill the dependencies section with real edges
- find who imports this module (`Grep` for the module name) to infer its public interface
- files in this module:
{files}"""

    return _page_prompt(
        config=config,
        context=repo_context(scan, config, tree_entries=120),
        extra_context=graph_context + page_index,
        title=translator(config.language)("page.module.title", module=module.key),
        goal=(
            f"Document the module `{module.key}` at a level of detail that lets an engineer "
            "change it safely without reading every file first."
        ),
        outline=outline,
        investigate=investigate,
    )


# ----------------------------------------------------------------------
def build_reference_prompt(
    module: ModuleInfo,
    files: list,
    part: int,
    total_parts: int,
    scan: RepoScan,
    config: WikiConfig,
    page_index: str = "",
) -> str:
    file_list = "\n".join(f"- `{f.rel_path}` ({f.lines} linhas)" for f in files)
    headings = "\n".join(f"## {f.rel_path}" for f in files)
    t = translator(config.language)
    part_note = (
        t("page.reference.part", part=part, total=total_parts)
        if total_parts > 1 else ""
    )

    outline = f"""## Scope
(one sentence plus the list of files covered on this page)

Then exactly these {len(files)} `##` sections, in this order. These headings are FILE
PATHS: copy them verbatim, never translate them (rule 5 applies to prose headings, not
to identifiers). None may be missing:

{headings}

Each of those file sections carries:

### Purpose
(1-2 sentences)

### Symbols
(table: Symbol | Kind (class/function/const/type) | Signature | Description)

### Detail
(one `#### <symbol name>` subsection per non-trivial public symbol, with:
the signature in a code block, parameters, return value, exceptions/errors, side
effects, and a usage note where it helps)

### Dependencies
(relevant internal and external imports)

End the page with:

## Gaps / Open questions"""

    investigate = f"""- READ IN FULL each of the following {len(files)} files before writing
- transcribe signatures exactly as they appear in the code (types included)
- document private/internal symbols only when they are central to understanding the file
- COVERAGE is this page's success criterion: a short section per file beats long
  sections with files missing
- files to cover on this page:
{file_list}"""

    return _page_prompt(
        config=config,
        context=repo_context(scan, config, tree_entries=80),
        extra_context=page_index,
        title=t("page.reference.title", module=module.key) + part_note,
        goal=(
            "Produce low-level API reference documentation for the listed files: every "
            "public symbol with its exact signature, parameters, return value and side effects."
        ),
        outline=outline,
        investigate=investigate,
    )
