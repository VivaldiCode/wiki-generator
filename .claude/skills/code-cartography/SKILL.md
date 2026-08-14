---
name: code-cartography
description: Mapeia a topologia de um repositorio — que ficheiro importa qual, hubs, camadas, ciclos e codigo isolado — e entrega um grafo Mermaid de ligacoes. Usar quando o pedido envolver "mapa do codigo", "grafo de dependencias", "quem importa o que", "arquitetura a partir dos imports", "codigo morto", "ciclos de dependencia", ou ao documentar um repositorio desconhecido.
---

# Code Cartography

Produzir um mapa **verificado** de um repositorio: os ficheiros como nos, os
`import`/`require`/`include` como arestas. O produto final e sempre um grafo
Mermaid mais a leitura desse grafo.

## Principio central

**Arestas inventadas destroem o valor de um mapa.** Uma unica ligacao errada faz
com que ninguem volte a confiar no diagrama. Por isso:

- Toda a aresta tem de vir de uma linha de import concreta que foste ler.
- Se nao conseguires resolver um import para um ficheiro do repositorio,
  classifica-o como dependencia externa ou como nao resolvido — nao adivinhes o alvo.
- Preferir extracao deterministica (parser/regex sobre o codigo) a inferencia.
  Neste projeto, `wiki_generator/cartography.py` faz exatamente isso e deve ser a
  fonte do grafo sempre que estiver disponivel.

## Procedimento

1. **Inventariar** os ficheiros de codigo (respeitando `.gitignore`; excluir
   vendored, build, node_modules, ficheiros gerados).
2. **Extrair** os especificadores de import de cada ficheiro, por linguagem:
   - Python: `import x`, `from x import`, imports relativos com pontos
   - JS/TS: `import ... from`, `export ... from`, `require()`, `import()`
   - Go: blocos `import`, resolvidos contra o `module` do `go.mod`
   - Java/Kotlin: `import a.b.C` → `a/b/C.java`
   - C/C++: `#include "x"` (relativo) e `<x>` (externo)
   - Rust: `mod x;`, `use crate::a::b`
   - Ruby: `require_relative`, `require`
   - PHP: `use A\B;`, `require`/`include`
3. **Resolver** cada especificador para um caminho real: caminhos relativos
   normalizados contra o diretorio do ficheiro; caminhos de pacote contra as
   raizes de codigo (`src/`, `lib/`, `app/`, `pkg/`, ...); tentar extensoes e
   ficheiros de indice (`index.ts`, `__init__.py`, `mod.rs`).
4. **Calcular** as metricas que dao significado ao mapa:
   - grau de entrada / saida por ficheiro → **hubs**
   - ficheiros sem arestas → **isolados** (codigo morto? carregamento dinamico?)
   - ficheiros que importam mas nao sao importados → **entrypoints**
   - ciclos dirigidos → **violacoes de camada**
   - agregacao por diretorio → **acoplamento entre modulos**
5. **Desenhar** o Mermaid.
6. **Interpretar**: a topologia so e util com a leitura por cima.

## Regras do diagrama

- `flowchart LR`, uma aresta `A --> B` por import, com o sentido **A importa B**.
- Agrupar por modulo em `subgraph` com o caminho do diretorio como titulo.
- IDs dos nos derivados do caminho e sanitizados (so `[A-Za-z0-9_]`); o caminho
  real vai na etiqueta, entre aspas.
- Nunca deixar parenteses, `/`, `:` ou `-` numa etiqueta sem aspas — parte o parser.
- Acima de ~140 nos, um diagrama unico deixa de ser legivel: emitir o grafo
  completo num ficheiro `.mmd`/`.json` a parte, e no documento apresentar a vista
  agregada por modulo mais um diagrama por modulo (incluindo os vizinhos diretos,
  para nao cortar arestas inter-modulo). A cobertura tem de continuar a ser total.

## Entregaveis

Sempre os quatro:

1. `file-graph.md` — o grafo de ficheiros, a tabela **importa / importado por**
   para *todos* os ficheiros, hubs, ciclos, isolados, externos.
2. `module-graph.md` — o grafo agregado por modulo e a matriz de acoplamento.
3. `file-graph.mmd` + `graph.json` — o grafo completo, sem truncagem, para
   ferramentas externas.
4. Uma leitura em prosa: forma do grafo, camadas, ficheiros criticos e o raio de
   impacto de os alterar, ciclos e o que custaria quebra-los.

## Armadilhas

- **Pacotes Go** sao diretorios, nao ficheiros: um import aponta para o pacote
  inteiro. Liga-o a um ficheiro representativo e diz que o fizeste.
- **Namespaces C#** e **aliases de bundler** (`@/components`, `~/lib`) nao mapeiam
  para caminhos de forma fiavel sem ler `tsconfig.json`/`jsconfig.json`. Marcar
  como nao resolvido e melhor do que ligar ao ficheiro errado.
- **Injecao de dependencias e registos dinamicos** produzem ligacoes reais que
  nenhum import revela. Um ficheiro "isolado" pode estar a ser carregado por
  reflexao — verificar antes de lhe chamar codigo morto.
- **Monorepos**: resolver dentro de cada pacote antes de resolver na raiz.
