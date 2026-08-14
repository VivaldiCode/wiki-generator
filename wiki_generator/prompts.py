"""Construcao dos prompts. A estrutura da wiki e definida aqui, e so aqui."""

from __future__ import annotations

from .config import WikiConfig
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
5. Follow the required outline exactly: same headings, same order, same levels.
   Keep a heading even if the answer is "not applicable in this repository".
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
    """Bloco de contexto partilhado por todos os prompts."""
    languages = ", ".join(
        f"{lang} ({count} ficheiros)" for lang, count in
        sorted(scan.languages.items(), key=lambda kv: -kv[1])[:8]
    ) or "indeterminado"

    modules = "\n".join(
        f"- `{module.key}` — {module.file_count} ficheiros, {module.total_lines} linhas"
        f" ({', '.join(module.languages[:3])})"
        for module in scan.modules
    ) or "- (nenhum modulo detetado)"

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
    """Indice das paginas que vao existir, para o modelo so linkar o que existe."""
    if not pages:
        return ""
    listed = "\n".join(f"- [[{path}]] — {title}" for path, title in pages)
    return f"""<wiki_pages>
Paginas desta wiki. Sao os UNICOS destinos validos para um wikilink:
{listed}
- [[README]] — indice da wiki
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
The page MUST use exactly these headings, in this order:

{outline}
</required_outline>

Output the Markdown page now, starting with `# {title}`.
"""


