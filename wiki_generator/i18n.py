"""Strings of the deterministically generated wiki pages.

Model-written pages follow `--language` because the prompt says so. The pages this
tool writes itself — index, summary, cartography, footers — need the same treatment,
otherwise `--language pt` yields Portuguese prose under English headings.

English is the fallback: an unknown locale degrades to English rather than failing.
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # --- page titles and index summaries ---
        "page.introduction.title": "Introduction",
        "page.introduction.summary": "What the project is, the problem it solves and its main capabilities.",
        "page.tech-stack.title": "Technology Stack",
        "page.tech-stack.summary": "Languages, frameworks, dependencies, build and tooling.",
        "page.repository-structure.title": "Repository Structure",
        "page.repository-structure.summary": "Directory map and the responsibility of each one.",
        "page.glossary.title": "Glossary",
        "page.glossary.summary": "Domain terms and acronyms used in the code.",
        "page.architecture-overview.title": "Architecture — High-Level View",
        "page.architecture-overview.summary": "Architectural style, main building blocks and how they relate.",
        "page.components.title": "Components and Responsibilities",
        "page.components.summary": "Detail of each logical component and its boundaries.",
        "page.data-flow.title": "Data Flows and Use Cases",
        "page.data-flow.summary": "How a request or task travels through the system, end to end.",
        "page.data-model.title": "Data Model",
        "page.data-model.summary": "Entities, schemas, persistence and migrations.",
        "page.integrations.title": "External Integrations",
        "page.integrations.summary": "APIs, services, queues and runtime external dependencies.",
        "page.cross-cutting.title": "Cross-Cutting Concerns",
        "page.cross-cutting.summary": "Configuration, errors, logging, security, concurrency and performance.",
        "page.decisions.title": "Design Decisions",
        "page.decisions.summary": "Inferred architectural decisions, alternatives and trade-offs.",
        "page.getting-started.title": "Getting Started",
        "page.getting-started.summary": "Install, configure and run the project locally.",
        "page.development.title": "Development Workflow",
        "page.development.summary": "How to develop, test and validate changes.",
        "page.configuration.title": "Configuration",
        "page.configuration.summary": "Environment variables, configuration files and defaults.",
        "page.deployment.title": "Build and Deployment",
        "page.deployment.summary": "How the project is built, packaged and deployed.",
        "page.observability.title": "Observability and Troubleshooting",
        "page.observability.summary": "Logs, metrics, health checks and diagnosing problems.",
        "page.reading-the-map.title": "Cartography — Reading the Map",
        "page.reading-the-map.summary": "Reading the dependency graph: hubs, layers, cycles and risks.",
        "page.module.title": "Module: {module}",
        "page.module.summary": "{files} files, {lines} lines — {languages}",
        "page.reference.title": "Reference: {module}",
        "page.reference.part": " (part {part} of {total})",
        # --- sections (also used as index headings) ---
        "sec.overview": "1. Overview",
        "sec.architecture": "2. Architecture",
        "sec.modules": "3. Modules",
        "sec.reference": "4. Code Reference",
        "sec.guides": "5. Guides",
        "sec.operations": "6. Operations",
        "sec.cartography": "7. Code Cartography",
        "sec.verification": "8. Verification",
        # --- verification report ---
        "verify.title": "Verification Report",
        "verify.intro": (
            "Claims made by this wiki, checked against the source code by an "
            "independent reviewer ({model}) and then challenged by an adversarial "
            "pass. Only findings that survived the challenge are listed."
        ),
        "verify.m.pages": "Pages verified",
        "verify.m.findings": "Surviving findings",
        "verify.m.overturned": "Findings overturned on challenge",
        "verify.m.model": "Verifier model",
        "verify.m.claims": "Claims extracted",
        "verify.m.unanswered": "Claims left unanswered",
        "verify.m.rejected": "Contradictions dropped for unusable evidence",
        "verify.none": "No contradicted claims found on the verified pages.",
        "verify.nothing": (
            "**Nothing was verified.** Every page in scope failed or was skipped, so "
            "this report says nothing about the wiki's accuracy."
        ),
        "verify.partial": (
            "> **Partial:** the budget or an error stopped verification early. "
            "Not checked: {pages}"
        ),
        "verify.incomplete": (
            "> **Incomplete:** {count} claim(s) were never answered by a checker. "
            "This report covers the rest."
        ),
        "verify.f.claim": "The wiki says",
        "verify.f.reality": "The code shows",
        "verify.f.evidence": "Evidence",
        "verify.f.absent": "`{path}` does not exist in the repository.",
        "verify.stamp": (
            "<sub>Computed against wiki state `{stamp}`. Regenerate the pages and "
            "this report no longer describes them.</sub>"
        ),
        "verify.disclaimer": (
            "<sub>Findings are model-generated and were required to cite a file and "
            "line that resolves. Confirm against the code before acting.</sub>"
        ),
        # --- index ---
        "index.title": "Wiki — {project}",
        "index.subtitle": "Documentation generated automatically from the source code.",
        "index.repository": "Repository",
        "index.files": "Files analysed",
        "index.lines": "Lines of code",
        "index.languages": "Languages",
        "index.modules": "Documented modules",
        "index.pages": "Pages",
        "index.model": "Model",
        "index.generated_at": "Generated at",
        "index.contents": "Contents",
        "index.failed": "Pages that failed",
        "index.failed.intro": (
            "These pages were not produced in the last run. Everything else on this "
            "index was."
        ),
        "index.failed.stale": "out of date, showing the previous version",
        "index.failed.current": (
            "no harm done: the version on disk already matches the sources"
        ),
        "index.failed.missing": "not written — the note does not exist",
        "index.failed.more": "- ... and {count} more with the same error",
        "index.failed.retry": (
            "Run the command under **{section}** again: only pages that are missing "
            "or out of date are regenerated, so a second run costs a fraction of the "
            "first. Do not add `--force` — it would pay for every page again."
        ),
        "index.failed.links": (
            "<sub>{count} page(s) have no file, so links pointing at them were "
            "degraded to plain text.</sub>"
        ),
        "index.regenerate": "How to regenerate",
        "index.incremental": (
            "Only pages whose source files changed are regenerated. "
            "Use `--force` to regenerate everything."
        ),
        "index.disclaimer": (
            "> These pages are model-generated from the code. Treat them as a useful "
            "map, not as the source of truth — the code is."
        ),
        "summary.title": "Summary",
        "summary.index": "Index",
        # --- page footer ---
        "footer.index": "<- Wiki index",
        "footer.generated": (
            "Generated by wiki-generator ({model}) from {count} file(s) of "
            "`{project}`. Do not edit by hand: changes are lost on the next run."
        ),
        "coverage.warning": (
            "> **Coverage warning:** this page does not document {missing}. "
            "Check the code directly for those files."
        ),
        # --- cartography ---
        "carto.file.title": "Cartography — File Graph",
        "carto.file.intro": (
            "Dependency graph between files, extracted statically from the code's "
            "`import`/`require`/`include` statements. Each edge `A --> B` means "
            "**A imports B**."
        ),
        "carto.metric": "Metric",
        "carto.value": "Value",
        "carto.m.nodes": "Files in the graph",
        "carto.m.edges": "Internal edges",
        "carto.m.orphans": "Orphan files",
        "carto.m.cycles": "Dependency cycles",
        "carto.m.external": "Distinct external packages",
        "carto.full_graph": "Full graph",
        "carto.links_table": "Links per file",
        "carto.th.file": "File",
        "carto.th.imports": "Imports",
        "carto.th.imported_by": "Imported by",
        "carto.hubs": "Hubs",
        "carto.hubs.intro": (
            "Files with the most connections — changes here propagate furthest."
        ),
        "carto.th.total": "Total",
        "carto.cycles": "Dependency cycles",
        "carto.cycles.found": (
            "Cycles make code hard to test and to extract. Detected:"
        ),
        "carto.cycles.none": "No dependency cycles detected.",
        "carto.orphans": "Orphan files",
        "carto.orphans.intro": (
            "No inbound or outbound edges. They may be dynamically loaded "
            "entrypoints, standalone scripts, or dead code — worth confirming."
        ),
        "carto.orphans.none": "No orphan files.",
        "carto.external": "Most used external dependencies",
        "carto.th.package": "Package",
        "carto.th.imports_count": "Imports",
        "carto.no_extractor": "Languages without import analysis",
        "carto.no_extractor.intro": (
            "These languages are present in the repository but have no import "
            "extractor in this tool. Their files appear in the graph **without "
            "edges** — the absence of links does not mean they are isolated:"
        ),
        "carto.unresolved": "Unresolved relative imports",
        "carto.unresolved.intro": (
            "Relative specifiers that do not point at an analysed file (build "
            "aliases, generated files, or files excluded from the scan)."
        ),
        "carto.split.intro": (
            "The repository has {nodes} files in the graph — above the {limit} "
            "limit for a single readable diagram on one page. Coverage is still "
            "complete, split like this:"
        ),
        "carto.split.b1": (
            "- **Complete graph, untruncated:** `07-cartography/file-graph.mmd` "
            "(any Mermaid viewer) and `07-cartography/graph.json` (tooling)."
        ),
        "carto.split.b2": (
            "- **Aggregated view:** the per-module diagram below — nodes are "
            "clickable and open each module's page."
        ),
        "carto.split.b3": (
            "- **File-by-file detail:** one page per module, indexed next. On those "
            "pages, files from other modules are dashed and clickable, so the graph "
            "can be walked module by module."
        ),
        "carto.aggregated": "Aggregated per-module view",
        "carto.per_module": "Per-module diagrams",
        "carto.th.module": "Module",
        "carto.th.files": "Files",
        "carto.th.imports_from": "Imports from",
        "carto.th.imported_by_n": "Imported by",
        "carto.th.pages": "Pages",
        "carto.view": "view",
        "carto.module.title": "Cartography — `{module}`",
        "carto.module.part": " (part {part} of {total})",
        "carto.module.intro": (
            "{count} files on this page{of_total}. Files from other modules are "
            "dashed and clickable."
        ),
        "carto.module.of_total": ", out of {total} in the module",
        "carto.module.parts": "**Parts of this module:** ",
        "carto.module.dropped": (
            "> {dropped} neighbouring files were left out of the diagram for "
            "readability (the most connected ones were kept). The links table below "
            "is complete, and the full graph is in `07-cartography/graph.json`."
        ),
        "carto.module.neighbours": "Neighbouring modules",
        "carto.module.th.out": "This imports from it",
        "carto.module.th.in": "It imports from this",
        "carto.module.no_neighbours": "This module has no links to other modules.",
        "carto.mod.title": "Cartography — Module Graph",
        "carto.mod.intro": (
            "The same graph aggregated at module level. The number on an edge is how "
            "many file imports back it."
        ),
        "carto.mod.clickable": (
            " Nodes are clickable and open each module's detailed cartography."
        ),
        "carto.mod.coupling": "Inter-module coupling",
        "carto.th.source": "Source",
        "carto.th.target": "Target",
        "carto.footer.file_graph": "File graph",
        "carto.footer.module_graph": "Module graph",
        "carto.footer.deterministic": (
            "<sub>Page computed deterministically by wiki-generator (static import "
            "analysis). Not model-generated.</sub>"
        ),
        "carto.empty": "No files to display",
        "carto.this_module": "this module",
        "carto.neighbour": "neighbour",
        "carto.click_tooltip": "Open this module's cartography",
        "carto.none": "(none)",
    },
    "pt": {
        "page.introduction.title": "Introducao",
        "page.introduction.summary": "O que e o projeto, que problema resolve e quais as capacidades principais.",
        "page.tech-stack.title": "Stack Tecnologica",
        "page.tech-stack.summary": "Linguagens, frameworks, dependencias, ferramentas de build e tooling.",
        "page.repository-structure.title": "Estrutura do Repositorio",
        "page.repository-structure.summary": "Mapa de diretorios e a responsabilidade de cada um.",
        "page.glossary.title": "Glossario",
        "page.glossary.summary": "Termos de dominio e siglas usados no codigo.",
        "page.architecture-overview.title": "Arquitetura — Visao de Alto Nivel",
        "page.architecture-overview.summary": "Estilo arquitetural, blocos principais e como se relacionam.",
        "page.components.title": "Componentes e Responsabilidades",
        "page.components.summary": "Detalhe de cada componente logico e das suas fronteiras.",
        "page.data-flow.title": "Fluxos de Dados e Casos de Uso",
        "page.data-flow.summary": "Como um pedido ou tarefa atravessa o sistema, ponta a ponta.",
        "page.data-model.title": "Modelo de Dados",
        "page.data-model.summary": "Entidades, esquemas, persistencia e migracoes.",
        "page.integrations.title": "Integracoes Externas",
        "page.integrations.summary": "APIs, servicos, filas e dependencias externas em runtime.",
        "page.cross-cutting.title": "Preocupacoes Transversais",
        "page.cross-cutting.summary": "Configuracao, erros, logging, seguranca, concorrencia e performance.",
        "page.decisions.title": "Decisoes de Desenho",
        "page.decisions.summary": "Decisoes arquiteturais inferidas, alternativas e trade-offs.",
        "page.getting-started.title": "Primeiros Passos",
        "page.getting-started.summary": "Instalar, configurar e correr o projeto localmente.",
        "page.development.title": "Fluxo de Desenvolvimento",
        "page.development.summary": "Como desenvolver, testar e validar alteracoes.",
        "page.configuration.title": "Configuracao",
        "page.configuration.summary": "Variaveis de ambiente, ficheiros de configuracao e defaults.",
        "page.deployment.title": "Build e Deployment",
        "page.deployment.summary": "Como se constroi, empacota e faz deploy do projeto.",
        "page.observability.title": "Observabilidade e Troubleshooting",
        "page.observability.summary": "Logs, metricas, health checks e diagnostico de problemas.",
        "page.reading-the-map.title": "Cartografia — Como Ler o Mapa",
        "page.reading-the-map.summary": "Leitura do grafo de dependencias: hubs, camadas, ciclos e riscos.",
        "page.module.title": "Modulo: {module}",
        "page.module.summary": "{files} ficheiros, {lines} linhas — {languages}",
        "page.reference.title": "Referencia: {module}",
        "page.reference.part": " (parte {part} de {total})",
        "sec.overview": "1. Visao Geral",
        "sec.architecture": "2. Arquitetura",
        "sec.modules": "3. Modulos",
        "sec.reference": "4. Referencia de Codigo",
        "sec.guides": "5. Guias",
        "sec.operations": "6. Operacao",
        "sec.cartography": "7. Cartografia do Codigo",
        "sec.verification": "8. Verificacao",
        "verify.title": "Relatorio de Verificacao",
        "verify.intro": (
            "Afirmacoes desta wiki confrontadas com o codigo-fonte por um revisor "
            "independente ({model}) e depois contestadas por uma passagem "
            "adversarial. So constam os achados que sobreviveram a contestacao."
        ),
        "verify.m.pages": "Paginas verificadas",
        "verify.m.findings": "Achados que sobreviveram",
        "verify.m.overturned": "Achados derrubados na contestacao",
        "verify.m.model": "Modelo verificador",
        "verify.m.claims": "Afirmacoes extraidas",
        "verify.m.unanswered": "Afirmacoes sem resposta",
        "verify.m.rejected": "Contradicoes descartadas por evidencia inutilizavel",
        "verify.none": "Nenhuma afirmacao contrariada nas paginas verificadas.",
        "verify.nothing": (
            "**Nao foi verificado nada.** Todas as paginas do ambito falharam ou "
            "foram ignoradas, portanto este relatorio nada diz sobre a exatidao "
            "da wiki."
        ),
        "verify.partial": (
            "> **Parcial:** o orcamento ou um erro interromperam a verificacao. "
            "Nao verificadas: {pages}"
        ),
        "verify.incomplete": (
            "> **Incompleto:** {count} afirmacao(oes) ficaram sem resposta de um "
            "verificador. Este relatorio cobre as restantes."
        ),
        "verify.f.claim": "A wiki diz",
        "verify.f.reality": "O codigo mostra",
        "verify.f.evidence": "Evidencia",
        "verify.f.absent": "`{path}` nao existe no repositorio.",
        "verify.stamp": (
            "<sub>Calculado sobre o estado `{stamp}` da wiki. Se regerares as "
            "paginas, este relatorio deixa de as descrever.</sub>"
        ),
        "verify.disclaimer": (
            "<sub>Os achados sao gerados por modelo e obrigados a citar um ficheiro "
            "e linha que resolvem. Confirma no codigo antes de agir.</sub>"
        ),
        "index.title": "Wiki — {project}",
        "index.subtitle": "Documentacao gerada automaticamente a partir do codigo-fonte.",
        "index.repository": "Repositorio",
        "index.files": "Ficheiros analisados",
        "index.lines": "Linhas de codigo",
        "index.languages": "Linguagens",
        "index.modules": "Modulos documentados",
        "index.pages": "Paginas",
        "index.model": "Modelo",
        "index.generated_at": "Gerado em",
        "index.contents": "Indice",
        "index.failed": "Paginas com falha",
        "index.failed.intro": (
            "Estas paginas nao foram produzidas na ultima execucao. Todo o resto "
            "deste indice foi."
        ),
        "index.failed.stale": "desatualizada, mostra a versao anterior",
        "index.failed.current": (
            "sem estragos: a versao em disco ja corresponde as fontes"
        ),
        "index.failed.missing": "nao foi escrita — a nota nao existe",
        "index.failed.more": "- ... e mais {count} com o mesmo erro",
        "index.failed.retry": (
            "Corre outra vez o comando em **{section}**: so sao regeradas as paginas "
            "em falta ou desatualizadas, por isso uma segunda execucao custa uma "
            "fracao da primeira. Nao acrescentes `--force` — pagava outra vez todas "
            "as paginas."
        ),
        "index.failed.links": (
            "<sub>{count} pagina(s) sem ficheiro, por isso as ligacoes que lhes "
            "apontavam foram degradadas para texto simples.</sub>"
        ),
        "index.regenerate": "Como regerar",
        "index.incremental": (
            "Apenas as paginas cujos ficheiros de origem mudaram sao regeradas. "
            "Usa `--force` para regerar tudo."
        ),
        "index.disclaimer": (
            "> Estas paginas sao geradas por um modelo a partir do codigo. Trata-as "
            "como um mapa util, nao como a fonte de verdade — o codigo e que manda."
        ),
        "summary.title": "Sumario",
        "summary.index": "Indice",
        "footer.index": "<- Indice da wiki",
        "footer.generated": (
            "Gerada por wiki-generator ({model}) a partir de {count} ficheiro(s) de "
            "`{project}`. Nao editar a mao: as alteracoes sao perdidas na proxima geracao."
        ),
        "coverage.warning": (
            "> **Aviso de cobertura:** esta pagina nao documenta {missing}. "
            "Consulta o codigo diretamente para esses ficheiros."
        ),
        "carto.file.title": "Cartografia — Grafo de Ficheiros",
        "carto.file.intro": (
            "Grafo de dependencias entre ficheiros, extraido estaticamente dos "
            "`import`/`require`/`include` do codigo. Cada aresta `A --> B` significa "
            "**A importa B**."
        ),
        "carto.metric": "Metrica",
        "carto.value": "Valor",
        "carto.m.nodes": "Ficheiros no grafo",
        "carto.m.edges": "Ligacoes internas",
        "carto.m.orphans": "Ficheiros isolados",
        "carto.m.cycles": "Ciclos de dependencia",
        "carto.m.external": "Pacotes externos distintos",
        "carto.full_graph": "Grafo completo",
        "carto.links_table": "Ligacoes por ficheiro",
        "carto.th.file": "Ficheiro",
        "carto.th.imports": "Importa",
        "carto.th.imported_by": "Importado por",
        "carto.hubs": "Hubs",
        "carto.hubs.intro": (
            "Ficheiros com mais ligacoes — mudancas aqui propagam-se mais longe."
        ),
        "carto.th.total": "Total",
        "carto.cycles": "Ciclos de dependencia",
        "carto.cycles.found": (
            "Ciclos tornam o codigo dificil de testar e de extrair. Detetados:"
        ),
        "carto.cycles.none": "Nenhum ciclo de dependencia detetado.",
        "carto.orphans": "Ficheiros isolados",
        "carto.orphans.intro": (
            "Sem ligacoes de entrada nem de saida. Podem ser entrypoints carregados "
            "dinamicamente, scripts avulsos, ou codigo morto — vale a pena confirmar."
        ),
        "carto.orphans.none": "Nenhum ficheiro isolado.",
        "carto.external": "Dependencias externas mais usadas",
        "carto.th.package": "Pacote",
        "carto.th.imports_count": "Imports",
        "carto.no_extractor": "Linguagens sem analise de imports",
        "carto.no_extractor.intro": (
            "Estas linguagens estao presentes no repositorio mas nao tem extrator de "
            "imports nesta ferramenta. Os seus ficheiros aparecem no grafo **sem "
            "arestas** — a ausencia de ligacoes nao significa que estejam isolados:"
        ),
        "carto.unresolved": "Imports relativos nao resolvidos",
        "carto.unresolved.intro": (
            "Especificadores relativos que nao apontam para um ficheiro analisado "
            "(aliases de build, ficheiros gerados, ou ficheiros excluidos do scan)."
        ),
        "carto.split.intro": (
            "O repositorio tem {nodes} ficheiros no grafo — acima do limite de "
            "{limit} para um unico diagrama legivel numa pagina. A cobertura "
            "continua a ser total, repartida assim:"
        ),
        "carto.split.b1": (
            "- **Grafo integral, sem truncagem:** `07-cartography/file-graph.mmd` "
            "(qualquer visualizador Mermaid) e `07-cartography/graph.json` (ferramentas)."
        ),
        "carto.split.b2": (
            "- **Vista agregada:** o diagrama por modulo aqui abaixo — os nos sao "
            "clicaveis e levam a pagina de cada modulo."
        ),
        "carto.split.b3": (
            "- **Detalhe ficheiro-a-ficheiro:** uma pagina por modulo, indexada a "
            "seguir. Nessas paginas, os ficheiros de outros modulos aparecem a "
            "tracejado e sao clicaveis, para se poder navegar o grafo de modulo em modulo."
        ),
        "carto.aggregated": "Vista agregada por modulo",
        "carto.per_module": "Diagramas por modulo",
        "carto.th.module": "Modulo",
        "carto.th.files": "Ficheiros",
        "carto.th.imports_from": "Importa de",
        "carto.th.imported_by_n": "E importado por",
        "carto.th.pages": "Paginas",
        "carto.view": "ver",
        "carto.module.title": "Cartografia — `{module}`",
        "carto.module.part": " (parte {part} de {total})",
        "carto.module.intro": (
            "{count} ficheiros nesta pagina{of_total}. Os ficheiros de outros modulos "
            "aparecem a tracejado e sao clicaveis."
        ),
        "carto.module.of_total": ", de {total} no modulo",
        "carto.module.parts": "**Partes deste modulo:** ",
        "carto.module.dropped": (
            "> {dropped} ficheiros vizinhos foram omitidos do diagrama por "
            "legibilidade (ficaram os mais ligados a este modulo). A tabela de "
            "ligacoes abaixo esta completa, e o grafo integral esta em "
            "`07-cartography/graph.json`."
        ),
        "carto.module.neighbours": "Modulos vizinhos",
        "carto.module.th.out": "Este importa de la",
        "carto.module.th.in": "Esse importa daqui",
        "carto.module.no_neighbours": "Este modulo nao tem ligacoes a outros modulos.",
        "carto.mod.title": "Cartografia — Grafo de Modulos",
        "carto.mod.intro": (
            "Mesmo grafo agregado ao nivel de modulo. O numero na aresta e a "
            "quantidade de imports de ficheiro que a sustentam."
        ),
        "carto.mod.clickable": (
            " Os nos sao clicaveis e abrem a cartografia detalhada de cada modulo."
        ),
        "carto.mod.coupling": "Acoplamento entre modulos",
        "carto.th.source": "Origem",
        "carto.th.target": "Destino",
        "carto.footer.file_graph": "Grafo de ficheiros",
        "carto.footer.module_graph": "Grafo de modulos",
        "carto.footer.deterministic": (
            "<sub>Pagina calculada deterministicamente por wiki-generator (analise "
            "estatica de imports). Nao gerada por modelo.</sub>"
        ),
        "carto.empty": "Sem ficheiros para representar",
        "carto.this_module": "este modulo",
        "carto.neighbour": "vizinho",
        "carto.click_tooltip": "Abrir a cartografia deste modulo",
        "carto.none": "(nenhum)",
    },
}

# `pt-br` shares the Portuguese strings until it diverges.
STRINGS["pt-br"] = STRINGS["pt"]


class Translator:
    """Looks up a string for a locale, falling back to English."""

    def __init__(self, language: str) -> None:
        self.language = (language or "en").lower()
        self.table = STRINGS.get(self.language, STRINGS["en"])

    def __call__(self, key: str, **kwargs) -> str:
        text = self.table.get(key) or STRINGS["en"].get(key) or key
        return text.format(**kwargs) if kwargs else text


def translator(language: str) -> Translator:
    return Translator(language)
