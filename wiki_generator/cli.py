"""Interface de linha de comandos."""

from __future__ import annotations

import argparse
import asyncio
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
from .scanner import find_repositories, scan_repo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wiki-generator",
        description=(
            "Gera uma wiki completa e padronizada de um repositorio usando o "
            "Claude Code em modo headless (subscricao, sem API key)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  wiki-generator --source ~/code/meu-projeto\n"
            "  wiki-generator --source ~/code/meu-projeto --output ~/wikis\n"
            "      -> ~/wikis/meu-projeto/\n"
            "  wiki-generator --source ~/code --output ~/wikis\n"
            "      -> uma wiki por repositorio git encontrado em ~/code\n"
            "  wiki-generator --source . --only architecture --force\n"
            "  wiki-generator --source . --log-dir /tmp/wg-logs --verbose\n"
        ),
    )

    target = parser.add_argument_group("alvo")
    target.add_argument("--source", "-s", "--repo", "-r", dest="source", default=".",
                        help="Repositorio a documentar, ou uma pasta que contenha varios "
                             "repositorios git (nesse caso sao todos processados).")
    target.add_argument("--output", "-o", "--out", dest="output", default=None,
                        help="Pasta de saida. Cada repositorio recebe a sua wiki em "
                             "<output>/<nome-do-repo>/. Sem esta opcao, a wiki vai "
                             "para <repo>/wiki.")
    target.add_argument("--config", "-c", default=None,
                        help="Ficheiro JSON de configuracao.")

    model = parser.add_argument_group("modelo")
    model.add_argument("--model", "-m", default=None,
                       help=f"Modelo a usar (default: {DEFAULT_MODEL}).")
    model.add_argument("--fallback-model", default=None,
                       help="Modelo de recurso se o principal estiver indisponivel.")
    model.add_argument("--concurrency", "-j", type=int, default=None,
                       help="Paginas geradas em paralelo (default: 4).")
    model.add_argument("--timeout", type=int, default=None,
                       help="Timeout por pagina, em segundos (default: 600).")
    model.add_argument("--max-retries", type=int, default=None,
                       help="Tentativas extra em falhas transitorias (default: 2).")
    model.add_argument("--max-budget-usd", type=float, default=None,
                       help="Tecto de custo por chamada (so relevante com API key).")
    model.add_argument("--permission-mode", default=None,
                       choices=["acceptEdits", "auto", "bypassPermissions", "manual",
                                "dontAsk", "plan"],
                       help="Modo de permissoes do CLI (default: bypassPermissions).")
    model.add_argument("--claude-bin", default=None,
                       help="Binario do Claude Code (default: claude).")
    model.add_argument("--log-dir", default=None, metavar="DIR",
                       help="Guardar as chamadas ao Claude Code (prompt, stdout, stderr) "
                            "em DIR/<repo>/<pagina>.json, para debug.")

    content = parser.add_argument_group("conteudo")
    content.add_argument("--language", "-l", default=None,
                         help="Idioma da wiki: en, pt, pt-br, es, ... (default: en).")
    content.add_argument("--project-name", default=None,
                         help="Nome do projeto (default: nome do diretorio).")
    content.add_argument("--audience", default=None, help="Publico-alvo da documentacao.")

    structure = parser.add_argument_group("estrutura")
    structure.add_argument("--module-depth", type=int, default=None,
                           help="Profundidade de diretorios usada para agrupar modulos (default: 2).")
    structure.add_argument("--max-modules", type=int, default=None,
                           help="Numero maximo de modulos documentados (default: 25).")
    structure.add_argument("--files-per-reference-page", type=int, default=None,
                           help="Ficheiros por pagina de referencia (default: 6).")
    structure.add_argument("--max-reference-pages", type=int, default=None,
                           help="Tecto de paginas de referencia (default: 60).")
    structure.add_argument("--no-reference", action="store_true",
                           help="Nao gerar as paginas de referencia de baixo nivel.")
    structure.add_argument("--single", action="store_true",
                           help="Tratar a arvore toda como um so repositorio, mesmo que "
                                "contenha varios repos git (por omissao gera uma wiki por repo).")
    structure.add_argument("--no-cartography", action="store_true",
                           help="Nao gerar o grafo de dependencias entre ficheiros.")
    structure.add_argument("--include", action="append", default=None, metavar="GLOB",
                           help="So analisar ficheiros que correspondam (repetivel).")
    structure.add_argument("--exclude", action="append", default=None, metavar="GLOB",
                           help="Excluir ficheiros que correspondam (repetivel).")

    behaviour = parser.add_argument_group("comportamento")
    behaviour.add_argument("--force", "-f", action="store_true",
                           help="Regerar todas as paginas, ignorando a cache.")
    behaviour.add_argument("--dry-run", action="store_true",
                           help="Mostrar o plano de paginas e sair, sem chamar o modelo.")
    behaviour.add_argument("--only", action="append", default=None, metavar="ALVO",
                           help="Gerar so estas paginas: chave (`architecture.overview`), "
                                "prefixo (`architecture`) ou tipo (`module`). Repetivel.")
    behaviour.add_argument("--verbose", "-v", action="store_true", help="Saida detalhada.")
    behaviour.add_argument("--version", action="version", version=f"wiki-generator {__version__}")

    return parser


