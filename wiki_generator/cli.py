"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import __version__
from .assembler import assemble
from .cartography import build_graph, graph_context, write_cartography
from .claude_client import ClaudeError, ClaudeRunner, ensure_cli_available
from .config import DEFAULT_MODEL, WikiConfig
from .generator import WikiGenerator, provenance_warning
from .citations import check as check_citations, format_report
from .links import validate_and_fix
from .models import PageResult, PageSpec
from .planner import build_plan
from . import (board as board_mod, clients, costs, dockerrun, ollama,
               providers, state as state_mod, wizard)
from .i18n import translator
from .journal import RunJournal
from . import verify as verify_mod
from .scanner import (
    EmptyRepositoryError,
    count_repo_files,
    find_repositories,
    scan_repo,
    substance,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wiki-generator",
        description=(
            "Generate a complete, standardized engineering wiki from a repository "
            "using Claude Code in headless mode (subscription, no API key)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  wiki-generator --source ~/code/meu-projeto\n"
            "  wiki-generator --source ~/code/meu-projeto --output ~/wikis\n"
            "      -> ~/wikis/meu-projeto/\n"
            "  wiki-generator --source ~/code --output ~/wikis\n"
            "      -> one wiki per git repository found in ~/code\n"
            "  wiki-generator --source . --only architecture --force\n"
            "  wiki-generator --source . --log-dir /tmp/wg-logs --verbose\n"
        ),
    )

    target = parser.add_argument_group("target")
    target.add_argument("--source", "-s", "--repo", "-r", dest="source", default=".",
                        help="Repository to document, or a folder containing several git "
                             "repositories (all of them are then processed).")
    target.add_argument("--output", "-o", "--out", dest="output", default=None,
                        help="Output folder. Each repository gets its wiki in "
                             "<output>/<repo-name>/. Without this option the wiki "
                             "goes to <repo>/wiki.")
    target.add_argument("--config", "-c", default=None,
                        help="JSON configuration file.")

    model = parser.add_argument_group("model")
    model.add_argument("--client", default=None,
                       choices=sorted(clients.CLIENTS),
                       help=f"Which agentic CLI runs the prompts "
                            f"(default: {clients.DEFAULT_CLIENT}).")
    model.add_argument("--model", "-m", default=None,
                       help="Model to use (default: the client's own — "
                            + ", ".join(f"{n}: {c.default_model}"
                                        for n, c in sorted(clients.CLIENTS.items()))
                            + ").")
    model.add_argument("--fallback-model", default=None,
                       help="Fallback model if the primary one is unavailable.")
    model.add_argument("--concurrency", "-j", type=int, default=None,
                       help="Pages generated in parallel (default: 4).")
    model.add_argument("--timeout", type=int, default=None,
                       help="Per-page timeout, in seconds (default: 600).")
    model.add_argument("--max-retries", type=int, default=None,
                       help="Extra attempts on transient failures (default: 2).")
    model.add_argument("--max-budget-usd", type=float, default=None,
                       help="Cost ceiling per call (only relevant with an API key).")
    model.add_argument("--permission-mode", default=None,
                       choices=["acceptEdits", "auto", "bypassPermissions", "manual",
                                "dontAsk", "plan"],
                       help="Claude Code permission mode (default: bypassPermissions).")
    model.add_argument("--claude-bin", default=None,
                       help="Claude Code binary (default: claude).")
    model.add_argument("--log-dir", default=None, metavar="DIR",
                       help="Save every Claude Code call (prompt, stdout, stderr) to "
                            "DIR/<repo>/<page>.json, for debugging.")

    content = parser.add_argument_group("content")
    content.add_argument("--language", "-l", default=None,
                         help="Wiki language: en, pt, pt-br, es, ... (default: en).")
    content.add_argument("--project-name", default=None,
                         help="Project name (default: the directory name).")
    content.add_argument("--audience", default=None, help="Target audience for the documentation.")

    structure = parser.add_argument_group("structure")
    structure.add_argument("--module-depth", type=int, default=None,
                           help="Directory depth used to group modules (default: 2).")
    structure.add_argument("--max-modules", type=int, default=None,
                           help="Maximum number of documented modules (default: 25).")
    structure.add_argument("--files-per-reference-page", type=int, default=None,
                           help="Files per reference page (default: 6).")
    structure.add_argument("--max-reference-pages", type=int, default=None,
                           help="Cap on reference pages (default: 60).")
    structure.add_argument("--no-reference", action="store_true",
                           help="Skip the low-level reference pages.")
    structure.add_argument("--single", action="store_true",
                           help="Treat the whole tree as a single repository, even if it "
                                "contains several git repos (default: one wiki per repo).")
    structure.add_argument("--min-lines", type=int, default=None, metavar="N",
                           help="Skip repositories with fewer than N lines of content "
                                "(default: 50). A wiki built from a one-line README is "
                                "pages of empty headings. Use 0 to never skip.")
    structure.add_argument("--no-cartography", action="store_true",
                           help="Skip the file dependency graph.")
    structure.add_argument("--include", action="append", default=None, metavar="GLOB",
                           help="Only analyse files matching this glob (repeatable).")
    structure.add_argument("--exclude", action="append", default=None, metavar="GLOB",
                           help="Exclude files matching this glob (repeatable).")

    behaviour_costs = parser.add_argument_group("cost tracking")
    behaviour_costs.add_argument("--costs-report", action="store_true",
                                 help="Print the aggregated cost ledger for "
                                      "--output and exit. Reads the per-run "
                                      "records left on the volume by earlier runs.")

    model.add_argument("--allow-unrestricted-client", action="store_true",
                       help="Run a client that cannot be restricted to read-only "
                            "tools from the command line. The run may modify the "
                            "repository.")

    container = parser.add_argument_group("container")
    container.add_argument("--docker", action="store_true",
                           help="Run everything inside the container image, which "
                                "already has every client CLI installed. Builds "
                                "the image first if it is not present.")
    container.add_argument("--docker-image", default=dockerrun.IMAGE, metavar="TAG",
                           help=f"Image to use (default: {dockerrun.IMAGE}).")
    container.add_argument("--docker-rebuild", action="store_true",
                           help="Rebuild the image even if it is already there.")

    behaviour_profile = parser.add_argument_group("saved runs")
    behaviour_profile.add_argument("--profile", default=None, metavar="PATH",
                                   help="Replay a run saved by the interactive "
                                        "setup. Flags given alongside it win.")

    multi = parser.add_argument_group("multiclient")
    multi.add_argument("--multiclient", action="store_true",
                       help="Route repositories to clients by size and run the "
                            "clients at the same time. Triages first, so nothing "
                            "already finished is touched again.")
    multi.add_argument("--list-limit", type=int, default=60, metavar="N",
                       help="Above this many repositories the triage lists only "
                            "the ones needing work (default: 60).")
    multi.add_argument("--triage-only", action="store_true",
                       help="Classify the repositories, write the control file "
                            "and stop. No model is called.")
    multi.add_argument("--control-file", default=None, metavar="PATH",
                       help="Where the control file lives "
                            f"(default: ./{state_mod.CONTROL_FILE}).")
    multi.add_argument("--small-max-files", type=int, default=200, metavar="N",
                       help="At or below this many files a repository is small "
                            "(default: 200).")
    multi.add_argument("--large-min-files", type=int, default=2000, metavar="N",
                       help="At or above this many files a repository is large "
                            "(default: 2000).")
    multi.add_argument("--client-small", default="opencode", metavar="CLIENT",
                       help="Client for small repositories (default: opencode).")
    multi.add_argument("--client-medium", default="claude", metavar="CLIENT",
                       help="Client for mid-sized repositories (default: claude).")
    multi.add_argument("--client-large", default="grok", metavar="CLIENT",
                       help="Client for large repositories (default: grok).")

    provider = parser.add_argument_group("provider")
    provider.add_argument("--bedrock", action="store_true",
                          help="Run the model on Amazon Bedrock instead of the "
                               "Claude subscription. Credentials come from the "
                               "usual AWS chain (task role, instance profile, "
                               "environment, ~/.aws).")
    provider.add_argument("--aws-region", default=None, metavar="REGION",
                          help="Region for Bedrock (default: AWS_REGION, then "
                               "~/.aws/config). Required with --bedrock.")

    verification = parser.add_argument_group("verification (opt-in)")
    verification.add_argument("--verify", action="store_true",
                              help="After generating, check the wiki's factual claims "
                                   "against the code using subagents. Slow and costly; "
                                   "off by default.")
    verification.add_argument("--verify-scope", default=None,
                              choices=["analytical", "all"],
                              help="Which pages to verify (default: analytical — the 5 "
                                   "pages carrying claims someone acts on).")
    verification.add_argument("--verify-model", default=None,
                              help="Model for verification (default: sonnet).")
    verification.add_argument("--verify-concurrency", type=int, default=None,
                              help="Claim batches in flight (default: 2). Real "
                                   "parallelism is this times 8 subagents.")
    verification.add_argument("--verify-max-usd", type=float, default=None,
                              help="Per-repository verification budget (default: 5.0; "
                                   "0 disables the ceiling).")
    verification.add_argument("--verify-total-usd", type=float, default=None,
                              help="Verification budget across every repository of "
                                   "the run (default: 25.0; 0 disables it). Without "
                                   "it, --source over 20 repos costs 20 times the "
                                   "per-repository ceiling.")
    verification.add_argument("--verify-timeout", type=int, default=None,
                              help="Per-call timeout for verification (default: 1800).")
    verification.add_argument("--verify-fail-on", default=None,
                              choices=["none", "any", "high"],
                              help="Exit 4 when findings survive (default: none).")

    behaviour = parser.add_argument_group("behaviour")
    behaviour.add_argument("--force", "-f", action="store_true",
                           help="Regenerate every page, ignoring the cache.")
    behaviour.add_argument("--dry-run", action="store_true",
                           help="Print the page plan and exit, without calling the model.")
    behaviour.add_argument("--only", action="append", default=None, metavar="TARGET",
                           help="Generate only these pages: key (`architecture.overview`), "
                                "prefix (`architecture`) or type (`module`). Repeatable.")
    behaviour.add_argument("--no-rollback", action="store_true",
                           help="Keep the partial output of an interrupted previous run "
                                "instead of rolling it back (default: roll back, so a "
                                "wiki is never left half-generated).")
    behaviour.add_argument("--verbose", "-v", action="store_true", help="Verbose output.")
    behaviour.add_argument("--version", action="version", version=f"wiki-generator {__version__}")

    return parser


