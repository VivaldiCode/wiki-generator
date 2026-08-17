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
- [Semantic verification (`--verify`)](#semantic-verification---verify)
- [Running several clients at once](#running-several-clients-at-once)
- [Choosing a client](#choosing-a-client)
- [Running on AWS with Bedrock](#running-on-aws-with-bedrock)
- [Cost tracking](#cost-tracking)
- [Choosing a model](#choosing-a-model)
- [Options reference](#options-reference)
- [How it works](#how-it-works)
- [Limitations](#limitations)

---

## Interactive setup

Run it with no arguments and it asks, checking what it can instead of asking:

```
wiki-generator — interactive setup

Clients on this machine:
  yes  claude    /Users/me/.local/bin/claude
  yes  grok      /Users/me/.grok/bin/grok
  no   opencode     (see opencode's install docs)

  Repository, or a folder containing several [/Users/me/code]:
    4 git repositories found under /Users/me/code.
  Where the wikis go (one folder per repository) [/Users/me/wikis]:
  Split the 4 repositories across 2 clients by size (y/n) [y]:
  ...
  Save these answers to reuse later (y/n) [y]:
    Saved. Reuse with: wiki-generator --profile /Users/me/wiki.wiki-profile.json
```

Piped or scripted, it prints `--help` instead — the wizard only appears when
there is someone there to answer. A saved profile replays with `--profile`, and
flags given alongside it win, so a saved run can be adjusted without editing it.

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
Found 4 git repositories in /Users/me/code:
  - api-gateway          -> /Users/me/wikis/api-gateway
  - web-app              -> /Users/me/wikis/web-app
  - mobile               -> /Users/me/wikis/mobile
  - infra                -> /Users/me/wikis/infra
One wiki per repository. Use --single to generate a single one.
```

Repositories are processed **smallest first**, by file count. A long multi-repo run is
far more useful when the quick wins land early: complete wikis appear within minutes, and
if the run is cut short the casualty is the one repo that was going to take longest anyway.

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
Scanning repository...
  312 files | 148 source | 41022 lines | 6 modules
Building the dependency graph (code cartography)...
  187 nodes | 421 edges | 2 cycles | 9 orphan files

Plan: 44 model-generated pages
[1/44] + 01-overview/introduction.md
[2/44] + 01-overview/tech-stack.md
    ... 2/44 done | 10 in flight | 1m elapsed | ETA ~7m
[3/44] + 02-architecture/overview.md
```

`+` generated · `=` served from cache · `!` failed

Use `--verbose` to also see each page as it starts.

### 6. Repositories with nothing to document are skipped

A wiki built from a one-line README is twenty pages of empty headings and no
information — worse than no wiki, because it looks like documentation. Repositories
below a content threshold are skipped, and the reason is reported:

```
[1/4] vazio
Scanning repository...
  Skipped: no analysable files (check --include/--exclude, or the directory is empty)
[4/4] so-readme
  Skipped: only 1 line(s) across 1 file(s), below the --min-lines threshold of 50

======================================================================
Skipped 2 of 4 repositories:
  - vazio: no analysable files (check --include/--exclude, or the directory is empty)
  - so-readme: only 1 line(s) across 1 file(s), below the --min-lines threshold of 50
```

The threshold counts lines across the files that survive the scan — lock files,
generated output and binaries are already excluded, so what is measured is what a reader
would call the repository's content. The default is 50 lines; `--min-lines 0` disables it.

A skip is not a failure: the exit code stays 0, and no output folder is created for the
skipped repository.

### 7. Interrupted runs roll back

A run that dies halfway — Ctrl-C, a crash, a closed laptop — would leave a wiki whose
index, cartography and pages disagree with each other. That half-state is worse than no
wiki, because nothing about it says it is half: the pages that did land look finished.

So generation is transactional, per repository. Before touching anything the current wiki
is copied aside and a marker is written; the marker is only cleared on a clean finish.
Finding one at startup is proof the previous run did not complete, and the snapshot is
restored:

```
! Previous run (started 2026-08-14T10:28:52Z) did not finish.
  Rolled back: 5 files restored, 5 removed.
```

Ctrl-C and `kill` roll back in-process and stop the Claude Code children immediately. A
`kill -9` cannot be caught, so the marker is found and honoured on the next run instead.

In a multi-repo run the granularity is one repository: a crash on the fourth rolls back
the fourth only — the three already committed are complete and correct, and stay.

Use `--no-rollback` to keep a partial run's output and continue from it incrementally.

### 8. When a page fails

A failed page does not abandon the run — the other pages are still written, the index is
still built, and the exit code is `1`.

What the CLI prints is the diagnostic the CLI actually reported, grouped by cause. A run
usually fails for one reason, and repeating it once per page buries the line you have to
act on:

```
Failed:    19
  ! Claude usage limit reached | HTTP 429
      01-overview/introduction.md
      01-overview/tech-stack.md
      ... and 17 more
  19 page(s) need another run. Repeat the same command without --force: only what is
  missing or out of date is regenerated.
```

The index says the same thing, under **Pages that failed**, and distinguishes three
outcomes — because they need different things from you:

| | What it means | What to do |
|---|---|---|
| **not written** | No file. Links to it were degraded to plain text. | Run again |
| **out of date** | The previous version is still there, but the sources changed | Run again |
| **no harm done** | The version on disk already matches the sources | Nothing |

The third is the common one, and the reason a failed page is **not** dropped from the
index: the file opens perfectly well, so removing it would shrink the wiki over a
transient error.

A second run costs a fraction of the first — only missing and out-of-date pages are
regenerated. Do not add `--force`, which would pay for every page again.

### 9. Incremental regeneration

Every page stores a fingerprint of the source files it was built from, in
`.wiki-manifest.json`. On a second run, only pages whose source files changed are
regenerated:

```bash
wiki-generator --source ~/code/my-project         # second run: mostly cached
wiki-generator --source ~/code/my-project --force # ignore the cache
```

Changing the model, the language or the wiki structure also invalidates the cache
automatically.

### 10. Regenerate only part of it

```bash
# one specific page
wiki-generator --source . --only architecture.overview

# a whole section
wiki-generator --source . --only architecture

# a page type
wiki-generator --source . --only module --only reference
```

The index still lists the whole wiki, not just what was regenerated.

### 11. Save logs for debugging

```bash
wiki-generator --source ~/code/my-project --log-dir /tmp/wg-logs
```

Every Claude Code call is written to `/tmp/wg-logs/<repo>/<page>.json` with the prompt
sent, the system prompt, stdout, stderr and the exit code. Retries get a `.retryN`
suffix. When a page comes out wrong, this is where you find out why.

> Logs contain the full prompt, which includes manifest excerpts and your repository's
> file tree. Treat the log folder with the same care as the code.

### 12. Large repositories

```bash
wiki-generator --source . \
  --no-reference \                 # skip the low-level reference
  --max-modules 15 \
  --exclude '**/generated/**' \
  --exclude '**/*.pb.go'
```

### 13. Language

The wiki content language is configurable:

```bash
wiki-generator --source . --language pt     # en (default), pt, pt-br, es, fr, de, it
```

### 14. Configuration file

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

All three answer *"does this resolve?"*. None answers *"is this sentence true?"* — that
is what `--verify` is for.

---

## Semantic verification (`--verify`)

**Off by default. It is slow, it costs real quota, and it is advisory.**

An invented REST endpoint is well-formed prose citing a file that exists: no deterministic
check can catch it. `--verify` reads the finished wiki back and confronts its claims with
the code.

```bash
wiki-generator --repo ~/code/api --verify
```

```
Verifying 5 page(s) with sonnet (budget $5.00)...
    verifying 01-overview/introduction.md
  5 verified, 0 cached | 34 claim(s) | 6 finding(s), 0 overturned | $2.51
  ! [high] 01-overview/introduction.md: Runtime dependencies are express and redis
  ! [high] 01-overview/introduction.md: Service exposes DELETE /api/users/:id
```

### How it works

1. **Extract** — one cheap call per page lists every *verifiable* claim: routes,
   dependencies, environment variables, file paths, commands, symbols, config keys.
2. **Check** — claims go out in batches of 8. Each batch is one parent agent that spawns
   **one subagent per claim, in parallel**, and consolidates their answers. Fan-out is
   bounded on purpose: parallelism is `--verify-concurrency x 8`, a number you can
   predict, not an unbounded swarm against your rate limit.
3. **Refute** — an adversarial pass defends the documentation and tries to overturn each
   accusation. Only findings that survive are reported.

### Nothing is taken on the model's word

Every finding must carry evidence that **Python** verifies before it is reported:

- `evidence_kind: "present"` — the cited file must exist in the scan and the line must be
  within it.
- `evidence_kind: "absent"` — for the strongest class of error, an invented file, the
  evidence is the absence: the cited path must genuinely *not* exist.
- Evidence pointing anywhere inside the wiki is rejected outright. Without this a checker
  greps for `/api/users`, finds it in the page under review, and "confirms" it.

The refuter carries the same burden: an overturn without a file, a line and a verbatim
quote that resolve does not count.

What was thrown away is counted, never silent — unanswered claims and contradictions
dropped for unusable evidence both appear in the report, so a page that was half-checked
never reads as clean.

### Output

- **`08-verification/report.md`** — in the wiki, wikilinked to each page it indicts.
- **`.wiki-verify/findings.json`** — structured and diffable, for CI.

Both are **deleted when `--verify` is off**: a stale report claiming errors that were
already fixed is itself a factual error.

Findings are cached against the page text, the repository and the verify model, so a
second run with nothing changed re-verifies nothing and costs nothing.

### Cost

Measured at roughly **$0.50 per page** on sonnet. The default scope is the five analytical
pages — introduction, tech stack, architecture overview, integrations, configuration —
because that is where errors concentrate; reference and module pages are transcription.

Two ceilings, both enforced: `--verify-max-usd` per repository and `--verify-total-usd`
across the whole run. On exceed, verification stops and the report is marked **partial**,
naming the pages it never reached. `--dry-run` lists the units and prints an estimate.

### It cannot damage a good wiki

Verification runs **after** the run is committed. A rate limit during an optional advisory
step must not throw away a generation that completed perfectly, so any failure here
degrades to a warning: the wiki stays exactly as it was, and the run still exits 0.

Use `--verify-fail-on any|high` for CI, which makes surviving findings exit **4**.

| Flag | Default | Meaning |
|---|---|---|
| `--verify` | **off** | Enable the phase |
| `--verify-scope` | `analytical` | `analytical` (5 pages) or `all` |
| `--verify-model` | `sonnet` | Small models are what produce the errors |
| `--verify-concurrency` | `2` | Batches in flight; real parallelism is this x 8 |
| `--verify-max-usd` | `5.00` | Per repository (`0` disables) |
| `--verify-total-usd` | `25.00` | Across the whole run (`0` disables) |
| `--verify-timeout` | `1800` | Per call; a batch fan-out exceeds the 600s default |
| `--verify-fail-on` | `none` | `none` \| `any` \| `high` -> exit 4 |

---

## Running several clients at once

Documenting thirty repositories is not one job, it is thirty — and the expensive
mistake is redoing one that was already finished. `--multiclient` triages first,
routes what is left to a client by size, and runs the clients together.

```bash
wiki-generator --source ~/code --output ~/wikis --multiclient
```

### Triage decides what is touched

Before any model is called, every repository is classified from what is on disk:

| State | Meaning | What happens |
|---|---|---|
| **done** | every planned page exists, its fingerprint matches, no failure recorded | never touched again |
| **incomplete** | pages missing, out of date, a failure in the index, or an interrupted run | resumed |
| **untouched** | no wiki | generated from zero |
| **skipped** | nothing to document (below `--min-lines`) | left alone |

A finished wiki is judged **against whatever wrote it**, not against the client
routing would pick today — otherwise changing the routing would re-bill the
whole tree. Failures are found by reading the index for whatever the client
called its error, so `claude exited with code 1`, `Not signed in` and
`token refresh failed` all count.

**Wikis generated before this version are recognised, not redone.** Their
manifests predate the provenance field, and — more to the point — a page's
fingerprint hashes the prompt that produced it, so any wiki written before the
prompts last changed can never match today's, whatever client or language it is
checked against. Comparing them is not conservative, it is meaningless. An older
wiki is therefore judged on what can still be judged — are all the pages there,
did anything fail — and the model and language it was written with are read off
its own index for the record. Checked against a real tree of seven wikis and 258
pages: six recognised as done, one correctly flagged for two missing pages.

`--triage-only` does this and stops. No model is called either way.

Triage costs a full scan per repository, so on a large tree it is minutes of
work — and silence for minutes is indistinguishable from a hang. It reports as
it goes, and the heartbeat is the point: a single repository with tens of
thousands of files takes long enough on its own to look stuck, and only a clock
that keeps moving tells the two apart.

```
  [847/1500] payments-service  0s   done:612 untouched:198 incomplete:36
```

On a terminal that line is rewritten in place and only repositories taking over
five seconds earn a permanent line. Piped, every repository gets one line, so the
log holds the whole classification. Measured: 1500 small repositories in 46
seconds.

Past `--list-limit` (60) the per-repository listing collapses to what needs work,
capped, with the counts and the control file carrying the rest — a thousand lines
of `untouched` is not a report. One repository that cannot be classified is
recorded as `error` and the other 1499 carry on.

### Routing is by size

| Repository | Default client | Why |
|---|---|---|
| ≤ 200 files | `opencode` | small enough that a cheap or local model suffices |
| 200–2000 files | `claude` | the default |
| ≥ 2000 files | `grok` | denser output, and cheaper per page on long work |

Change the boundaries with `--small-max-files` / `--large-min-files`, and the
clients with `--client-small` / `--client-medium` / `--client-large`.

### One line per client

```
  claude -> api-gateway
  grok -> monolith
[claude   ] api-gateway   12/34 pages   4m
[grok     ] monolith       3/180 pages  9m
[opencode ] idle
  repositories: 7 done, 1 incomplete, 0 failed, 14 left of 22
```

The lanes are redrawn in place on a terminal and appended only when something
changes when the output is a file, because a log that repeats an unchanged
status every five seconds is unreadable. Each repository's full output goes to
`wiki-logs/<repo>.log`.

### The control file

`wiki-control.json` records the triage: every repository, its size, its client,
its state and the reason. It is **a record, never an input** — the next run
triages again rather than trusting it, so a wiki deleted or repaired by hand is
noticed.

> It holds the names and paths of the repositories you document. It is in
> `.gitignore` for that reason; keep it that way if your repositories are private.

---

## Choosing a client

The generator never sends a repository's contents to a model. It runs an
**agentic coding CLI** with read-only tools and lets the model open the files it
needs — which is what keeps per-page cost flat on a large repository. Any CLI
that can do four things can drive it:

1. run a single prompt and exit (headless / print mode)
2. read files itself
3. be restricted to reading only
4. report the result as JSON

```bash
wiki-generator --repo ~/code/api --client grok
```

| Client | Binary | Default model | Auth | Verified |
|---|---|---|---|---|
| `claude` *(default)* | `claude` | `haiku` | Claude Code subscription, or `--bedrock` | yes |
| `grok` | `grok` | `grok-4.6` | `grok login`, or `XAI_API_KEY` | yes |
| `opencode` | `opencode` | `anthropic/claude-haiku-4-5` | `opencode auth login` | yes |

Model aliases do not travel between clients — `haiku` means nothing to Grok — so
each client names its own default and `--model` overrides it.

### Capabilities, and what happens without them

Clients declare what they support, and features that need more than the client
offers **refuse before the run starts** rather than failing at the end:

```
Error: --verify needs JSON schemas and subagents, which the 'x' client does not support.
```

`--verify` needs JSON schemas and subagents. A client that reports no per-call
cost turns the `--verify-max-usd` dollar ceiling into a page limit (see
[Cost tracking](#cost-tracking)).

### Tool names are a safety boundary, not a preference

**An allowlist naming tools a CLI does not have restricts nothing, and fails
open.** This is measured, not theoretical. Asked to run `whoami` under two
allowlists, the same CLI:

| `--tools` | What happened |
|---|---|
| `read_file,list_dir,grep` (its real names) | *"I have no terminal tool in this session"* |
| `Read,Glob,Grep` (another CLI's names) | *"I'll run `whoami` in the terminal"* |

So tool names live in the client adapter, never in shared configuration, and
each adapter also denies its write tools by name as a second lock. Confirmed
with the generator's own argv: no terminal, no file writes, no file created.

An adapter is marked **verified** only once its flags, tool names and JSON
envelope have been confirmed against a signed-in run. An unverified one warns on
every run rather than implying a guarantee it has not earned. Both shipped
adapters are verified.

A model's account of its own tools is *not* evidence: asked what it had, this
CLI confidently reported "5 native + 525 via MCP (333 pfSense, 192 Portainer)"
while `grok mcp list` reported none configured. Only behaviour counts — ask it
to do the thing and see whether it can.

### Local models

`opencode` reaches a local Ollama with no account and no bill:

```bash
wiki-generator --repo ~/code/api --client opencode --model ollama/qwen2.5:latest
```

The generator runs with the repository as its working directory, and writing an
`opencode.json` into someone's repository to declare a provider is not
acceptable — so an `ollama/` model prefix makes the adapter declare the local
provider in the config it already imposes. Nothing is written to the repository.

Expect it to work and to be weak. On a 4-file fixture: 19 pages, no failures,
zero cost, 4m39 — against 18 seconds for `haiku`. The quality gap is not
verbosity but **verifiability**: the local model produced **no `file:line`
citations at all**, where `haiku` produced 56. A wiki with no citations cannot
be checked against the code, which is most of the point.

#### The model is checked before the run, not during it

Two failures are caught up front, because Ollama declares both facts:

```
Error: `qwen3:latest` is not installed. Install it with `ollama pull qwen3:latest`,
       or pick one that can: llama3.2:latest, qwen2.5:latest.

Error: `tinyllama:latest` cannot call tools (completion). The generator gives the
       model tools to read the repository — without them it will write pages
       about code it never opened. Pick one that can: llama3.2:latest, ...
```

The second matters more than it looks. A missing model is an opaque provider
error a few minutes in; a model **without tool calling** fails silently — the run
completes, every page is written, and the wiki describes a repository the model
was never able to open.

Interactively it offers the choice rather than the error: pull the one you asked
for, use one already installed (with its size), or name a different one to pull.
Nothing is downloaded without being asked for — a pull is gigabytes over your
connection. Scripted, it stops with the message above instead of quietly
swapping the model out from under you.

### When a client cannot be restricted

`opencode` has no tool-allowlist flag at all: permissions live in a config file,
and headless blocks on an approval prompt unless `--auto` is passed, which
approves everything not explicitly denied. The adapter writes its own config
with `bash`, `edit`, `write`, `patch` and `webfetch` denied and points
`OPENCODE_CONFIG` at it, so containment does not depend on the user's own
config — but that is configured containment, not containment enforced by argv,
and it has not been confirmed behaviourally.

So the generator **refuses to run it** unless you say otherwise:

```
Error: the 'opencode' client cannot be restricted to read-only tools from the
command line, so this run could modify /path/to/repo. Pass
--allow-unrestricted-client to accept that, ideally with the repository mounted
read-only.
```

A warning would not be enough. The Grok allowlist looked correct and failed open;
that is the evidence this refusal is built on.

`--verify` is unavailable on `opencode` — no JSON-schema output, no inline
subagent definitions — and the run stops before generating rather than after.

### Switching client mid-wiki

A page's fingerprint includes the client and the model, because a page written
by one is not interchangeable with a page written by another. Pointing a
different client at an existing wiki therefore regenerates all of it — correctly,
but silently, which reads as a broken cache. The manifest now records what wrote
the wiki, and the run says so **before the first paid call**, including under
`--dry-run`:

```
Plan: 258 model-generated pages
  ! This wiki was generated by claude/haiku; this run uses grok/grok-4.6. Every
    page will be regenerated — pages are not shared between clients or models.
    To continue the existing wiki instead, run with --client claude --model haiku.
```

### Adding one

Add a class to `wiki_generator/clients.py` with an `argv()`, a `parse()` and an
`error_from()`. Nothing else in the codebase names a binary or a flag.

---

## Running on AWS with Bedrock

By default the generator uses the Claude Code subscription already logged in on
your machine. `--bedrock` switches the model to **Amazon Bedrock**, which is what
makes it runnable on infrastructure where no one can log in interactively.

```bash
wiki-generator --source /repos --output /wikis --bedrock --aws-region us-east-1
```

Credentials are never passed by this tool. `--bedrock` sets `CLAUDE_CODE_USE_BEDROCK=1`
and the region, and leaves everything else to the AWS credential chain — the ECS
task role, the EC2 instance profile, `AWS_PROFILE`, `~/.aws`. That is the point:
on Fargate the role is resolved per call, and there is no key to leak.

### It fails before the run, not during it

A generation run is tens of minutes. Every way Bedrock can be misconfigured
surfaces on the first model call, where it is indistinguishable from a transient
error — so the region and credential chain are checked up front instead:

```
Error: Bedrock needs a region. Pass --aws-region, or set AWS_REGION.
```

A missing region is fatal. Absent credentials are only a warning, because on
EC2, ECS and EKS they legitimately do not exist until the call is made.

`--verbose` adds the caller identity (`aws sts get-caller-identity`) so a run
that reaches the wrong account says so in its first three lines.

### Throttling is retried; permission errors are not

Bedrock names its failures rather than numbering them, and it throttles harder
than the subscription. `ThrottlingException`, `ServiceUnavailableException` and
`ModelNotReadyException` are retried with backoff. `AccessDeniedException`,
`ExpiredToken`, `ValidationException` and a missing credential chain are **not** —
retrying those only burns the clock on a run that cannot succeed.

### Cost reporting differs, and the budget adapts

The subscription reports a price per call, which is what `--verify-max-usd`
counts against. Bedrock bills your account directly and may report nothing. When
no call comes back priced, the dollar ceiling would silently stop being a ceiling —
so it is converted into a page limit at the measured rate (~$0.50/page) and the
report says so. The generation summary says `not reported by Bedrock` rather than
printing `$0.00`, which would read as "it was free".

### Container and task definition

`deploy/` has a working starting point:

| File | What it is |
|---|---|
| `deploy/Dockerfile` | node + `claude` CLI + python, **running as non-root** |
| `deploy/ecs-task-definition.json` | Fargate task: repos mounted read-only, wikis on EFS |
| `deploy/task-role-policy.json` | The IAM the task role actually needs |

```bash
docker build -t wiki-generator -f deploy/Dockerfile .
```

Three details in there are load-bearing:

**The container does not run as root.** There is no terminal to answer a
permission prompt, so the generator runs with `--permission-mode bypassPermissions`,
and Claude Code refuses to bypass prompts as root. The image runs as uid 1000, so
the EFS access point must let uid 1000 write.

**Repositories are mounted read-only.** The generator only ever reads them; the
mount makes that a guarantee rather than a promise.

**`stopTimeout` is 120 seconds.** ECS sends `SIGTERM` before `SIGKILL`, and the
generator treats `SIGTERM` exactly like Ctrl-C: stop the model calls, roll the
wiki back to its previous state, exit. Too short a timeout turns a stopped task
into a half-written wiki.

Keep the output volume between runs. The incremental cache, the rollback journal
and the [cost ledger](#cost-tracking) all live beside the wiki, so a second run
regenerates only what changed — throw the volume away and every run pays for the
whole wiki again, with no record that it ever ran.

### Model IDs

`--model` is passed to the CLI unchanged, so both the aliases and full Bedrock
identifiers work:

```bash
--model haiku
--model us.anthropic.claude-haiku-4-5-20251001-v1:0
```

Which Claude models your account can invoke, and whether they need a cross-region
inference profile, is an AWS-side question — check **Bedrock → Model access** in
the region you are running in. A model you have not been granted returns
`AccessDeniedException`, which the generator reports verbatim and does not retry.

---

## Cost tracking

Every run writes what it consumed to the output volume, beside the wikis:

```
<output>/.wiki-costs/<repo>/2026-08-16T104100Z-72059.json
```

On a laptop the cost line in the terminal is enough. On ECS the process exits,
the logs roll over, and the only thing that outlives the task is the volume — so
the ledger lives there.

```json
{
  "repo": "api-gateway",
  "status": "generated",
  "provider": "bedrock",
  "model": "haiku",
  "aws_region": "us-east-1",
  "duration_s": 412.7,
  "pages": { "generated": 34, "cached": 0, "failed": 0 },
  "generation": {
    "cost_usd": null,
    "cost_reported": false,
    "calls": 34,
    "tokens": {
      "input_tokens": 25,
      "output_tokens": 41266,
      "cache_read_input_tokens": 927716,
      "cache_creation_input_tokens": 87912
    }
  },
  "verification": null,
  "total_cost_usd": null,
  "total_tokens": { "...": "..." }
}
```

**Tokens are recorded even when cost is not**, and that is the point on Bedrock:
the account is billed directly and the CLI often reports nothing. A record with
tokens can be priced afterwards from the AWS rate card; a `0.0` that actually
means "unknown" can only mislead — so an unpriced stage writes `null` with
`cost_reported: false`, and a total that mixes priced and unpriced stages is
`null` rather than a partial sum.

Every repository of a run gets its own record, including the ones that were
skipped — a `status: "skipped"` entry with its `skip_reason` answers "why did
this repo cost nothing" months later.

### Reading it back

```bash
wiki-generator --output /wikis --costs-report
```

```json
{
  "runs": 42,
  "repositories": {
    "api-gateway": { "runs": 6, "cost_usd": 3.71, "unpriced_runs": 0, "tokens": { "...": "..." } }
  },
  "total_cost_usd": 12.84,
  "unpriced_runs": 0,
  "total_tokens": { "...": "..." }
}
```

Records are **immutable, one file per run** — never appended to. Two tasks
writing the same volume at the same time is the normal case when repositories
are fanned out, and an append-and-rewrite ledger loses records to that race. A
directory of small files does not, and it aggregates with a glob.

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
| `--min-lines` | `50` | Skip repositories below this many lines of content (`0` disables) |
| `--include` / `--exclude` | — | File globs (repeatable) |

### Behaviour

| Flag | Description |
|---|---|
| `--force`, `-f` | Ignore the cache |
| `--dry-run` | Print the plan and exit |
| `--only` | Page key, prefix or type (repeatable) |
| `--no-rollback` | Keep an interrupted run's partial output instead of rolling it back |
| `--verbose`, `-v` | Also report each page as it starts |

### Provider

| Flag | Default | Description |
|---|---|---|
| `--client` | `claude` | Which agentic CLI runs the prompts (`claude`, `grok`) |
| `--bedrock` | off | Run the model on Amazon Bedrock instead of the subscription |
| `--aws-region` | `AWS_REGION` | Region for Bedrock; required with `--bedrock` |

### Cost tracking

| Flag | Description |
|---|---|
| `--costs-report` | Print the aggregated ledger for `--output` and exit |

### Verification

Off by default — see [Semantic verification](#semantic-verification---verify).

| Flag | Default | Description |
|---|---|---|
| `--verify` | off | Check the wiki's claims against the code |
| `--verify-scope` | `analytical` | `analytical` or `all` |
| `--verify-model` | `sonnet` | Model used to verify |
| `--verify-concurrency` | `2` | Claim batches in flight |
| `--verify-max-usd` | `5.00` | Budget per repository |
| `--verify-total-usd` | `25.00` | Budget for the whole run |
| `--verify-timeout` | `1800` | Per-call timeout |
| `--verify-fail-on` | `none` | Exit 4 on surviving findings |

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
6. **Semantic verification** (`verify.py`, optional) — after the run is committed, the
   wiki's claims are extracted, checked by parallel subagents and challenged by an
   adversarial pass. Every finding's evidence is validated in Python.

Zero dependencies beyond the Python standard library.

### Security

Files that look like credentials (`*service-account*.json`, `*-adminsdk-*.json`, `*.pem`,
`.env`, SSH keys, …) are **excluded from the scan by default and reported**, not silently
dropped — the generator gives a model read access to the repository and writes
documentation from it, so nothing should point at secrets. `.env.example` and friends stay
included: they are configuration documentation, not secrets.

A repository's own `CLAUDE.md` is loaded automatically by `claude -p`. For generation that
is merely a source of nondeterminism; for `--verify` it is a trust boundary, since a
`CLAUDE.md` in an untrusted repository could instruct the reviewer to find nothing. The
verification prompt therefore frames every file in the repository as **data, never
instructions**.

---

## Limitations

- The output is model-generated. It is a **useful map, not the source of truth** — the
  code is. The cartography pages are the exception: they are deterministic.
- C# namespaces, bundler aliases (`@/components`) and dependency injection do not yield
  reliable edges; they show up as unresolved rather than as invented edges.
- Go packages are directories, not files: an import links to a representative file.
- No API key required, but it consumes your Claude Code subscription quota —
  or, with `--bedrock`, your AWS account's Bedrock spend.

> The whole codebase is in English: CLI, comments, docstrings and prompts. Wiki content
> follows `--language` — page titles, index and cartography come from `i18n.py`, and the
> model renders the outline headings in the target language while leaving identifiers
> (file paths, symbols) verbatim.

---

## License

MIT