# ----------------------------------------------------------------------
# Paginas fixas: (key, path, title, section, order, goal, outline, investigate)
# ----------------------------------------------------------------------
CORE_PAGES: list[dict] = [
    {
        "key": "overview.introduction",
        "path": "01-overview/introduction.md",
        "title": "Introducao",
        "section": "1. Visao Geral",
        "order": 110,
        "summary": "O que e o projeto, que problema resolve e quais as capacidades principais.",
        "goal": "Explain what this project is, the problem it solves, and what it can do — derived strictly from the code and docs present.",
        "outline": """## Em uma frase
## Problema e contexto
## Capacidades principais
(lista com bullets; cada bullet aponta o ficheiro/modulo que a implementa)
## Fora de ambito
## Estado e maturidade
(evidencias: testes, CI, versionamento, TODOs)
## Gaps / Open questions""",
        "investigate": """- `README.md`, `docs/`, `CONTRIBUTING.md`, `CHANGELOG.md`
- os entrypoints listados acima
- manifestos de dependencias para perceber o dominio""",
    },
    {
        "key": "overview.tech-stack",
        "path": "01-overview/tech-stack.md",
        "title": "Stack Tecnologica",
        "section": "1. Visao Geral",
        "order": 120,
        "summary": "Linguagens, frameworks, dependencias, ferramentas de build e tooling.",
        "goal": "Inventory the technology stack: languages, runtimes, frameworks, notable libraries, build/test/lint tooling.",
        "outline": """## Resumo
## Linguagens e runtimes
(tabela: Linguagem | Versao exigida | Onde e usada | Evidencia)
## Frameworks e bibliotecas principais
(tabela: Nome | Versao | Para que serve neste projeto | Evidencia)
## Build, testes e qualidade
(tabela: Ferramenta | Comando | Ficheiro de configuracao)
## Infraestrutura e servicos externos
## Gaps / Open questions""",
        "investigate": """- manifestos de dependencias (ja incluidos acima quando existem)
- ficheiros de lock, Dockerfile, Makefile, workflows de CI
- ficheiros de configuracao de linters/formatters/test runners""",
    },
    {
        "key": "overview.repository-structure",
        "path": "01-overview/repository-structure.md",
        "title": "Estrutura do Repositorio",
        "section": "1. Visao Geral",
        "order": 130,
        "summary": "Mapa de diretorios e a responsabilidade de cada um.",
        "goal": "Explain the repository layout: what lives where and why, plus naming/organisation conventions.",
        "outline": """## Mapa de diretorios
(tabela: Caminho | Responsabilidade | Notas)
## Convencoes de organizacao
(nomenclatura, colocacao de testes, onde vive configuracao, geracao de codigo)
## Ficheiros de topo relevantes
## Onde comecar a ler
(3-5 ficheiros, por ordem, com justificacao)
## Gaps / Open questions""",
        "investigate": """- percorre os diretorios de topo e le 1-2 ficheiros representativos de cada
- procura padroes repetidos de nomes de ficheiros""",
    },
    {
        "key": "overview.glossary",
        "path": "01-overview/glossary.md",
        "title": "Glossario",
        "section": "1. Visao Geral",
        "order": 140,
        "summary": "Termos de dominio e siglas usados no codigo.",
        "goal": "Define the domain vocabulary that appears in the code, so a newcomer can read identifiers without guessing.",
        "outline": """## Termos de dominio
(tabela: Termo | Significado neste projeto | Onde aparece)
## Siglas e abreviaturas
(tabela: Sigla | Expansao | Contexto)
## Nomes que enganam
(termos cujo significado aqui difere do uso comum; omite a seccao com "Nenhum identificado" se nao houver)
## Gaps / Open questions""",
        "investigate": """- nomes de modelos/entidades/tabelas/tipos
- comentarios e docstrings
- nomes repetidos em identificadores e strings""",
    },
    {
        "key": "architecture.overview",
        "path": "02-architecture/overview.md",
        "title": "Arquitetura — Visao de Alto Nivel",
        "section": "2. Arquitetura",
        "order": 210,
        "summary": "Estilo arquitetural, blocos principais e como se relacionam.",
        "goal": "Describe the high-level architecture: architectural style, the main building blocks, and how they fit together.",
        "outline": """## Estilo arquitetural
(monolito / camadas / hexagonal / microservicos / CLI / biblioteca — com a evidencia que suporta a classificacao)
## Diagrama de contexto
(um bloco ```mermaid com `flowchart TD` mostrando o sistema, atores e sistemas externos)
## Blocos principais
(tabela: Bloco | Responsabilidade | Codigo)
## Diagrama de componentes
(um bloco ```mermaid com `flowchart LR` mostrando os blocos internos e as dependencias entre eles)
## Regras de dependencia
(que camada pode chamar qual; violacoes observadas)
Para afirmar que nao ha violacoes tens de o ter procurado: usa o grafo em
`<code_cartography>` e/ou um Grep por imports que atravessem camadas, e diz que
verificacao fizeste. Sem essa verificacao, escreve "nao verificado exaustivamente".
## Gaps / Open questions""",
        "investigate": """- entrypoints e o que instanciam
- diretorios de topo do codigo-fonte e os imports entre eles
- configuracao de rotas/handlers/comandos""",
    },
    {
        "key": "architecture.components",
        "path": "02-architecture/components.md",
        "title": "Componentes e Responsabilidades",
        "section": "2. Arquitetura",
        "order": 220,
        "summary": "Detalhe de cada componente logico e das suas fronteiras.",
        "goal": "Detail each logical component: responsibility, public surface, collaborators, and boundaries.",
        "outline": """## Inventario de componentes
(tabela: Componente | Modulo/caminho | Responsabilidade unica | Depende de)
## Detalhe por componente
(uma subseccao `###` por componente com: Responsabilidade, Interface publica, Colaboradores, Invariantes/pressupostos)
## Acoplamentos notaveis
## Gaps / Open questions""",
        "investigate": """- os modulos listados no contexto do repositorio
- classes/servicos/handlers exportados por cada um""",
    },
    {
        "key": "architecture.data-flow",
        "path": "02-architecture/data-flow.md",
        "title": "Fluxos de Dados e Casos de Uso",
        "section": "2. Arquitetura",
        "order": 230,
        "summary": "Como um pedido/tarefa atravessa o sistema, ponta a ponta.",
        "goal": "Trace the main end-to-end flows through the system, from entrypoint to persistence/response.",
        "outline": """## Fluxos principais
(lista dos 2-5 fluxos mais importantes, uma linha cada)
## Detalhe por fluxo
(uma subseccao `###` por fluxo, cada uma com: um paragrafo de resumo, um bloco ```mermaid com `sequenceDiagram`, e uma lista numerada dos passos com o ficheiro:linha de cada passo)
## Tratamento de erros nos fluxos
## Operacoes assincronas e background
## Gaps / Open questions""",
        "investigate": """- segue um pedido/comando desde o entrypoint ate ao efeito final
- procura handlers, controllers, use cases, jobs, consumidores de filas""",
    },
    {
        "key": "architecture.data-model",
        "path": "02-architecture/data-model.md",
        "title": "Modelo de Dados",
        "section": "2. Arquitetura",
        "order": 240,
        "summary": "Entidades, esquemas, persistencia e migracoes.",
        "goal": "Document the data model: entities, their fields and relationships, storage technology and migrations.",
        "outline": """## Tecnologia de persistencia
## Entidades
(tabela: Entidade | Definida em | Descricao)
## Diagrama de relacoes
(um bloco ```mermaid com `erDiagram`; se nao existir modelo relacional, usa `classDiagram` para as estruturas de dados principais)
## Campos por entidade
(uma subseccao `###` por entidade com tabela: Campo | Tipo | Restricoes | Notas)
## Migracoes e evolucao do esquema
## Gaps / Open questions""",
        "investigate": """- modelos ORM, structs, dataclasses, schemas, ficheiros .sql, migracoes
- se nao houver base de dados, documenta as estruturas de dados em memoria e formatos serializados""",
    },
    {
        "key": "architecture.integrations",
        "path": "02-architecture/integrations.md",
        "title": "Integracoes Externas",
        "section": "2. Arquitetura",
        "order": 250,
        "summary": "APIs, servicos, filas e dependencias externas em runtime.",
        "goal": "List every external system this project talks to at runtime and how the integration is implemented.",
        "outline": """## Inventario de integracoes
(tabela: Sistema externo | Direcao (in/out) | Protocolo | Implementado em | Credenciais)
## Detalhe por integracao
(uma subseccao `###` por integracao: para que serve, contrato, autenticacao, tratamento de erros e retries)
## Interfaces expostas por este projeto
(tabela: Interface | Metodo/tipo | Declarada em (ficheiro:linha) | O que faz)
OBRIGATORIO: cada linha desta tabela corresponde a uma declaracao que LESTE. Abre os
ficheiros de rotas/handlers e copia os caminhos tal como estao no codigo, incluindo o
prefixo com que o router e montado. Se nao encontraste declaracoes de rotas, escreve
que nao encontraste — nao preenchas a tabela com endpoints plausiveis.
## Gaps / Open questions""",
        "investigate": """- clientes HTTP, SDKs, drivers de base de dados, produtores/consumidores de filas
- variaveis de ambiente com URLs, hosts, chaves
- LE os ficheiros de rotas na integra e ve tambem onde o router e montado (o prefixo
  final de um endpoint costuma vir do sitio onde e registado, nao do ficheiro da rota)
- confirma cada endpoint com um Grep pelo caminho literal antes de o escrever""",
    },
    {
        "key": "architecture.cross-cutting",
        "path": "02-architecture/cross-cutting.md",
        "title": "Preocupacoes Transversais",
        "section": "2. Arquitetura",
        "order": 260,
        "summary": "Configuracao, erros, logging, seguranca, concorrencia e performance.",
        "goal": "Document the cross-cutting mechanisms: configuration, error handling, logging, auth, security, concurrency, performance.",
        "outline": """## Configuracao
## Tratamento de erros
## Logging e observabilidade
## Autenticacao e autorizacao
## Seguranca
(gestao de segredos, validacao de input, superficies de risco observadas)
## Concorrencia e paralelismo
## Performance e caching
## Gaps / Open questions""",
        "investigate": """- middleware, decorators, interceptors, wrappers de excecoes
- configuracao de logging, metricas, tracing
- leitura de variaveis de ambiente e de ficheiros de configuracao""",
    },
    {
        "key": "architecture.decisions",
        "path": "02-architecture/decisions.md",
        "title": "Decisoes de Desenho",
        "section": "2. Arquitetura",
        "order": 270,
        "summary": "Decisoes arquiteturais inferidas, alternativas e trade-offs.",
        "goal": "Surface the significant design decisions visible in the code, with their trade-offs. Infer, but label inference as such.",
        "outline": """## Decisoes significativas
(uma subseccao `###` por decisao, no formato: **Contexto** / **Decisao** / **Evidencia no codigo** / **Trade-offs** / **Confianca** (alta se documentada, media se so inferida do codigo))
## Padroes recorrentes
## Divida tecnica observavel
(TODOs, FIXMEs, duplicacao, workarounds — com ficheiro:linha)
## Gaps / Open questions""",
        "investigate": """- ADRs ou notas de desenho em `docs/`
- comentarios que explicam "porque"
- TODO/FIXME/HACK/XXX no codigo""",
    },
    {
        "key": "guides.getting-started",
        "path": "05-guides/getting-started.md",
        "title": "Primeiros Passos",
        "section": "5. Guias",
        "order": 510,
        "summary": "Instalar, configurar e correr o projeto localmente.",
        "goal": "Give a working local setup path: prerequisites, install, configure, run, verify.",
        "outline": """## Pre-requisitos
(tabela: Requisito | Versao | Como verificar)
## Instalacao
(passos numerados com blocos de comandos reais deste repositorio)
## Configuracao minima
(variaveis/ficheiros obrigatorios para arrancar)
## Correr o projeto
## Verificar que funciona
## Problemas comuns no arranque
## Gaps / Open questions""",
        "investigate": """- README, Makefile, scripts do package.json, docker-compose
- ficheiros .env.example
- so documenta comandos que existam mesmo no repositorio""",
    },
    {
        "key": "guides.development",
        "path": "05-guides/development.md",
        "title": "Fluxo de Desenvolvimento",
        "section": "5. Guias",
        "order": 520,
        "summary": "Como desenvolver, testar e validar alteracoes.",
        "goal": "Describe the day-to-day development workflow: code layout conventions, tests, linting, and the local feedback loop.",
        "outline": """## Ciclo de desenvolvimento
## Comandos uteis
(tabela: Objetivo | Comando | Onde esta definido)
## Testes
(estrutura, como correr, como escrever um novo, cobertura)
## Qualidade de codigo
(linters, formatters, type checking, hooks de pre-commit)
## Convencoes de codigo
(observadas no codigo existente, nao inventadas)
## Como adicionar uma funcionalidade nova
(passos concretos com os ficheiros tipicamente tocados)
## Gaps / Open questions""",
        "investigate": """- configuracao dos test runners e ficheiros de teste existentes
- configuracao de linters/formatters
- workflows de CI para perceber o que e validado""",
    },
    {
        "key": "operations.configuration",
        "path": "06-operations/configuration.md",
        "title": "Configuracao",
        "section": "6. Operacao",
        "order": 610,
        "summary": "Variaveis de ambiente, ficheiros de configuracao e defaults.",
        "goal": "Produce a complete configuration reference: every env var and config option the code actually reads.",
        "outline": """## Fontes de configuracao
(precedencia entre defaults, ficheiros, ambiente, flags)
## Variaveis de ambiente
(tabela: Variavel | Obrigatoria | Default | Descricao | Lida em (ficheiro:linha))
OBRIGATORIO: so entra na tabela uma variavel que encontraste a ser lida no codigo. O
nome tem de ser copiado tal e qual. Uma variavel que aparece so num `.env.example` mas
que nada le e uma linha diferente: assinala-a como nao utilizada.
## Ficheiros de configuracao
(tabela: Ficheiro | Formato | Conteudo | Usado por)
## Flags de linha de comandos
## Segredos
(quais sao sensiveis e como sao fornecidos — nunca incluas valores reais)
## Gaps / Open questions""",
        "investigate": """- procura por leituras de ambiente (os.environ, process.env, os.Getenv, System.getenv, ...)
- .env.example, ficheiros de settings/config
- parsers de argumentos CLI""",
    },
    {
        "key": "operations.deployment",
        "path": "06-operations/deployment.md",
        "title": "Build e Deployment",
        "section": "6. Operacao",
        "order": 620,
        "summary": "Como se constroi, empacota e faz deploy do projeto.",
        "goal": "Document how the project is built, packaged, released and deployed, based on CI config and container/infra files.",
        "outline": """## Artefactos produzidos
## Processo de build
## Pipeline de CI/CD
(tabela: Workflow/job | Trigger | O que faz | Ficheiro)
## Containerizacao
(imagens, multi-stage, portas, volumes — se aplicavel)
## Ambientes e promocao
## Rollback
## Gaps / Open questions""",
        "investigate": """- `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`, ficheiros de deploy
- `Dockerfile`, `docker-compose`, manifestos k8s/helm/terraform
- scripts de release e configuracao de packaging""",
    },
    {
        "key": "operations.observability",
        "path": "06-operations/observability.md",
        "title": "Observabilidade e Troubleshooting",
        "section": "6. Operacao",
        "order": 630,
        "summary": "Logs, metricas, health checks e diagnostico de problemas.",
        "goal": "Document what the system emits at runtime and how to diagnose it when it misbehaves.",
        "outline": """## Logs
(formato, destinos, niveis, eventos mais uteis)
## Metricas e tracing
## Health checks e readiness
## Modos de falha conhecidos
(tabela: Sintoma | Causa provavel | Onde verificar | Mitigacao)
## Diagnostico passo a passo
## Gaps / Open questions""",
        "investigate": """- configuracao e chamadas de logging
- endpoints de health/readiness, exporters de metricas
- blocos de tratamento de excecoes e o que registam""",
    },
]