# ----------------------------------------------------------------------
def _config_from_args(args: argparse.Namespace) -> WikiConfig:
    source = Path(args.source).expanduser().resolve()
    # `--output` is the folder hosting the wikis; each repository gets a
    # subfolder named after it. Without `--output`, the wiki stays in the repo.
    explicit_output = Path(args.output).expanduser().resolve() if args.output else None
    out = explicit_output / source.name if explicit_output else source / "wiki"

    overrides = {
        "repo_path": source,
        "output_path": out,
        "log_dir": Path(args.log_dir).expanduser().resolve() if args.log_dir else None,
        "model": args.model,
        "fallback_model": args.fallback_model,
        "concurrency": args.concurrency,
        "timeout": args.timeout,
        "max_retries": args.max_retries,
        "max_budget_usd": args.max_budget_usd,
        "permission_mode": args.permission_mode,
        "claude_bin": args.claude_bin,
        "language": args.language,
        "project_name": args.project_name,
        "audience": args.audience,
        "module_depth": args.module_depth,
        "max_modules": args.max_modules,
        "files_per_reference_page": args.files_per_reference_page,
        "max_reference_pages": args.max_reference_pages,
        "min_lines": args.min_lines,
        "aws_region": args.aws_region,
        "verify_scope": args.verify_scope,
        "verify_model": args.verify_model,
        "verify_concurrency": args.verify_concurrency,
        "verify_max_usd": args.verify_max_usd,
        "verify_total_usd": args.verify_total_usd,
        "verify_timeout": args.verify_timeout,
        "verify_fail_on": args.verify_fail_on,
        "include_globs": tuple(args.include) if args.include else None,
        "exclude_globs": tuple(args.exclude) if args.exclude else None,
        "only": tuple(args.only) if args.only else None,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}

    if args.config:
        config = WikiConfig.from_file(Path(args.config), **overrides)
    else:
        config = WikiConfig(**overrides)

    if args.no_reference:
        config.include_reference = False
    if args.client:
        config.client = args.client
    if args.model is None:
        # Model aliases do not travel between clients: "haiku" means nothing to
        # Grok. Each client names its own default.
        config.model = clients.get(config.client).default_model
    if args.bedrock:
        config.provider = providers.BEDROCK
    config.verify = args.verify
    config.force = args.force
    config.dry_run = args.dry_run
    config.verbose = args.verbose
    config.extra["cartography"] = not args.no_cartography
    config.extra["single"] = args.single
    config.extra["rollback"] = not args.no_rollback
    config.extra["output_root"] = str(explicit_output) if explicit_output else None
    return config


