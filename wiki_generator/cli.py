"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .assembler import assemble
from .cartography import build_graph, graph_context, write_cartography
from .claude_client import ClaudeError, ensure_cli_available
from .config import DEFAULT_MODEL, WikiConfig
from .generator import WikiGenerator
from .citations import check as check_citations, format_report
from .links import validate_and_fix
from .models import PageResult, PageSpec
from .planner import build_plan
from .journal import RunJournal
from .scanner import count_repo_files, find_repositories, scan_repo


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
    model.add_argument("--model", "-m", default=None,
                       help=f"Model to use (default: {DEFAULT_MODEL}).")
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
    structure.add_argument("--no-cartography", action="store_true",
                           help="Skip the file dependency graph.")
    structure.add_argument("--include", action="append", default=None, metavar="GLOB",
                           help="Only analyse files matching this glob (repeatable).")
    structure.add_argument("--exclude", action="append", default=None, metavar="GLOB",
                           help="Exclude files matching this glob (repeatable).")

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
    if len(repos) <= 1:
        return [config]

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
    for index, target in enumerate(targets, start=1):
        if len(targets) > 1:
            print(f"\n{'=' * 70}", flush=True)
            print(f"[{index}/{len(targets)}] {target.repo_path.name}", flush=True)
            print("=" * 70, flush=True)
        try:
            exit_code |= await run(target)
        except (ValueError, OSError) as exc:
            print(f"  ! {target.repo_path.name}: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


async def run(config: WikiConfig) -> int:
    print(f"Repository: {config.repo_path}", flush=True)
    print(f"Output:     {config.output_path}", flush=True)
    print(f"Model:      {config.model}  (concurrency {config.concurrency})", flush=True)
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
    scan = scan_repo(config)
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
    if config.dry_run:
        for spec in specs:
            print(f"  - {spec.path:<52} {spec.title}")
        if graph is not None:
            print("  - 07-cartography/file-graph.md      (deterministic)")
            print("  - 07-cartography/module-graph.md    (deterministic)")
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

    print()
    print(f"Generated: {len(report.generated)}")
    print(f"Cached:    {len(report.cached)}")
    if report.failed:
        print(f"Failed:    {len(report.failed)}", file=sys.stderr)
        for result in report.failed:
            print(f"  ! {result.spec.path}: {result.error[:200]}", file=sys.stderr)
    print(f"Time:      {report.elapsed_s:.1f}s")
    if report.total_cost_usd:
        print(f"Cost:      ${report.total_cost_usd:.4f}")
    print(f"\nWiki at: {nav_files[0]}")

    # Only now is the wiki coherent: pages, cartography, index and validation all
    # done. Committing drops the snapshot and clears the interrupted-run marker.
    journal.commit()

    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _config_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
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
        return asyncio.run(run_all(config))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except (ValueError, OSError, ClaudeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