def build_core_prompt(
    page: dict, scan: RepoScan, config: WikiConfig, graph_context: str = "",
    page_index: str = "",
) -> str:
    # O grafo de cartografia so ajuda nas paginas que falam de estrutura/fluxos.
    uses_graph = page["key"].startswith(("architecture.", "overview.repository"))
    return _page_prompt(
        config=config,
        context=repo_context(scan, config),
        extra_context=(graph_context if uses_graph else "") + page_index,
        title=page["title"],
        goal=page["goal"],
        outline=page["outline"],
        investigate=page["investigate"],
    )


# ----------------------------------------------------------------------
CARTOGRAPHY_PAGE = {
    "key": "cartography.reading-the-map",
    "path": "07-cartography/reading-the-map.md",
    "title": "Cartografia — Como Ler o Mapa",
    "section": "7. Cartografia do Codigo",
    "order": 710,
    "summary": "Leitura do grafo de dependencias: hubs, camadas, ciclos e riscos.",
    "goal": (
        "Interpret the pre-computed static dependency graph: explain the shape of the "
        "codebase, which files are load-bearing, where the layering holds or breaks, and "
        "what the graph implies for anyone changing this code."
    ),
    "outline": """## Forma do grafo
(o que a topologia revela: em camadas? em estrela a volta de um hub? sem estrutura?)
## Camadas observadas
(tabela: Camada | Ficheiros representativos | Depende de | E dependida por)
## Ficheiros criticos
(tabela: Ficheiro | Porque e critico | Raio de impacto de uma alteracao)
## Pontos de entrada
## Ciclos e violacoes de camada
(cada ciclo do grafo: porque existe, e o que custaria quebra-lo)
## Ficheiros isolados
(para cada um: codigo morto, carregamento dinamico, ou entrypoint?)
## Riscos de manutencao
## Gaps / Open questions""",
    "investigate": """- o grafo em `<code_cartography>` ja e verdade verificada: NAO inventes arestas novas
- le os ficheiros hub e os envolvidos em ciclos para explicar o *porque* de cada ligacao
- confirma se cada ficheiro isolado e mesmo codigo morto antes de o classificar como tal""",
}