def _synthetic_results(paths: list[Path], config: WikiConfig) -> list[PageResult]:
    """Register the deterministic cartography pages in the wiki index."""
    meta = {
        "file-graph.md": (
            "Cartography — File Graph",
            705,
            "Full mermaid graph: which file imports which, hubs, cycles and orphans.",
        ),
        "module-graph.md": (
            "Cartography — Module Graph",
            706,
            "The same graph aggregated per module, with inter-module coupling.",
        ),
    }
    results: list[PageResult] = []
    for path in paths:
        if path.name not in meta:
            continue
        title, order, summary = meta[path.name]
        rel = str(path.relative_to(config.output_path))
        results.append(
            PageResult(
                spec=PageSpec(
                    key=f"cartography.{path.stem}",
                    path=rel,
                    title=title,
                    section="sec.cartography",
                    kind="cartography",
                    order=order,
                    prompt="",
                    summary=summary,
                ),
                status="generated",
            )
        )
    return results


# ----------------------------------------------------------------------
def _plan_targets(config: WikiConfig) -> list[WikiConfig]:
    """One wiki per repository.

    If the given path holds several git repositories, each gets its own wiki in
    `<repo>/wiki` — mixing independent projects into a single wiki produces an
    architecture, stack and glossary that describe none of them.
    """
    if config.extra.get("single"):
        return [config]

    repos = find_repositories(config.repo_path)
    if not repos:
        return [config]
    if len(repos) == 1 and repos[0] == config.repo_path:
        # The given path is itself the repository.
        return [config]
    if len(repos) == 1 and not config.extra.get("output_root"):
        # One repository inside a plain folder, and no explicit output: the wiki
        # goes in `<repo>/wiki` as usual, but it must be scoped to the repository
        # rather than to the folder that happens to contain it.
        return [replace(config, repo_path=repos[0],
                        output_path=repos[0] / "wiki", project_name=None)]

    # Smallest repositories first. A long multi-repo run is far more useful when
    # the quick wins land early: you get complete wikis to look at within minutes,
    # and if the run is cut short the casualty is the one repo that was going to
    # take longest anyway.
    repos.sort(key=lambda repo: (count_repo_files(repo), repo.name))

    output_root = config.extra.get("output_root")
    targets: list[WikiConfig] = []
    for repo in repos:
        child = replace(
            config,
            repo_path=repo,
            output_path=(
                Path(output_root) / repo.name if output_root else repo / "wiki"
            ),
            project_name=None,  # each repo uses its own name
        )
        child.extra = dict(config.extra)
        targets.append(child)
    return targets


PROGRESS_PAGE = re.compile(r"^\[(\d+)/(\d+)\]")
PROGRESS_PLAN = re.compile(r"^Plan: (\d+) model-generated")


