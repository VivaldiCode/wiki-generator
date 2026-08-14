# wiki-generator

Generate a **complete, standardized engineering wiki** from any repository, using
**Claude Code in headless mode** — with the subscription already authenticated in the
CLI, no `ANTHROPIC_API_KEY` and no per-API billing.

Output is **plain Markdown with Obsidian wikilinks**: open the folder as a vault, no
extra tooling.

```bash
wiki-generator --source ~/code/my-project
```

---

## Contents

- [Install](#install)
- [Usage guide](#usage-guide)
- [What it produces](#what-it-produces)
- [Code cartography](#code-cartography)
- [Built-in verification](#built-in-verification)
- [Choosing a model](#choosing-a-model)
- [Options reference](#options-reference)
- [How it works](#how-it-works)
- [Limitations](#limitations)

---

## Install

Requires **Python 3.10+** and [Claude Code](https://claude.com/claude-code) installed and
authenticated.

```bash
claude auth          # if not authenticated yet
git clone https://github.com/VivaldiCode/wiki-generator.git
cd wiki-generator
pip install -e .
```

Verify:

```bash
wiki-generator --version
```

---

## Usage guide

### 1. One repository

```bash
wiki-generator --source ~/code/my-project
```

The wiki lands in `~/code/my-project/wiki/`.

### 2. Choose where the wiki goes

```bash
wiki-generator --source ~/code/my-project --output ~/wikis
```

With `--output`, every repository gets **its own subfolder, named after the repo**:

```
~/wikis/
└── my-project/
    ├── README.md
    ├── 01-overview/
    └── ...
```

### 3. Several repositories at once

Point at a folder containing multiple git repositories. All of them are discovered and
processed, **one wiki per repository** — never a single wiki mixing independent projects
(a shared architecture, stack and glossary would describe none of them accurately).

```bash
wiki-generator --source ~/code --output ~/wikis
```

```
Detetados 4 repositorios git em /Users/me/code:
  - api-gateway          -> /Users/me/wikis/api-gateway
  - web-app              -> /Users/me/wikis/web-app
  - mobile               -> /Users/me/wikis/mobile
  - infra                -> /Users/me/wikis/infra
Uma wiki por repositorio. Usa --single para gerar uma so.
```

Without `--output`, each wiki is written inside its own repository (`<repo>/wiki/`).

To treat the whole tree as **one** project (a monorepo without separate git sub-repos),
use `--single`.

### 4. See the plan before spending quota

```bash
wiki-generator --source ~/code/my-project --dry-run
```

Lists every page that would be generated, without calling the model.

### 5. Follow progress

The CLI reports in real time. Each page shows up as soon as it completes, and every 20
seconds a status line reports throughput and estimated time remaining:

```
[2/7] api-gateway
======================================================================
A analisar o repositorio...
  312 ficheiros | 148 de codigo | 41022 linhas | 6 modulos
A construir o grafo de dependencias (code cartography)...
  187 nos | 421 ligacoes | 2 ciclos | 9 ficheiros isolados

Plano: 44 paginas geradas por modelo
[1/44] + 01-overview/introduction.md
[2/44] + 01-overview/tech-stack.md
    ... 2/44 concluidas | 10 em curso | 1m decorridos | ETA ~7m
[3/44] + 02-architecture/overview.md
```

`+` generated · `=` served from cache · `!` failed

Use `--verbose` to also see each page as it starts.

### 6. Incremental regeneration

Every page stores a fingerprint of the source files it was built from, in
`.wiki-manifest.json`. On a second run, only pages whose source files changed are
regenerated:

```bash
wiki-generator --source ~/code/my-project         # second run: mostly cached
wiki-generator --source ~/code/my-project --force # ignore the cache
```

Changing the model, the language or the wiki structure also invalidates the cache
automatically.

### 7. Regenerate only part of it

```bash
# one specific page
wiki-generator --source . --only architecture.overview

# a whole section
wiki-generator --source . --only architecture

# a page type
wiki-generator --source . --only module --only reference
```

The index still lists the whole wiki, not just what was regenerated.

### 8. Save logs for debugging

```bash
wiki-generator --source ~/code/my-project --log-dir /tmp/wg-logs
```

Every Claude Code call is written to `/tmp/wg-logs/<repo>/<page>.json` with the prompt
sent, the system prompt, stdout, stderr and the exit code. Retries get a `.retryN`
suffix. When a page comes out wrong, this is where you find out why.

> Logs contain the full prompt, which includes manifest excerpts and your repository's
> file tree. Treat the log folder with the same care as the code.

### 9. Large repositories

```bash
wiki-generator --source . \
  --no-reference \                 # skip the low-level reference
  --max-modules 15 \
  --exclude '**/generated/**' \
  --exclude '**/*.pb.go'
```

### 10. Language

The wiki content language is configurable:

```bash
wiki-generator --source . --language pt     # en (default), pt, pt-br, es, fr, de, it
```

### 11. Configuration file

```bash
wiki-generator --config wiki.config.json
```

```json
{
  "repo_path": ".",
  "output_path": "./wiki",
  "model": "haiku",
  "language": "en",
  "concurrency": 6,
  "max_modules": 20,
  "exclude_globs": ["**/generated/**", "**/migrations/**"]
}
```

Command-line options override the file.

---

## What it produces

A fixed structure, identical across repositories — which makes wikis comparable between
projects and predictable to read:

```
wiki/
├── README.md                        index + repository metrics
├── SUMMARY.md                       linear index
│
├── 01-overview/                     HIGH LEVEL
│   ├── introduction.md              what it is, what problem it solves
│   ├── tech-stack.md                languages, frameworks, tooling
│   ├── repository-structure.md      directory map and conventions
│   └── glossary.md                  domain vocabulary
│
├── 02-architecture/                 ARCHITECTURE
│   ├── overview.md                  architectural style + context diagrams
│   ├── components.md                each component and its boundaries
│   ├── data-flow.md                 end-to-end flows + sequence diagrams
│   ├── data-model.md                entities, ER diagram, migrations
│   ├── integrations.md              APIs, queues, external services
│   ├── cross-cutting.md             config, errors, logging, auth, security
│   └── decisions.md                 design decisions, trade-offs, tech debt
│
├── 03-modules/<module>.md           MID LEVEL — one per module
│
├── 04-reference/<module>.md         LOW LEVEL — per-file API:
│                                    symbols, signatures, parameters, side effects
│
├── 05-guides/
│   ├── getting-started.md
│   └── development.md
│
├── 06-operations/
│   ├── configuration.md             env vars and config the code actually reads
│   ├── deployment.md                build, CI/CD, containers
│   └── observability.md             logs, metrics, troubleshooting
│
└── 07-cartography/                  CODE CARTOGRAPHY
    ├── file-graph.md                Mermaid graph: which file imports which
    ├── module-graph.md              aggregated graph + coupling matrix
    ├── modules/<module>.md          per-module detail (large repositories)
    ├── file-graph.mmd               full graph, untruncated
    ├── graph.json                   graph as JSON for external tooling
    └── reading-the-map.md           reading the graph: hubs, layers, cycles
```

Every page has a **mandatory outline** — same headings, same order, in any repository.
That is what makes the output standardized rather than free-form prose.

Every page ends with **"Gaps / Open questions"**: where the model could not determine
something from the code, it has to say so instead of inventing.

### Obsidian

The output is a vault. All internal links are wikilinks
(`[[02-architecture/overview]]`), resolved from the vault root and without the `.md`
extension. Open the folder in Obsidian — `README.md` is the index, and Obsidian's graph
view gives a second perspective on the documentation.

Two details that only show up when you do this for real, both handled: inside Markdown
tables the alias pipe is escaped (`[[target\|text]]`), and source file paths stay as
inline code rather than becoming links — they are not notes.

---

## Code cartography

The dependency graph is built by **deterministic static analysis**
(`wiki_generator/cartography.py`), not by the model — a single invented edge would
destroy trust in the diagram.

It extracts `import` / `require` / `include` statements from Python, TypeScript/JavaScript,
Go, Java, Kotlin, Rust, C/C++, Ruby, PHP, Dart and Shell, resolves each specifier to a
real file in the repository, and computes:

- **hubs** — files with the most connections (largest blast radius for a change)
- **entrypoints** — they import, nothing imports them
- **orphan files** — no edges at all (dead code? dynamic loading?)
- **dependency cycles** — layering violations
- **inter-module coupling**

Languages present in the repository but without an extractor (Swift, C#, Elixir, SQL,
Terraform, …) are **flagged on the page**: their files appear with no edges, and the wiki
states explicitly that this is a tool limitation, not dead code.

### Full coverage, navigable pages

The diagram covers **every** file. Past ~140 nodes a single diagram stops rendering, so
coverage is split rather than reduced:

- `file-graph.md` — metrics, aggregated per-module view, index of the module pages
- `07-cartography/modules/<module>.md` — one page per module, split into parts of 180
  files when needed
- `file-graph.mmd` / `graph.json` — the complete graph, untruncated

**Navigating between modules.** In each module's diagram, files from other modules are
drawn dashed and are clickable (Mermaid `click`). Each page also carries a *neighbouring
modules* table with the import count in each direction. You can walk the graph module by
module instead of staring at one diagram.

The methodology is formalized as a reusable skill in
[`.claude/skills/code-cartography/SKILL.md`](.claude/skills/code-cartography/SKILL.md).

---

## Built-in verification

Three checks run at the end of every generation, all deterministic:

**Reference page coverage.** Confirms every file in a batch actually got its own section.
If one is missing, the call is repeated with an explicit list of what was left out; if it
is still missing, the page carries a visible warning rather than passing the omission off
as documented.

**Wikilinks.** A broken link points at a note that will never exist. Links without a
target are downgraded to plain text and reported. The check ignores code blocks — `[[ -f
x ]]` in bash is not a link. A final re-check reads from disk again, to catch anything
that wrote after the fix.

**`file:line` citations.** Every citation is checked against the repository and
classified as **invalid** (file does not exist, or line past EOF) or **unrooted** (the
file exists, but the path is relative to a subdirectory). What this does **not** catch —
and does not pretend to — is a citation that is in range but points at the wrong line.

---

## Choosing a model

The default is `haiku` because it is cheap and fast. But in a real audit — 7 repositories
from a production project, 258 pages, 440 claims checked against the code by independent
reviewers with adversarial refutation — the difference between models was not one of
degree, but of kind.

On the same analytical pages, with identical prompts:

| | haiku | sonnet |
|---|---:|---:|
| Confirmed factual errors | 28 | **20** |
| Error rate | 1 in 15 claims | **1 in 25** |
| Invented endpoints / dependencies / files | most of them | **0** |
| Citation errors (wrong line) | few | almost all |

`haiku` invents **content**: plausible REST endpoints that do not exist in the router,
dependencies absent from the manifest, permissions absent from the Android manifest. A
reader believes it and acts on it.

`sonnet` errs on **citation precision**: it points at `pubspec.yaml:62` when the truth is
`:41`. The reader does not find what was promised, but is not left believing something
false.

Hardening the prompts fixed the pattern it was aimed at — requiring `file:line` per row
in endpoint tables, and instructing the model to read where the router is *mounted* — and
cut invented endpoints by 67%. It could do no more: **the remaining content errors are a
model limit, not a prompt limit.** Demanding citations even converted vague, unfalsifiable
claims into precise, wrong ones.

Practical recommendation — reference and module pages are mechanical transcription and
`haiku` handles them well; the analytical pages carry the claims someone will act on:

```bash
wiki-generator --source . --model haiku                    # the bulk

wiki-generator --source . --model sonnet \
  --only overview.introduction --only overview.tech-stack \
  --only architecture.overview --only architecture.integrations \
  --only operations.configuration                          # the ones that matter
```

Treat any `file:line` citation as an approximate pointer, on any model: line numbers
drift as code is edited and no model gets them reliably right.

---

## Options reference

### Target

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | `.` | Repository, or a folder containing several git repositories |
| `--output`, `-o` | `<repo>/wiki` | Output folder; each repo gets `<output>/<name>/` |
| `--config`, `-c` | — | JSON configuration file |

`--repo`/`-r` and `--out` still work as aliases.

### Model

| Flag | Default | Description |
|---|---|---|
| `--model`, `-m` | `haiku` | Alias (`haiku`, `sonnet`, `opus`) or full model name |
| `--fallback-model` | — | Fallback model when the primary is unavailable |
| `--concurrency`, `-j` | `4` | Pages generated in parallel |
| `--timeout` | `600` | Per-page timeout, in seconds |
| `--max-retries` | `2` | Extra attempts on transient failures |
| `--permission-mode` | `bypassPermissions` | Claude Code permission mode |
| `--claude-bin` | `claude` | Claude Code binary |
| `--log-dir` | — | Save Claude Code calls for debugging |

### Content

| Flag | Default | Description |
|---|---|---|
| `--language`, `-l` | `en` | `en`, `pt`, `pt-br`, `es`, `fr`, `de`, `it` |
| `--project-name` | directory name | Project name shown in the wiki |
| `--audience` | repository engineers | Target audience for the documentation |

### Structure

| Flag | Default | Description |
|---|---|---|
| `--module-depth` | `2` | Directory depth used to group modules |
| `--max-modules` | `25` | Cap on documented modules |
| `--files-per-reference-page` | `6` | Files per reference page |
| `--max-reference-pages` | `60` | Cap on reference pages |
| `--no-reference` | — | Skip the low-level reference |
| `--no-cartography` | — | Skip the dependency graph |
| `--single` | — | Treat the whole tree as a single repository |
| `--include` / `--exclude` | — | File globs (repeatable) |

### Behaviour

| Flag | Description |
|---|---|
| `--force`, `-f` | Ignore the cache |
| `--dry-run` | Print the plan and exit |
| `--only` | Page key, prefix or type (repeatable) |
| `--verbose`, `-v` | Also report each page as it starts |

---

## How it works

1. **Scan** (`scanner.py`) — walks the repository honouring `.gitignore` (via
   `git ls-files`), classifies files by language and groups them into modules. No model.
2. **Cartography** (`cartography.py`) — file-to-file dependency graph by static analysis.
   No model.
3. **Plan** (`planner.py`) — the fixed structure becomes a list of pages, each with its
   own prompt and its own set of source files.
4. **Generation** (`generator.py`) — each page is an isolated `claude -p` run with only
   `Read`/`Glob`/`Grep` available. The model reads the real code instead of receiving it
   in the prompt, which keeps per-page cost low even on large repositories.
5. **Assembly and verification** (`assembler.py`, `links.py`, `citations.py`) — index,
   summary, link validation and citation validation.

Zero dependencies beyond the Python standard library.

### Security

Files that look like credentials (`*service-account*.json`, `*-adminsdk-*.json`, `*.pem`,
`.env`, SSH keys, …) are **excluded from the scan by default and reported**, not silently
dropped — the generator gives a model read access to the repository and writes
documentation from it, so nothing should point at secrets. `.env.example` and friends stay
included: they are configuration documentation, not secrets.

---

## Limitations

- The output is model-generated. It is a **useful map, not the source of truth** — the
  code is. The cartography pages are the exception: they are deterministic.
- C# namespaces, bundler aliases (`@/components`) and dependency injection do not yield
  reliable edges; they show up as unresolved rather than as invented edges.
- Go packages are directories, not files: an import links to a representative file.
- No API key required, but it consumes your Claude Code subscription quota.

> Source-code comments and docstrings are in Portuguese. Everything user-facing — CLI
> output, `--help`, and the deterministically generated wiki pages — is localized: the
> tool speaks English by default and follows `--language` for the wiki content.

---

## License

MIT