def build_cartography_prompt(
    scan: RepoScan, config: WikiConfig, graph_context: str, page_index: str = ""
) -> str:
    return _page_prompt(
        config=config,
        context=repo_context(scan, config, tree_entries=120),
        extra_context=graph_context + page_index,
        title=CARTOGRAPHY_PAGE["title"],
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
        files += f"\n- ... (+{module.file_count - 80} ficheiros)"

    outline = """## Responsabilidade
## Ficheiros
(tabela: Ficheiro | Papel | Linhas)
## Interface publica
(o que este modulo expoe a outros modulos: funcoes, classes, tipos, rotas, comandos)
## Dependencias
(tabela: Depende de | Tipo (interno/externo) | Para que)
## Diagrama interno
(um bloco ```mermaid com `flowchart LR` mostrando os ficheiros/tipos principais e as relacoes entre eles)
## Fluxos importantes
## Pontos de atencao
(armadilhas, estado partilhado, invariantes, TODOs)
## Gaps / Open questions"""

    investigate = f"""- le os ficheiros deste modulo, comecando pelos maiores e pelos `__init__`/`index`/`mod`
- usa o bloco `<code_cartography>` para preencher a seccao de dependencias com arestas reais
- procura quem importa este modulo (`Grep` pelo nome do modulo) para inferir a interface publica
- ficheiros deste modulo:
{files}"""

    return _page_prompt(
        config=config,
        context=repo_context(scan, config, tree_entries=120),
        extra_context=graph_context + page_index,
        title=f"Modulo: {module.key}",
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
    part_note = f" (parte {part} de {total_parts})" if total_parts > 1 else ""

    outline = f"""## Ambito
(uma frase mais a lista dos ficheiros cobertos nesta pagina)

Depois, exatamente estas {len(files)} seccoes `##`, por esta ordem e com este
texto exato — nenhuma pode faltar:

{headings}

Cada uma dessas seccoes de ficheiro leva:

### Proposito
(1-2 frases)

### Simbolos
(tabela: Simbolo | Tipo (classe/funcao/const/tipo) | Assinatura | Descricao)

### Detalhe
(uma subseccao `#### <nome do simbolo>` por simbolo publico nao-trivial, com:
assinatura num bloco de codigo, parametros, retorno, excecoes/erros, efeitos
secundarios, e uma nota de utilizacao quando ajude)

### Dependencias
(imports internos e externos relevantes)

Termina a pagina com:

## Gaps / Open questions"""

    investigate = f"""- LE INTEGRALMENTE cada um dos {len(files)} ficheiros seguintes antes de escrever
- transcreve as assinaturas exatamente como aparecem no codigo (tipos incluidos)
- documenta simbolos privados/internos apenas quando forem centrais para perceber o ficheiro
- COBERTURA e o criterio de sucesso desta pagina: e melhor uma seccao curta por
  ficheiro do que seccoes longas com ficheiros em falta
- ficheiros a cobrir nesta pagina:
{file_list}"""

    return _page_prompt(
        config=config,
        context=repo_context(scan, config, tree_entries=80),
        extra_context=page_index,
        title=f"Referencia: {module.key}{part_note}",
        goal=(
            "Produce low-level API reference documentation for the listed files: every "
            "public symbol with its exact signature, parameters, return value and side effects."
        ),
        outline=outline,
        investigate=investigate,
    )