async def _run_lane(
    client: str, queue: list, config: WikiConfig, board, log_dir: Path,
    argv_base: list[str], bedrock: bool = False, region: str = "",
) -> None:
    """One client, its repositories, one at a time.

    Each repository is a separate process. Not for isolation's sake — the
    generator prints to stdout, and `redirect_stdout` swaps it for the whole
    process, so three lanes redirecting concurrently would shred each other's
    output. A subprocess owns its own stdout; the lane reads its progress lines
    and tees the rest to a log.
    """
    for state in queue:
        board.start(client, state.name)
        log_path = log_dir / f"{state.name}.log"
        argv = argv_base + [
            "--repo", state.path,
            "--client", client,
            "--output", str(Path(state.wiki_path).parent),
        ]
        # Bedrock routes Claude Code and nothing else: handed to a grok or
        # opencode lane it is refused outright, so it travels per lane.
        if bedrock and client == "claude":
            argv += ["--bedrock"]
            if region:
                argv += ["--aws-region", region]
        pages_done = 0
        try:
            with log_path.open("w", encoding="utf-8") as log:
                process = await asyncio.create_subprocess_exec(
                    *argv, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                assert process.stdout is not None
                async for raw in process.stdout:
                    line = raw.decode("utf-8", "replace").rstrip()
                    log.write(line + "\n")
                    plan = PROGRESS_PLAN.match(line)
                    if plan:
                        board.plan(client, int(plan.group(1)))
                        continue
                    if PROGRESS_PAGE.match(line):
                        pages_done += 1
                        board.page(client)
                code = await process.wait()
        except (OSError, asyncio.CancelledError) as exc:
            board.finish(client, "failed", str(exc)[:80])
            raise
        board.finish(
            client,
            "done" if code == 0 else "incomplete",
            "" if code == 0 else f"exit {code}, see {log_path.name}",
        )


async def run_multiclient(config: WikiConfig, args) -> int:
    """Route repositories to clients by size and run the clients together."""
    routing = state_mod.Routing(
        small_max_files=args.small_max_files,
        large_min_files=args.large_min_files,
        small=args.client_small, medium=args.client_medium, large=args.client_large,
    )
    # None, not the default output path: without an explicit --output each wiki
    # lives at `<repo>/wiki`, and treating the default as a root looks for every
    # wiki in a directory that was never used. That misses existing wikis
    # entirely and reports them as untouched — a full, paid regeneration.
    explicit = config.extra.get("output_root")
    output_root = Path(explicit) if explicit else None
    repos = find_repositories(config.repo_path) or [config.repo_path]

    if config.provider == providers.BEDROCK:
        if "claude" not in {routing.small, routing.medium, routing.large}:
            print("Error: --bedrock applies to the claude client, which no tier "
                  "is routed to.", file=sys.stderr)
            return 2
        print(f"Claude lanes run on Bedrock "
              f"({providers.resolved_region(config.aws_region)}).", flush=True)

    print(f"Triaging {len(repos)} repositories (no model calls)...", flush=True)
    with board_mod.TriageProgress(len(repos)) as progress:
        states = state_mod.triage(config, repos, routing, output_root,
                                  on_event=progress)
    control = Path(args.control_file or Path.cwd() / state_mod.CONTROL_FILE)
    state_mod.save(control, states, routing, config, config.repo_path, output_root)

    counts = state_mod.totals(states)
    print(f"  done {counts['done']} | incomplete {counts['incomplete']} | "
          f"untouched {counts['untouched']} | skipped {counts['skipped']}"
          + (f" | error {counts['error']}" if counts.get("error") else ""), flush=True)
    mark = {"done": "=", "incomplete": "~", "untouched": "+",
            "skipped": "-", "error": "!"}
    if counts["pending_by_client"]:
        print("  pending: " + ", ".join(
            f"{name} {n}" for name, n in sorted(counts["pending_by_client"].items())
        ), flush=True)

    # A per-repository listing is useful for a handful and unreadable for a
    # thousand. Past the limit only what needs work is listed, and only the
    # first few of those — the counts above already carry the scale.
    listed = states if len(states) <= args.list_limit else [
        s for s in states if s.needs_work or s.state == state_mod.ERROR
    ]
    hidden = 0
    if len(listed) > args.list_limit:
        hidden = len(listed) - args.list_limit
        listed = listed[:args.list_limit]
    for entry in listed:
        print(f"  {mark.get(entry.state, '?')} {entry.name:<28} "
              f"{entry.files:>6} files  {entry.client:<9} {entry.reason}", flush=True)
    if hidden:
        print(f"  ... and {hidden} more needing work "
              f"(--list-limit to see them, or read {control.name})", flush=True)
    print(f"  Control file: {control}", flush=True)

    pending = [s for s in states if s.needs_work]
    if not pending:
        print("\nNothing to do: every repository is complete.", flush=True)
        return 0
    if args.triage_only:
        return 0

    # Smallest first within each lane, so complete wikis appear early.
    lanes: dict[str, list] = {}
    for entry in sorted(pending, key=lambda s: (s.files, s.name)):
        lanes.setdefault(entry.client, []).append(entry)

    log_dir = control.parent / "wiki-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    board = board_mod.Board(sorted(lanes), total_repos=len(pending))

    argv_base = [sys.executable, "-m", "wiki_generator",
                 "--min-lines", str(config.min_lines),
                 "--concurrency", str(config.concurrency),
                 "--language", config.language]
    if config.verify:
        argv_base.append("--verify")
    if args.allow_unrestricted_client:
        argv_base.append("--allow-unrestricted-client")

    print(f"\n{len(pending)} repositories across {len(lanes)} clients:", flush=True)
    for name, queue in sorted(lanes.items()):
        print(f"  {name:<9} {len(queue)} repositories", flush=True)
    print(flush=True)

    async def ticker() -> None:
        try:
            while True:
                await asyncio.sleep(5)
                board.render()
        except asyncio.CancelledError:
            return

    tick = asyncio.create_task(ticker())
    try:
        await asyncio.gather(*(
            _run_lane(name, queue, config, board, log_dir, argv_base,
                      bedrock=config.provider == providers.BEDROCK,
                      region=providers.resolved_region(config.aws_region) or "")
            for name, queue in sorted(lanes.items())
        ))
    finally:
        tick.cancel()
        board.stop()

    # Re-triage: the control file must describe what is true now, not what was
    # true before the run.
    states = state_mod.triage(config, repos, routing, output_root)
    state_mod.save(control, states, routing, config, config.repo_path, output_root)
    counts = state_mod.totals(states)
    print(f"\n{board.summary()}", flush=True)
    print(f"  after re-triage: done {counts['done']} | incomplete "
          f"{counts['incomplete']} | untouched {counts['untouched']}", flush=True)
    return 0 if counts["incomplete"] == 0 and counts["untouched"] == 0 else 1


async def run_all(config: WikiConfig) -> int:
    targets = _plan_targets(config)

    if len(targets) > 1:
        print(f"Found {len(targets)} git repositories in {config.repo_path}:", flush=True)
        for target in targets:
            count = count_repo_files(target.repo_path)
            print(
                f"  - {target.repo_path.name:<24} {count:>6} files -> {target.output_path}",
                flush=True,
            )
        print("Smallest first, so complete wikis appear early.", flush=True)
        print("One wiki per repository. Use --single to generate a single one.\n", flush=True)

    exit_code = 0
    skipped: list[tuple[str, str]] = []
    # Shared across repositories: the per-repository ceiling alone lets a --source
    # run over 20 repos spend 20 times what the user thought they capped.
    spend = {"verify": 0.0}
    for index, target in enumerate(targets, start=1):
        if len(targets) > 1:
            print(f"\n{'=' * 70}", flush=True)
            print(f"[{index}/{len(targets)}] {target.repo_path.name}", flush=True)
            print("=" * 70, flush=True)
        try:
            exit_code = max(exit_code, await run(target, skipped, spend))
        except (ValueError, OSError) as exc:
            print(f"  ! {target.repo_path.name}: {exc}", file=sys.stderr)
            exit_code = 1

    if skipped:
        print(f"\n{'=' * 70}", flush=True)
        print(f"Skipped {len(skipped)} of {len(targets)} repositories:", flush=True)
        for name, reason in skipped:
            print(f"  - {name}: {reason}", flush=True)
    return exit_code


async def run(
    config: WikiConfig, skipped: list | None = None, spend: dict | None = None
) -> int:
    skipped = skipped if skipped is not None else []
    spend = spend if spend is not None else {"verify": 0.0}
    started = time.monotonic()
    record = costs.RunRecord(
        repo=config.repo_path.name,
        repo_path=str(config.repo_path),
        wiki_path=str(config.output_path),
        status="failed",  # replaced on every path that completes
        client=config.client,
        provider=config.provider,
        model=config.model,
        aws_region=(providers.resolved_region(config.aws_region)
                    if config.provider == providers.BEDROCK else None),
    )
    print(f"Repository: {config.repo_path}", flush=True)
    print(f"Output:     {config.output_path}", flush=True)
    where = (
        f"  via Bedrock ({providers.resolved_region(config.aws_region)})"
        if config.provider == providers.BEDROCK else ""
    )
    print(f"Model:      {config.model} via {config.client}  "
          f"(concurrency {config.concurrency}){where}", flush=True)
    print()

    # A marker left behind means the previous run died mid-way. Restore the wiki
    # to what it was before that run, so generation always starts from a coherent
    # state rather than compounding a half-written one.
    journal = RunJournal(config.output_path)
    pending = journal.pending()
    if pending:
        if config.extra.get("rollback", True):
            recovery = journal.recover()
            detail = (
                f"{recovery.restored_files} files restored, "
                f"{recovery.removed_files} removed"
                if recovery.wiki_existed
                else "the wiki did not exist before, so it was removed"
            )
            print(
                f"! Previous run (started {pending.get('started_at', '?')}) did not "
                f"finish. Rolled back: {detail}.",
                flush=True,
            )
        else:
            print(
                f"! Previous run (started {pending.get('started_at', '?')}) did not "
                "finish. Keeping the partial output (--no-rollback).",
                flush=True,
            )

    print("Scanning repository...", flush=True)
    try:
        scan = scan_repo(config)
    except EmptyRepositoryError as exc:
        # Not a failure: there is genuinely nothing to document.
        print(f"  Skipped: {exc}", flush=True)
        skipped.append((config.repo_path.name, str(exc)))
        _record_cost(config, record, "skipped", started, skip_reason=str(exc))
        return 0
    print(
        f"  {len(scan.files)} files | {len(scan.source_files)} source | "
        f"{scan.total_lines} lines | {len(scan.modules)} modules"
    )
    if scan.sensitive_skipped:
        print(
            f"  ! {len(scan.sensitive_skipped)} credential-looking files excluded "
            "from the scan:"
        )
        for path in scan.sensitive_skipped[:10]:
            print(f"      {path}")
        if len(scan.sensitive_skipped) > 10:
            print(f"      ... (+{len(scan.sensitive_skipped) - 10})")

    lines, files = substance(scan)
    if config.min_lines > 0 and lines < config.min_lines:
        reason = (
            f"only {lines} line(s) across {files} file(s), "
            f"below the --min-lines threshold of {config.min_lines}"
        )
        print(f"  Skipped: {reason}", flush=True)
        skipped.append((config.repo_path.name, reason))
        _record_cost(config, record, "skipped", started, skip_reason=reason)
        return 0

    graph = None
    graph_ctx = ""
    if config.extra.get("cartography", True):
        print("Building the dependency graph (code cartography)...", flush=True)
        graph = build_graph(scan, config)
        graph_ctx = graph_context(graph)
        print(
            f"  {len(graph.nodes)} nodes | {len(graph.edges)} edges | "
            f"{len(graph.cycles(limit=100))} cycles | "
            f"{len(graph.orphans())} orphan files"
        )

    specs = build_plan(scan, config, graph_ctx)
    if not specs:
        print("No pages to generate (check --only).", file=sys.stderr)
        return 1

    noun = "page" if len(specs) == 1 else "pages"
    print(f"\nPlan: {len(specs)} model-generated {noun}", flush=True)
    # Before the first paid call, and before --dry-run returns: this is where
    # the decision to spend is made.
    provenance = provenance_warning(config)
    if provenance:
        print(f"  ! {provenance}", file=sys.stderr, flush=True)
    if config.dry_run:
        for spec in specs:
            print(f"  - {spec.path:<52} {spec.title}")
        if graph is not None:
            print("  - 07-cartography/file-graph.md      (deterministic)")
            print("  - 07-cartography/module-graph.md    (deterministic)")
        if config.verify:
            wanted = (set(verify_mod.ANALYTICAL_KEYS)
                      if config.verify_scope == "analytical" else None)
            units = [s for s in specs
                     if (wanted is None or s.key in wanted) and s.kind != "cartography"]
            print(f"\nVerification: {len(units)} page(s), "
                  f"~${len(units) * verify_mod.COST_PER_PAGE_USD:.2f} estimated "
                  f"(measured at ~${verify_mod.COST_PER_PAGE_USD:.2f}/page on sonnet)")
            for spec in units:
                print(f"  - {spec.path}")
        return 0

    config.output_path.mkdir(parents=True, exist_ok=True)

    generator = WikiGenerator(config, scan)
    journal.begin({"model": config.model, "language": config.language,
                   "pages": len(specs), "repo": config.repo_path.name})
    try:
        report = await generator.generate(specs)
    except BaseException:
        # Includes KeyboardInterrupt: an interrupted run must not leave a wiki
        # whose index, cartography and pages disagree with each other.
        recovery = journal.abort()
        if recovery.recovered:
            print(
                f"\nRun interrupted — wiki rolled back to its previous state "
                f"({recovery.restored_files} files restored, "
                f"{recovery.removed_files} removed).",
                file=sys.stderr,
            )
        raise

    # The index must list the whole wiki, not just what this run generated:
    # with --only, building it from the subset would drop every other page.
    results = list(report.results)
    if config.only:
        generated_keys = {r.spec.key for r in results}
        for spec in build_plan(scan, replace(config, only=()), graph_ctx):
            if spec.key not in generated_keys and (config.output_path / spec.path).is_file():
                results.append(PageResult(spec=spec, status="cached"))

    if graph is not None:
        written = write_cartography(graph, config)
        results += _synthetic_results(written, config)
        print(f"\nCartography written: {len(written)} files in 07-cartography/")

    nav_files = assemble(config, scan, results)

    # A broken wikilink points at a note that will never exist: it is degraded
    # to plain text, and what was degraded is reported.
    link_report = validate_and_fix(config.output_path)
    if link_report["broken"]:
        print(
            f"\nLinks: {link_report['checked']} checked, "
            f"{link_report['broken']} with no target -> converted to plain text",
            flush=True,
        )
        for src, target in link_report["details"][:8]:
            print(f"  ~ {src}: [[{target}]]")
        if link_report["broken"] > 8:
            print(f"  ... (+{link_report['broken'] - 8})")

    # Re-check reading from disk: catches the case where something wrote over
    # the pages after validation. Silence here is the only proof the wiki is clean.
    recheck = validate_and_fix(config.output_path, fix=False)
    if recheck["broken"]:
        print(
            f"  ! {recheck['broken']} links still have no target after the fix "
            "— something wrote to the wiki after validation",
            file=sys.stderr,
        )

    # `file:line` citations are the error class that survives a strong model;
    # part of it is mechanically detectable.
    citation_report = check_citations(
        config.output_path, config.repo_path,
        repo_files=[f.rel_path for f in scan.files],
    )
    if citation_report["invalid"]:
        print("\n" + format_report(citation_report), file=sys.stderr)

    print(flush=True)
    print(f"Generated: {len(report.generated)}", flush=True)
    print(f"Cached:    {len(report.cached)}", flush=True)
    if report.failed:
        print(f"Failed:    {len(report.failed)}", file=sys.stderr, flush=True)
        # Grouped by error: a run almost always fails for one reason, and printing
        # it once per page buries the one line the user has to act on.
        groups: dict[str, list[PageResult]] = {}
        for result in report.failed:
            groups.setdefault(result.error[:300], []).append(result)
        for error, pages in groups.items():
            print(f"  ! {error}", file=sys.stderr)
            for result in pages[:5]:
                print(f"      {result.spec.path}", file=sys.stderr)
            if len(pages) > 5:
                print(f"      ... and {len(pages) - 5} more", file=sys.stderr)
        stale = [r for r in report.failed if not r.previous_is_current]
        if stale:
            # The index says this too, but nobody opens the index to fix a run.
            print(
                f"  {len(stale)} page(s) need another run. Repeat the same command "
                "without --force: only what is missing or out of date is "
                "regenerated.",
                file=sys.stderr, flush=True,
            )
        else:
            print("  Every failed page already had an up-to-date version on disk.",
                  file=sys.stderr, flush=True)
    print(f"Time:      {report.elapsed_s:.1f}s", flush=True)
    if report.total_cost_usd:
        label = "Cost:" if not config.verify else "Generation:"
        print(f"{label:<11}${report.total_cost_usd:.4f}", flush=True)
    elif report.generated and config.provider == providers.BEDROCK:
        # Silence here would read as "it was free". Bedrock bills the account
        # directly; the CLI just isn't the thing that knows the price.
        print("Cost:      not reported by Bedrock — see AWS Cost Explorer",
              flush=True)
    print(f"\nWiki at: {nav_files[0]}")

    # Only now is the wiki coherent: pages, cartography, index and validation all
    # done. Committing drops the snapshot and clears the interrupted-run marker.
    journal.commit()

    # Verification runs AFTER the commit, on purpose. It is the longest, least
    # reliable stage; inside the transaction a rate limit on an optional advisory
    # check would discard a generation that completed perfectly. An interrupted
    # verification should lose the verification, not the wiki.
    record.pages_generated = len(report.generated)
    record.pages_cached = len(report.cached)
    record.pages_failed = len(report.failed)
    record.generation = costs.Stage(
        cost_usd=report.total_cost_usd,
        cost_reported=report.cost_reported,
        calls=report.calls,
        usage=report.usage,
    )

    exit_code = 1 if report.failed else 0
    if config.verify:
        try:
            verdict = await _verify_phase(
                config, scan, results, spend, report.total_cost_usd, record
            )
            exit_code = max(exit_code, verdict)
        except Exception as exc:  # noqa: BLE001 - advisory step must never be fatal
            print(f"\nVerification failed ({exc}). The wiki is unaffected.",
                  file=sys.stderr)
    else:
        # A stale report claiming errors that were already fixed is itself a
        # factual error — the exact failure this feature exists to prevent.
        verify_mod.clear_artifacts(config.output_path)

    _record_cost(
        config, record,
        "failed" if report.failed else ("cached" if not report.generated else "generated"),
        started,
    )
    return exit_code


def _record_cost(
    config: WikiConfig, record: costs.RunRecord, status: str, started: float,
    skip_reason: str = "",
) -> None:
    """Write the run's ledger entry. A failure here is reported, never fatal."""
    record.status = status
    record.skip_reason = skip_reason
    record.duration_s = time.monotonic() - started
    record.finished_at = costs._now()
    # The ledger belongs to the volume, not to one repository's wiki. With an
    # explicit --output that is the root the user named, so every repository's
    # records sit together and aggregate with one glob. Without it the wiki
    # lives inside the repository, and so does its ledger — writing anywhere
    # else would put accounting into a directory the user never nominated.
    root = Path(config.extra.get("output_root") or config.output_path)
    try:
        target = costs.write(record, root)
    except costs.CostWriteError as exc:
        print(f"  ! Could not write the cost record: {exc}", file=sys.stderr)
        return
    if config.verbose:
        print(f"  Cost record: {target}", flush=True)


async def _verify_phase(
    config: WikiConfig, scan, results: list[PageResult], spend: dict,
    generation_cost: float = 0.0, record: costs.RunRecord | None = None,
) -> int:
    """Check the wiki's claims against the code. Returns the exit code contribution."""
    wanted = (
        set(verify_mod.ANALYTICAL_KEYS)
        if config.verify_scope == "analytical"
        else None
    )
    pages: list[tuple[str, str, str]] = []
    for result in results:
        if wanted is not None and result.spec.key not in wanted:
            continue
        if result.spec.kind == "cartography":
            continue
        # --only runs hand back PageResults with empty markdown; the file on disk
        # is the only reliable source of page text.
        page_file = config.output_path / result.spec.path
        if not page_file.is_file():
            continue
        pages.append((result.spec.path, result.spec.title, page_file.read_text("utf-8")))

    if not pages:
        print("\nVerification: no pages in scope.", flush=True)
        return 0

    total_cap = config.verify_total_usd
    if total_cap > 0 and spend["verify"] >= total_cap:
        print(f"\nVerification skipped: the run has spent "
              f"${spend['verify']:.2f} of its ${total_cap:.2f} total budget.",
              file=sys.stderr, flush=True)
        return 0
    budget = config.verify_max_usd
    if total_cap > 0:
        remaining = total_cap - spend["verify"]
        budget = min(budget, remaining) if budget > 0 else remaining
    config = replace(config, verify_max_usd=budget)

    print(f"\nVerifying {len(pages)} page(s) with {config.verify_model} "
          f"(budget ${config.verify_max_usd:.2f})...", flush=True)
    runner = ClaudeRunner(config)
    verdict = await verify_mod.run_verification(config, scan, runner, pages)
    spend["verify"] += verdict.cost_usd
    if record is not None:
        record.verification = costs.Stage(
            cost_usd=verdict.cost_usd,
            cost_reported=verdict.cost_reported,
            calls=verdict.calls,
            usage=verdict.usage,
        )

    t = translator(config.language)
    report_path = verify_mod.write_report(config, verdict, t)
    results.append(
        PageResult(
            spec=PageSpec(
                key="verification.report",
                path=str(report_path.relative_to(config.output_path)),
                title=t("verify.title"),
                section="sec.verification",
                kind="verification",
                order=810,
                prompt="",
                summary=t("verify.m.findings") + f": {len(verdict.findings)}",
            ),
            status="generated",
        )
    )
    assemble(config, scan, results)  # idempotent: rebuilds README/SUMMARY only

    print(f"  {verdict.pages_verified} verified, {verdict.pages_cached} cached | "
          f"{verdict.claims_checked} claim(s) | {len(verdict.findings)} finding(s), "
          f"{len(verdict.overturned)} overturned | "
          + (f"${verdict.cost_usd:.2f}" if verdict.cost_reported else "cost not reported"),
          flush=True)
    if verdict.pages_skipped:
        print(f"  {len(verdict.pages_skipped)} page(s) not verified: "
              + "; ".join(verdict.pages_skipped[:3])
              + ("; ..." if len(verdict.pages_skipped) > 3 else ""),
              file=sys.stderr, flush=True)
    if verdict.claims_unanswered or verdict.evidence_rejected:
        print(f"  {verdict.claims_unanswered} claim(s) unanswered, "
              f"{verdict.evidence_rejected} contradiction(s) dropped for unusable "
              "evidence", file=sys.stderr, flush=True)
    # Printed here and not in the summary above: verification is often the more
    # expensive half, and the summary is written before it runs.
    if verdict.cost_reported:
        print(f"  Total for this repository: "
              f"${generation_cost + verdict.cost_usd:.4f}", flush=True)
    else:
        print("  This backend does not price its calls, so the budget was "
              f"enforced as a page limit instead (~${verify_mod.COST_PER_PAGE_USD:.2f}"
              "/page assumed).", file=sys.stderr, flush=True)
    for finding in verdict.findings[:5]:
        print(f"  ! [{finding.severity}] {finding.page}: {finding.claim[:90]}")
    if len(verdict.findings) > 5:
        print(f"  ... (+{len(verdict.findings) - 5}) see {report_path.name}")

    if config.verify_fail_on == "any" and verdict.findings:
        return 4
    if config.verify_fail_on == "high" and verdict.has_high:
        return 4
    return 0


def _run_in_docker(args, raw: list[str]) -> int:
    """Hand the whole run to the container image and wait for it."""
    ok, detail = dockerrun.available()
    if not ok:
        print(f"Error: {detail}", file=sys.stderr)
        return 2

    if args.docker_rebuild or not dockerrun.image_exists(args.docker_image):
        why = "rebuilding" if args.docker_rebuild else "not present, building"
        print(f"Image {args.docker_image} {why}. This takes a few minutes.",
              flush=True)
        try:
            dockerrun.build(args.docker_image,
                            on_line=lambda line: print(f"  {line}", flush=True))
        except dockerrun.DockerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    # The container gets everything except the flags that put it there.
    inner = [a for a in raw if a not in {"--docker", "--docker-rebuild"}]
    for flag in ("--docker-image",):
        if flag in inner:
            index = inner.index(flag)
            del inner[index:index + 2]

    try:
        plan = dockerrun.plan(inner, tag=args.docker_image,
                              interactive=sys.stdout.isatty())
    except dockerrun.DockerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    for host, inner_path, mode in plan.mounts:
        print(f"  mount {host} -> {inner_path} ({mode})", flush=True)
    for missing in plan.missing_credentials:
        print(f"  ! no credentials to mount for {missing}", file=sys.stderr)
    print(flush=True)

    try:
        return subprocess.call(plan.argv)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # No arguments and someone at the keyboard: ask instead of printing help.
    # Piped or scripted, --help is still the right answer.
    if not raw:
        if not wizard.should_offer():
            build_parser().print_help()
            return 0
        try:
            # A profile saved on an earlier run is offered before the questions
            # are asked again — "save this to reuse later" has to mean it.
            saved = wizard.find_profiles()
            raw = (wizard.offer_saved(saved) if saved else None) or wizard.run()
        except wizard.Cancelled:
            print("\nStopped.", file=sys.stderr)
            return 130

    args = build_parser().parse_args(raw)
    if args.docker:
        return _run_in_docker(args, raw)
    if args.profile:
        try:
            saved = wizard.load_profile(Path(args.profile))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        # Flags given alongside --profile win, so a saved run can be adjusted
        # without editing the file.
        args = build_parser().parse_args(saved + [a for a in raw
                                                  if a not in {"--profile", args.profile}])
    try:
        config = _config_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.costs_report:
        root = Path(config.extra.get("output_root") or config.output_path)
        summary = costs.summarize(root)
        if not summary["runs"]:
            print(f"No cost records under {root / costs.COSTS_DIR}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    try:
        client = clients.get(config.client)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if (config.provider == providers.BEDROCK and config.client != "claude"
            and not (args.multiclient or args.triage_only)):
        print(f"Error: --bedrock routes Claude Code through Amazon Bedrock; the "
              f"'{config.client}' client authenticates on its own.", file=sys.stderr)
        return 2

    missing = [
        need for need, ok in (
            ("JSON schemas", client.capabilities.json_schema),
            ("subagents", client.capabilities.subagents),
        ) if not ok
    ]
    if config.verify and missing:
        # Better to say so now than to spend the generation and fail at the
        # verification step, which runs last.
        print(f"Error: --verify needs {' and '.join(missing)}, which the "
              f"'{config.client}' client does not support.", file=sys.stderr)
        return 2

    if not client.capabilities.tool_restriction and not args.allow_unrestricted_client:
        # The generator's promise is that it only reads the repository. A client
        # with no allowlist cannot make that promise from argv, and a warning is
        # demonstrably not enough — the Grok allowlist was found to fail open
        # while looking correct. Opting in is explicit.
        print(f"Error: the '{config.client}' client cannot be restricted to "
              "read-only tools from the command line, so this run could modify "
              f"{config.repo_path}. Pass --allow-unrestricted-client to accept "
              "that, ideally with the repository mounted read-only.",
              file=sys.stderr)
        return 2

    if ollama.is_local(config.model):
        # A missing local model is an opaque provider error minutes into the
        # run; one without tool support is worse, because the run succeeds and
        # the wiki has no citations in it.
        try:
            config.model = providers.ollama_preflight(
                config.model, interactive=wizard.should_offer()
            )
        except providers.ProviderError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    for warning in client.warnings():
        print(f"  ! {warning}", file=sys.stderr)

    try:
        # Before the CLI check, because a missing region is a configuration
        # mistake whether or not the binary is installed.
        for warning in providers.preflight(
            config.provider, config.aws_region, config.verbose
        ):
            print(f"  ! {warning}", file=sys.stderr)
    except providers.ProviderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not config.dry_run:
        try:
            ensure_cli_available(config)
        except ClaudeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    # `kill <pid>` should unwind exactly like Ctrl-C: stop the children and roll
    # back. Without this, SIGTERM kills the process outright and leaves the
    # interrupted-run marker for the next run to clean up.
    def _on_terminate(signum, frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _on_terminate)
    except (ValueError, OSError):
        pass  # not the main thread, or a platform without SIGTERM

    try:
        if args.multiclient or args.triage_only:
            return asyncio.run(run_multiclient(config, args))
        return asyncio.run(run_all(config))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except (ValueError, OSError, ClaudeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
