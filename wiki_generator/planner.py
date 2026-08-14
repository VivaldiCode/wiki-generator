"""Transforma o scan do repositorio no plano de paginas da wiki."""

from __future__ import annotations

from .config import WikiConfig
from .models import PageSpec, RepoScan
from .prompts import (
    CARTOGRAPHY_PAGE,
    CORE_PAGES,
    build_cartography_prompt,
    build_core_prompt,
    build_module_prompt,
    build_reference_prompt,
    wiki_pages_block,
)
from .utils import chunked

# Canonical section keys, resolved to text by `i18n` at render time.
SECTION_ORDER = [
    "sec.overview",
    "sec.architecture",
    "sec.modules",
    "sec.reference",
    "sec.guides",
    "sec.operations",
    "sec.cartography",
]


def build_plan(
    scan: RepoScan, config: WikiConfig, graph_context: str = ""
) -> list[PageSpec]:
    specs: list[PageSpec] = []
    all_files = [f.rel_path for f in scan.files]
    # 1ª passagem: so os metadados, para saber que paginas vao existir.
    # 2ª passagem (no fim): os prompts, ja com o indice do vault.
    prompt_builders: list = []

    # --- paginas fixas -------------------------------------------------
    for page in CORE_PAGES:
        specs.append(
            PageSpec(
                key=page["key"],
                path=page["path"],
                title=page["title"],
                section=page["section"],
                kind=page["path"].split("/", 1)[0],
                order=page["order"],
                summary=page["summary"],
                prompt="",
                # As paginas transversais dependem de todo o repositorio.
                scope_files=all_files,
            )
        )
        prompt_builders.append(
            lambda index, page=page: build_core_prompt(page, scan, config, graph_context, index)
        )

    # --- uma pagina por modulo ----------------------------------------
    for index, module in enumerate(scan.modules):
        specs.append(
            PageSpec(
                key=f"module.{module.slug}",
                path=f"03-modules/{module.slug}.md",
                title=f"Modulo: {module.key}",
                section="sec.modules",
                kind="module",
                order=310 + index,
                summary=(
                    f"{module.file_count} ficheiros, {module.total_lines} linhas — "
                    f"{', '.join(module.languages[:3]) or 'n/d'}"
                ),
                prompt="",
                scope_files=[f.rel_path for f in module.files],
            )
        )
        prompt_builders.append(
            lambda index, module=module: build_module_prompt(
                module, scan, config, graph_context, index
            )
        )

    # --- referencia de baixo nivel, em partes por modulo ---------------
    if config.include_reference:
        reference_pages = 0
        for module_index, module in enumerate(scan.modules):
            groups = chunked(module.files, config.files_per_reference_page)
            for part_index, group in enumerate(groups, start=1):
                if reference_pages >= config.max_reference_pages:
                    break
                suffix = f"-{part_index}" if len(groups) > 1 else ""
                title_suffix = (
                    f" (parte {part_index} de {len(groups)})" if len(groups) > 1 else ""
                )
                specs.append(
                    PageSpec(
                        key=f"reference.{module.slug}{suffix}",
                        path=f"04-reference/{module.slug}{suffix}.md",
                        title=f"Referencia: {module.key}{title_suffix}",
                        section="sec.reference",
                        kind="reference",
                        order=410 + module_index * 10 + part_index,
                        summary=", ".join(f.name for f in group[:5])
                        + (f" (+{len(group) - 5})" if len(group) > 5 else ""),
                        prompt="",
                        scope_files=[f.rel_path for f in group],
                        # Cada ficheiro do lote tem de ter a sua propria seccao.
                        required_markers=[f"## {f.rel_path}" for f in group],
                    )
                )
                prompt_builders.append(
                    lambda index, module=module, group=group, part_index=part_index,
                    total=len(groups): build_reference_prompt(
                        module, group, part_index, total, scan, config, index
                    )
                )
                reference_pages += 1
            if reference_pages >= config.max_reference_pages:
                break

    # --- leitura interpretativa do grafo de cartografia -----------------
    if graph_context:
        specs.append(
            PageSpec(
                key=CARTOGRAPHY_PAGE["key"],
                path=CARTOGRAPHY_PAGE["path"],
                title=CARTOGRAPHY_PAGE["title"],
                section=CARTOGRAPHY_PAGE["section"],
                kind="cartography",
                order=CARTOGRAPHY_PAGE["order"],
                summary=CARTOGRAPHY_PAGE["summary"],
                prompt="",
                scope_files=all_files,
            )
        )
        prompt_builders.append(
            lambda index: build_cartography_prompt(scan, config, graph_context, index)
        )

    # 2ª passagem: agora que todas as paginas sao conhecidas, constroi os prompts
    # com o indice do vault, para o modelo nao inventar destinos de wikilink.
    page_index = wiki_pages_block(
        [(spec.path[:-3], spec.title) for spec in specs]
        + [("07-cartography/file-graph", "Cartografia — Grafo de Ficheiros"),
           ("07-cartography/module-graph", "Cartografia — Grafo de Modulos")]
        if graph_context else [(spec.path[:-3], spec.title) for spec in specs]
    )
    for spec, builder in zip(specs, prompt_builders):
        spec.prompt = builder(page_index)

    specs.sort(key=lambda spec: spec.order)

    if config.only:
        wanted = set(config.only)
        specs = [
            spec
            for spec in specs
            if spec.key in wanted
            or spec.kind in wanted
            or any(spec.key.startswith(f"{w}.") for w in wanted)
        ]

    return specs