# ----------------------------------------------------------------------
def _config_from_args(args: argparse.Namespace) -> WikiConfig:
    source = Path(args.source).expanduser().resolve()
    # `--output` e a pasta que aloja as wikis; cada repositorio recebe uma
    # subpasta com o seu nome. Sem `--output`, mantem-se a wiki dentro do repo.
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
    config.extra["output_root"] = str(explicit_output) if explicit_output else None
    return config


def _synthetic_results(paths: list[Path], config: WikiConfig) -> list[PageResult]:
    """Regista as paginas deterministicas de cartografia no indice da wiki."""
    meta = {
        "file-graph.md": (
            "Cartografia — Grafo de Ficheiros",
            705,
            "Grafo mermaid completo: que ficheiro importa qual, hubs, ciclos e orfaos.",
        ),
        "module-graph.md": (
            "Cartografia — Grafo de Modulos",
            706,
            "Mesmo grafo agregado por modulo, com o acoplamento entre modulos.",
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
                    section="7. Cartografia do Codigo",
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
    """Uma wiki por repositorio.

    Se o caminho indicado agregar varios repositorios git, cada um recebe a sua
    propria wiki em `<repo>/wiki` — misturar projetos independentes numa so wiki
    produz arquitetura, stack e glossario que nao descrevem nenhum deles.
    """
    if config.extra.get("single"):
        return [config]

    repos = find_repositories(config.repo_path)
    if len(repos) <= 1:
        return [config]

    output_root = config.extra.get("output_root")
    targets: list[WikiConfig] = []
    for repo in repos:
        child = replace(
            config,
            repo_path=repo,
            output_path=(
                Path(output_root) / repo.name if output_root else repo / "wiki"
            ),
            project_name=None,  # cada repo usa o seu proprio nome
        )
        child.extra = dict(config.extra)
        targets.append(child)
    return targets


async def run_all(config: WikiConfig) -> int:
    targets = _plan_targets(config)

    if len(targets) > 1:
        print(f"Detetados {len(targets)} repositorios git em {config.repo_path}:", flush=True)
        for target in targets:
            print(f"  - {target.repo_path.name:<24} -> {target.output_path}", flush=True)
        print("Uma wiki por repositorio. Usa --single para gerar uma so.\n", flush=True)

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
    print(f"Repositorio: {config.repo_path}", flush=True)
    print(f"Saida:       {config.output_path}", flush=True)
    print(f"Modelo:      {config.model}  (concorrencia {config.concurrency})", flush=True)
    print()

    print("A analisar o repositorio...", flush=True)
    scan = scan_repo(config)
    print(
        f"  {len(scan.files)} ficheiros | {len(scan.source_files)} de codigo | "
        f"{scan.total_lines} linhas | {len(scan.modules)} modulos"
    )
    if scan.sensitive_skipped:
        print(
            f"  ! {len(scan.sensitive_skipped)} ficheiros com aspeto de credenciais "
            "excluidos do scan:"
        )
        for path in scan.sensitive_skipped[:10]:
            print(f"      {path}")
        if len(scan.sensitive_skipped) > 10:
            print(f"      ... (+{len(scan.sensitive_skipped) - 10})")

    graph = None
    graph_ctx = ""
    if config.extra.get("cartography", True):
        print("A construir o grafo de dependencias (code cartography)...", flush=True)
        graph = build_graph(scan, config)
        graph_ctx = graph_context(graph)
        print(
            f"  {len(graph.nodes)} nos | {len(graph.edges)} ligacoes | "
            f"{len(graph.cycles(limit=100))} ciclos | "
            f"{len(graph.orphans())} ficheiros isolados"
        )

    specs = build_plan(scan, config, graph_ctx)
    if not specs:
        print("Nenhuma pagina a gerar (verifica --only).", file=sys.stderr)
        return 1

    print(f"\nPlano: {len(specs)} paginas geradas por modelo", flush=True)
    if config.dry_run:
        for spec in specs:
            print(f"  - {spec.path:<52} {spec.title}")
        if graph is not None:
            print("  - 07-cartography/file-graph.md      (deterministico)")
            print("  - 07-cartography/module-graph.md    (deterministico)")
        return 0

    config.output_path.mkdir(parents=True, exist_ok=True)

    generator = WikiGenerator(config, scan)
    report = await generator.generate(specs)

    # O indice tem de listar a wiki toda, nao so o que foi gerado nesta corrida:
    # com --only, montar o indice a partir do subconjunto apagaria as restantes.
    results = list(report.results)
    if config.only:
        generated_keys = {r.spec.key for r in results}
        for spec in build_plan(scan, replace(config, only=()), graph_ctx):
            if spec.key not in generated_keys and (config.output_path / spec.path).is_file():
                results.append(PageResult(spec=spec, status="cached"))

    if graph is not None:
        written = write_cartography(graph, config)
        results += _synthetic_results(written, config)
        print(f"\nCartografia escrita: {len(written)} ficheiros em 07-cartography/")

    nav_files = assemble(config, scan, results)

    # Um wikilink partido aponta para uma nota que nunca vai existir: degrada-se
    # para texto simples, e o que foi degradado e reportado.
    link_report = validate_and_fix(config.output_path)
    if link_report["broken"]:
        print(
            f"\nLinks: {link_report['checked']} verificados, "
            f"{link_report['broken']} sem destino -> convertidos em texto",
            flush=True,
        )
        for src, target in link_report["details"][:8]:
            print(f"  ~ {src}: [[{target}]]")
        if link_report["broken"] > 8:
            print(f"  ... (+{link_report['broken'] - 8})")

    # Reverificacao a ler do disco: apanha o caso em que algo escreveu por cima
    # depois da validacao. Silencio aqui e a unica prova de que a wiki fechou limpa.
    recheck = validate_and_fix(config.output_path, fix=False)
    if recheck["broken"]:
        print(
            f"  ! {recheck['broken']} links continuam sem destino apos a correcao "
            "— alguma escrita ocorreu depois da validacao",
            file=sys.stderr,
        )

    # As citacoes `ficheiro:linha` sao a classe de erro que sobrevive a um modelo
    # forte; parte dela e mecanicamente detetavel.
    citation_report = check_citations(
        config.output_path, config.repo_path,
        repo_files=[f.rel_path for f in scan.files],
    )
    if citation_report["invalid"]:
        print("\n" + format_report(citation_report), file=sys.stderr)

    print()
    print(f"Geradas:  {len(report.generated)}")
    print(f"Em cache: {len(report.cached)}")
    if report.failed:
        print(f"Falhadas: {len(report.failed)}", file=sys.stderr)
        for result in report.failed:
            print(f"  ! {result.spec.path}: {result.error[:200]}", file=sys.stderr)
    print(f"Tempo:    {report.elapsed_s:.1f}s")
    if report.total_cost_usd:
        print(f"Custo:    ${report.total_cost_usd:.4f}")
    print(f"\nWiki em: {nav_files[0]}")

    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _config_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"Erro de configuracao: {exc}", file=sys.stderr)
        return 2

    if not config.dry_run:
        try:
            ensure_cli_available(config)
        except ClaudeError as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 2

    try:
        return asyncio.run(run_all(config))
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        return 130
    except (ValueError, OSError, ClaudeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
