# wiki-generator

Gera uma **wiki de engenharia completa e padronizada** a partir de qualquer repositório,
usando o **Claude Code em modo headless** — com a subscrição já autenticada no CLI, sem
`ANTHROPIC_API_KEY` e sem faturação por API.

A saída é **markdown puro com wikilinks do Obsidian**: abre-se como vault, sem
ferramentas extra.

```bash
wiki-generator --source ~/code/meu-projeto
```

---

## Índice

- [Instalação](#instalação)
- [Guia de uso](#guia-de-uso)
- [O que produz](#o-que-produz)
- [Cartografia do código](#cartografia-do-código)
- [Verificações automáticas](#verificações-automáticas)
- [Escolher o modelo](#escolher-o-modelo)
- [Referência de opções](#referência-de-opções)
- [Como funciona](#como-funciona)
- [Limitações](#limitações)

---

## Instalação

Requisitos: **Python 3.10+** e o [Claude Code](https://claude.com/claude-code) instalado
e autenticado.

```bash
claude auth          # se ainda não estiver autenticado
git clone https://github.com/VivaldiCode/wiki-generator.git
cd wiki-generator
pip install -e .
```

Confirma:

```bash
wiki-generator --version
```

---

## Guia de uso

### 1. Um repositório

```bash
wiki-generator --source ~/code/meu-projeto
```

A wiki fica em `~/code/meu-projeto/wiki/`.

### 2. Escolher onde a wiki fica

```bash
wiki-generator --source ~/code/meu-projeto --output ~/wikis
```

Com `--output`, cada repositório recebe **uma subpasta com o seu nome**:

```
~/wikis/
└── meu-projeto/
    ├── README.md
    ├── 01-overview/
    └── ...
```

### 3. Vários repositórios de uma vez

Aponta para uma pasta que contenha vários repositórios git. Todos são descobertos e
processados, **uma wiki por repositório** — nunca uma wiki única a misturar projetos
independentes (arquitetura, stack e glossário partilhados não descreveriam nenhum deles).

```bash
wiki-generator --source ~/code --output ~/wikis
```

```
Detetados 4 repositorios git em /Users/eu/code:
  - api-gateway          -> /Users/eu/wikis/api-gateway
  - web-app              -> /Users/eu/wikis/web-app
  - mobile               -> /Users/eu/wikis/mobile
  - infra                -> /Users/eu/wikis/infra
Uma wiki por repositorio. Usa --single para gerar uma so.
```

Sem `--output`, cada wiki fica dentro do respetivo repositório (`<repo>/wiki/`).

Para tratar a árvore toda como **um só** projeto (monorepo sem sub-repos git separados),
usa `--single`.

### 4. Ver o plano antes de gastar quota

```bash
wiki-generator --source ~/code/meu-projeto --dry-run
```

Lista todas as páginas que seriam geradas, sem chamar o modelo.

### 5. Acompanhar o progresso

O CLI reporta em tempo real. Cada página concluída aparece assim que fecha, e de 20 em
20 segundos surge uma linha de estado com ritmo e tempo estimado:

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

`+` gerada · `=` veio da cache · `!` falhou

Para ver também o arranque de cada página, usa `--verbose`.

### 6. Regeração incremental

Cada página guarda um *fingerprint* dos ficheiros que a originaram em
`.wiki-manifest.json`. Ao correr de novo, só as páginas cujos ficheiros de origem
mudaram são regeradas:

```bash
wiki-generator --source ~/code/meu-projeto        # segunda corrida: quase tudo em cache
wiki-generator --source ~/code/meu-projeto --force # ignora a cache
```

Mudar de modelo, de idioma ou de estrutura também invalida a cache automaticamente.

### 7. Regerar só uma parte

```bash
# uma página específica
wiki-generator --source . --only architecture.overview

# uma secção inteira
wiki-generator --source . --only architecture

# um tipo de página
wiki-generator --source . --only module --only reference
```

O índice continua a listar a wiki toda, não só o que foi regerado.

### 8. Guardar logs para debug

```bash
wiki-generator --source ~/code/meu-projeto --log-dir /tmp/wg-logs
```

Cada chamada ao Claude Code é gravada em `/tmp/wg-logs/<repo>/<pagina>.json` com o
prompt enviado, o system prompt, o stdout, o stderr e o código de saída. Retentativas
ficam com sufixo `.retryN`. Quando uma página sai errada, é aqui que se percebe porquê.

> Os logs contêm o prompt completo, que inclui excertos de manifestos e a árvore de
> ficheiros do teu repositório. Trata a pasta de logs com o mesmo cuidado que o código.

### 9. Repositórios grandes

```bash
wiki-generator --source . \
  --no-reference \                 # salta a referência de baixo nível
  --max-modules 15 \
  --exclude '**/generated/**' \
  --exclude '**/*.pb.go'
```

### 10. Idioma

```bash
wiki-generator --source . --language pt     # en (default), pt, pt-br, es, fr, de, it
```

### 11. Ficheiro de configuração

```bash
wiki-generator --config wiki.config.json
```

```json
{
  "repo_path": ".",
  "output_path": "./wiki",
  "model": "haiku",
  "language": "pt",
  "concurrency": 6,
  "max_modules": 20,
  "exclude_globs": ["**/generated/**", "**/migrations/**"]
}
```

Opções da linha de comandos sobrepõem-se ao ficheiro.

---

## O que produz

Uma estrutura fixa, igual em todos os repositórios — o que torna as wikis comparáveis
entre projetos e previsíveis para quem as lê:

```
wiki/
├── README.md                        índice + métricas do repositório
├── SUMMARY.md                       índice linear
│
├── 01-overview/                     ALTO NÍVEL
│   ├── introduction.md              o que é, que problema resolve
│   ├── tech-stack.md                linguagens, frameworks, tooling
│   ├── repository-structure.md      mapa de diretórios e convenções
│   └── glossary.md                  vocabulário de domínio
│
├── 02-architecture/                 ARQUITETURA
│   ├── overview.md                  estilo arquitetural + diagramas de contexto
│   ├── components.md                cada componente e as suas fronteiras
│   ├── data-flow.md                 fluxos ponta-a-ponta + sequence diagrams
│   ├── data-model.md                entidades, ER diagram, migrações
│   ├── integrations.md              APIs, filas, serviços externos
│   ├── cross-cutting.md             config, erros, logging, auth, segurança
│   └── decisions.md                 decisões de desenho, trade-offs, dívida técnica
│
├── 03-modules/<módulo>.md           MÉDIO NÍVEL — um por módulo
│
├── 04-reference/<módulo>.md         BAIXO NÍVEL — API por ficheiro:
│                                    símbolos, assinaturas, parâmetros, efeitos
│
├── 05-guides/
│   ├── getting-started.md
│   └── development.md
│
├── 06-operations/
│   ├── configuration.md             env vars e config que o código lê
│   ├── deployment.md                build, CI/CD, containers
│   └── observability.md             logs, métricas, troubleshooting
│
└── 07-cartography/                  CARTOGRAFIA DO CÓDIGO
    ├── file-graph.md                grafo Mermaid: que ficheiro importa qual
    ├── module-graph.md              grafo agregado + matriz de acoplamento
    ├── modules/<módulo>.md          detalhe por módulo (repositórios grandes)
    ├── file-graph.mmd               grafo completo, sem truncagem
    ├── graph.json                   grafo em JSON para ferramentas externas
    └── reading-the-map.md           leitura do grafo: hubs, camadas, ciclos
```

Cada página tem um **outline obrigatório** — mesmas secções, mesma ordem, em qualquer
repositório. É isso que torna a saída padronizada em vez de prosa livre.

Todas terminam com **"Gaps / Open questions"**: onde o modelo não conseguiu determinar
algo a partir do código, tem de o dizer em vez de inventar.

### Obsidian

A saída é um vault. Todos os links internos são wikilinks (`[[02-architecture/overview]]`),
resolvidos a partir da raiz e sem extensão `.md`. Basta abrir a pasta no Obsidian —
`README.md` é o índice, e o grafo do Obsidian dá uma segunda vista sobre a documentação.

Detalhes que só aparecem ao fazer isto a sério e que estão tratados: dentro de tabelas
markdown o `|` do alias é escapado (`[[destino\|texto]]`), e caminhos de ficheiros de
código continuam como código inline em vez de virarem links — não são notas.

---

## Cartografia do código

O grafo de dependências é construído por **análise estática determinística**
(`wiki_generator/cartography.py`), não pelo modelo — uma única aresta inventada
destruiria a confiança no diagrama.

Extrai `import` / `require` / `include` de Python, TypeScript/JavaScript, Go, Java,
Kotlin, Rust, C/C++, Ruby, PHP, Dart e Shell, resolve cada especificador para um
ficheiro real do repositório, e calcula:

- **hubs** — ficheiros com mais ligações (maior raio de impacto de uma alteração)
- **entrypoints** — importam mas ninguém os importa
- **ficheiros isolados** — sem ligações (código morto? carregamento dinâmico?)
- **ciclos de dependência** — violações de camada
- **acoplamento entre módulos**

Linguagens presentes no repositório mas sem extrator (Swift, C#, Elixir, SQL,
Terraform, …) são **assinaladas na página**: os seus ficheiros aparecem sem arestas, e a
wiki diz explicitamente que isso é limitação da ferramenta e não código morto.

### Cobertura total, páginas navegáveis

O diagrama cobre **todos** os ficheiros. Acima de ~140 nós um diagrama único deixa de
renderizar, por isso a cobertura é repartida sem ser reduzida:

- `file-graph.md` — métricas, vista agregada por módulo, índice das páginas de módulo
- `07-cartography/modules/<módulo>.md` — uma página por módulo, dividida em partes de
  180 ficheiros quando necessário
- `file-graph.mmd` / `graph.json` — o grafo integral, sem truncagem

**Navegação entre módulos.** No diagrama de cada módulo, ficheiros de outros módulos
aparecem a tracejado e são clicáveis (`click` do Mermaid). Cada página tem ainda uma
tabela *Módulos vizinhos* com o número de imports em cada direção. Dá para percorrer o
grafo módulo a módulo em vez de olhar para um diagrama só.

A metodologia está formalizada na skill reutilizável
[`.claude/skills/code-cartography/SKILL.md`](.claude/skills/code-cartography/SKILL.md).

---

## Verificações automáticas

Três verificações correm no fim de cada geração, todas determinísticas:

**Cobertura das páginas de referência.** Confirma que cada ficheiro do lote tem mesmo a
sua secção. Se faltar algum, repete a chamada listando explicitamente o que ficou de
fora; se ainda assim faltar, a página leva um aviso visível em vez de dar a omissão por
documentada.

**Wikilinks.** Um link partido aponta para uma nota que nunca vai existir. Links sem
destino são degradados para texto simples e reportados. A verificação ignora blocos de
código — `[[ -f x ]]` em bash não é um link. No fim há uma reverificação que lê de novo
do disco, para apanhar o caso de algo ter escrito depois da correção.

**Citações `ficheiro:linha`.** Cada citação é confrontada com o repositório e
classificada como **inválida** (ficheiro não existe, ou linha para lá do fim) ou **mal
enraizada** (o ficheiro existe, mas o caminho está relativo a um subdiretório). O que
isto **não** apanha — e não finge apanhar — é a citação desalinhada dentro do ficheiro.

---

## Escolher o modelo

O default é `haiku` porque é barato e rápido. Mas numa auditoria real — 7 repositórios
de um projeto de produção, 258 páginas, 440 afirmações confrontadas com o código por
revisores independentes com refutação adversarial — a diferença entre modelos não foi de
grau, foi de tipo.

Nas mesmas páginas analíticas, com prompts idênticos:

| | haiku | sonnet |
|---|---:|---:|
| Erros factuais confirmados | 28 | **20** |
| Taxa de erro | 1 em 15 afirmações | **1 em 25** |
| Endpoints / dependências / ficheiros inventados | a maioria | **0** |
| Erros de citação (linha errada) | poucos | quase todos |

O `haiku` inventa **conteúdo**: endpoints REST plausíveis que não existem no router,
dependências que não estão no manifesto, permissões que não estão no manifesto Android.
Um leitor acredita e age em cima disso.

O `sonnet` erra em **precisão de citação**: aponta `pubspec.yaml:62` quando é `:41`. O
leitor não encontra o que lhe foi prometido, mas não fica a acreditar em algo falso.

Endurecer os prompts resolveu o padrão que foi mirado com precisão — exigir
`ficheiro:linha` por linha nas tabelas de endpoints, e mandar ler onde o router é
*montado* — e cortou os endpoints inventados em 67%. Não conseguiu mais: **os erros de
conteúdo restantes são limite do modelo, não do prompt.** Exigir citações chegou a
converter afirmações vagas e infalsificáveis em afirmações precisas e erradas.

Recomendação prática — as páginas de referência e de módulo são transcrição mecânica e o
`haiku` chega bem; as analíticas carregam as afirmações sobre as quais alguém vai decidir:

```bash
wiki-generator --source . --model haiku                    # o grosso

wiki-generator --source . --model sonnet \
  --only overview.introduction --only overview.tech-stack \
  --only architecture.overview --only architecture.integrations \
  --only operations.configuration                          # as que pesam
```

Trata qualquer citação `ficheiro:linha` como ponteiro aproximado, em qualquer modelo: os
números de linha desviam-se com a edição do código e nenhum modelo os acerta de forma
fiável.

---

## Referência de opções

### Alvo

| Flag | Default | Descrição |
|---|---|---|
| `--source`, `-s` | `.` | Repositório, ou pasta com vários repositórios git |
| `--output`, `-o` | `<repo>/wiki` | Pasta de saída; cada repo recebe `<output>/<nome>/` |
| `--config`, `-c` | — | Ficheiro JSON de configuração |

`--repo`/`-r` e `--out` continuam a funcionar como aliases.

### Modelo

| Flag | Default | Descrição |
|---|---|---|
| `--model`, `-m` | `haiku` | Alias (`haiku`, `sonnet`, `opus`) ou nome completo |
| `--fallback-model` | — | Modelo de recurso se o principal não estiver disponível |
| `--concurrency`, `-j` | `4` | Páginas geradas em paralelo |
| `--timeout` | `600` | Timeout por página, em segundos |
| `--max-retries` | `2` | Tentativas extra em falhas transitórias |
| `--permission-mode` | `bypassPermissions` | Modo de permissões do CLI |
| `--claude-bin` | `claude` | Binário do Claude Code |
| `--log-dir` | — | Guardar as chamadas ao Claude Code para debug |

### Conteúdo

| Flag | Default | Descrição |
|---|---|---|
| `--language`, `-l` | `en` | `en`, `pt`, `pt-br`, `es`, `fr`, `de`, `it` |
| `--project-name` | nome do diretório | Nome do projeto na wiki |
| `--audience` | engenheiros do repositório | Público-alvo da documentação |

### Estrutura

| Flag | Default | Descrição |
|---|---|---|
| `--module-depth` | `2` | Profundidade de diretórios ao agrupar módulos |
| `--max-modules` | `25` | Tecto de módulos documentados |
| `--files-per-reference-page` | `6` | Ficheiros por página de referência |
| `--max-reference-pages` | `60` | Tecto de páginas de referência |
| `--no-reference` | — | Saltar a referência de baixo nível |
| `--no-cartography` | — | Saltar o grafo de dependências |
| `--single` | — | Tratar a árvore toda como um só repositório |
| `--include` / `--exclude` | — | Globs de ficheiros (repetíveis) |

### Comportamento

| Flag | Descrição |
|---|---|
| `--force`, `-f` | Ignorar a cache |
| `--dry-run` | Mostrar o plano e sair |
| `--only` | Chave, prefixo ou tipo de página (repetível) |
| `--verbose`, `-v` | Reportar também o arranque de cada página |

---

## Como funciona

1. **Scan** (`scanner.py`) — percorre o repositório respeitando o `.gitignore` (via
   `git ls-files`), classifica ficheiros por linguagem e agrupa-os em módulos. Sem modelo.
2. **Cartografia** (`cartography.py`) — grafo de dependências ficheiro-a-ficheiro por
   análise estática. Sem modelo.
3. **Plano** (`planner.py`) — a estrutura fixa vira uma lista de páginas, cada uma com o
   seu prompt e o seu conjunto de ficheiros de origem.
4. **Geração** (`generator.py`) — cada página é um `claude -p` isolado, com apenas
   `Read`/`Glob`/`Grep` disponíveis. O modelo lê o código real em vez de o receber no
   prompt, o que mantém o custo por página baixo mesmo em repositórios grandes.
5. **Montagem e verificação** (`assembler.py`, `links.py`, `citations.py`) — índice,
   sumário, validação de links e de citações.

Zero dependências além da biblioteca padrão do Python.

### Segurança

Ficheiros com aspeto de credenciais (`*service-account*.json`, `*-adminsdk-*.json`,
`*.pem`, `.env`, chaves SSH, …) são **excluídos do scan por omissão e reportados**, não
silenciados — o gerador dá a um modelo acesso de leitura ao repositório e escreve
documentação a partir dele, por isso nada deve apontar para segredos. `.env.example` e
afins continuam incluídos: são documentação de configuração, não segredos.

---

## Limitações

- A saída é gerada por um modelo. É um **mapa útil, não a fonte de verdade** — o código
  é que manda. As páginas de cartografia são a exceção: são determinísticas.
- Namespaces C#, aliases de bundler (`@/components`) e injeção de dependências não
  produzem arestas fiáveis; aparecem como não resolvidos em vez de arestas inventadas.
- Pacotes Go são diretórios, não ficheiros: um import liga ao ficheiro representativo.
- Não requer API key, mas consome a quota da tua subscrição do Claude Code.

---

## Licença

MIT
