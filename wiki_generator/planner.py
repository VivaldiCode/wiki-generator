"""Turns the repository scan into the wiki page plan."""

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
from .i18n import translator
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
    "sec.verification",
]


def build_plan(
    scan: RepoScan, config: WikiConfig, graph_context: str = ""
) -> list[PageSpec]:
    t = translator(config.language)
    specs: list[PageSpec] = []
    all_files = [f.rel_path for f in scan.files]
    # 1st pass: metadata only, so we know which pages will exist.
    # 2nd pass (at the end): the prompts, now carrying the vault index.
    prompt_builders: list = []

    # --- fixed pages ---------------------------------------------------
    for page in CORE_PAGES:
        specs.append(
            PageSpec(
                key=page["key"],
                path=page["path"],
                title=t(page["title_key"]),
                section=page["section"],
                kind=page["path"].split("/", 1)[0],
                order=page["order"],
                summary=t(page["summary_key"]),
                prompt="",
                # Cross-cutting pages depend on the whole repository.
                scope_files=all_files,
            )
        )
        prompt_builders.append(
            lambda index, page=page: build_core_prompt(page, scan, config, graph_context, index)
        )

    # --- one page per module -------------------------------------------
    for index, module in enumerate(scan.modules):
        specs.append(
            PageSpec(
                key=f"module.{module.slug}",
                path=f"03-modules/{module.slug}.md",
                title=t("page.module.title", module=module.key),
                section="sec.modules",
                kind="module",
                order=310 + index,
                summary=t(
                    "page.module.summary",
                    files=module.file_count,
                    lines=module.total_lines,
                    languages=", ".join(module.languages[:3]) or "n/a",
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

    # --- low-level reference, split into parts per module --------------
    if config.include_reference:
        reference_pages = 0
        for module_index, module in enumerate(scan.modules):
            groups = chunked(module.files, config.files_per_reference_page)
            for part_index, group in enumerate(groups, start=1):
                if reference_pages >= config.max_reference_pages:
                    break
                suffix = f"-{part_index}" if len(groups) > 1 else ""
                title_suffix = (
                    t("page.reference.part", part=part_index, total=len(groups))
                    if len(groups) > 1 else ""
                )
                specs.append(
                    PageSpec(
                        key=f"reference.{module.slug}{suffix}",
                        path=f"04-reference/{module.slug}{suffix}.md",
                        title=t("page.reference.title", module=module.key) + title_suffix,
                        section="sec.reference",
                        kind="reference",
                        order=410 + module_index * 10 + part_index,
                        summary=", ".join(f.name for f in group[:5])
                        + (f" (+{len(group) - 5})" if len(group) > 5 else ""),
                        prompt="",
                        scope_files=[f.rel_path for f in group],
                        # Every file in the batch must get its own section.
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

    # --- interpretive reading of the cartography graph -----------------
    if graph_context:
        specs.append(
            PageSpec(
                key=CARTOGRAPHY_PAGE["key"],
                path=CARTOGRAPHY_PAGE["path"],
                title=t(CARTOGRAPHY_PAGE["title_key"]),
                section=CARTOGRAPHY_PAGE["section"],
                kind="cartography",
                order=CARTOGRAPHY_PAGE["order"],
                summary=t(CARTOGRAPHY_PAGE["summary_key"]),
                prompt="",
                scope_files=all_files,
            )
        )
        prompt_builders.append(
            lambda index: build_cartography_prompt(scan, config, graph_context, index)
        )

    # 2nd pass: now that every page is known, build the prompts with the vault
    # index, so the model cannot invent wikilink targets.
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
