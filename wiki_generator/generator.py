"""Orquestracao: cache incremental + execucao concorrente + escrita das paginas."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import STRUCTURE_VERSION, __version__
from .claude_client import ClaudeError, ClaudeRunner
from .config import WikiConfig
from .models import PageResult, PageSpec, RepoScan
from .prompts import system_prompt
from .utils import (
    dedupe_leading_heading,
    ensure_heading,
    markdown_links_to_wikilinks,
    sha1_text,
    strip_code_fence,
    trim_preamble,
    wikilink,
)

MANIFEST_NAME = ".wiki-manifest.json"


@dataclass
class GenerationReport:
    results: list[PageResult]
    total_cost_usd: float
    elapsed_s: float

    @property
    def generated(self) -> list[PageResult]:
        return [r for r in self.results if r.status == "generated"]

    @property
    def cached(self) -> list[PageResult]:
        return [r for r in self.results if r.status == "cached"]

    @property
    def failed(self) -> list[PageResult]:
        return [r for r in self.results if r.status == "failed"]


# ----------------------------------------------------------------------
class Manifest:
    """Guarda o fingerprint de cada pagina para permitir regeracao incremental."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {"version": __version__, "pages": {}}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded.get("pages"), dict):
                    self.data = loaded
            except (OSError, json.JSONDecodeError):
                pass

    def get(self, key: str) -> str | None:
        entry = self.data["pages"].get(key)
        return entry.get("fingerprint") if isinstance(entry, dict) else None

    def set(self, spec: PageSpec, fingerprint: str) -> None:
        self.data["pages"][spec.key] = {
            "fingerprint": fingerprint,
            "path": spec.path,
            "title": spec.title,
        }

    def prune(self, valid_keys: set[str]) -> None:
        self.data["pages"] = {
            k: v for k, v in self.data["pages"].items() if k in valid_keys
        }

    def save(self) -> None:
        self.data["version"] = __version__
        self.data["structure_version"] = STRUCTURE_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _fingerprint(spec: PageSpec, scan: RepoScan, config: WikiConfig) -> str:
    hashes = {f.rel_path: f.content_hash for f in scan.files}
    scoped = [f"{path}:{hashes.get(path, 'missing')}" for path in sorted(spec.scope_files)]
    payload = json.dumps(
        {
            "structure": STRUCTURE_VERSION,
            "key": spec.key,
            "prompt": sha1_text(spec.prompt),
            "config": config.fingerprint_fields(),
            "files": scoped,
        },
        sort_keys=True,
    )
    return sha1_text(payload)


# ----------------------------------------------------------------------
class WikiGenerator:
    def __init__(self, config: WikiConfig, scan: RepoScan) -> None:
        self.config = config
        self.scan = scan
        self.manifest = Manifest(config.output_path / MANIFEST_NAME)
        self.system_prompt = system_prompt(config)
        self._printed = 0
        self._done = 0
        self._in_flight = 0
        self._total = 0
        self._started_at = 0.0

    # ------------------------------------------------------------------
    async def generate(self, specs: list[PageSpec]) -> GenerationReport:
        started = time.monotonic()
        self._started_at = started
        runner = ClaudeRunner(self.config)
        self._total = len(specs)

        # Numa corrida de dezenas de minutos, saber so o que ja acabou nao chega:
        # esta tarefa mostra o ritmo e o tempo restante enquanto se espera.
        ticker = asyncio.create_task(self._progress_ticker())
        try:
            tasks = [self._generate_page(runner, spec) for spec in specs]
            results = await asyncio.gather(*tasks)
        finally:
            ticker.cancel()

        self.manifest.prune({spec.key for spec in specs} | set(self.manifest.data["pages"]))
        self.manifest.save()

        return GenerationReport(
            results=list(results),
            total_cost_usd=runner.total_cost_usd,
            elapsed_s=time.monotonic() - started,
        )

    # ------------------------------------------------------------------
    async def _progress_ticker(self, every: float = 20.0) -> None:
        try:
            while True:
                await asyncio.sleep(every)
                if self._done >= self._total:
                    return
                elapsed = time.monotonic() - self._started_at
                rate = self._done / elapsed if elapsed > 0 and self._done else 0
                eta = f"~{(self._total - self._done) / rate / 60:.0f}m" if rate else "?"
                print(
                    f"    ... {self._done}/{self._total} concluidas | "
                    f"{self._in_flight} em curso | {elapsed / 60:.0f}m decorridos | "
                    f"ETA {eta}",
                    flush=True,
                )
        except asyncio.CancelledError:
            return

    async def _generate_page(self, runner: ClaudeRunner, spec: PageSpec) -> PageResult:
        target = self.config.output_path / spec.path
        fingerprint = _fingerprint(spec, self.scan, self.config)

        if (
            not self.config.force
            and target.is_file()
            and self.manifest.get(spec.key) == fingerprint
        ):
            self._report(spec, "cached")
            return PageResult(spec=spec, status="cached", markdown=target.read_text("utf-8"))

        if self.config.verbose:
            print(f"    -> a gerar {spec.path}", flush=True)
        self._in_flight += 1
        try:
            response = await runner.run(spec.prompt, self.system_prompt, spec.key)
            markdown = self._clean(response.text, spec)

            # Modelos pequenos por vezes deixam ficheiros de fora numa pagina densa.
            # Verificar e exigir explicitamente o que falta e mais fiavel do que
            # confiar que o outline foi seguido.
            missing = [m for m in spec.required_markers if m not in markdown]
            if missing:
                retry_prompt = (
                    f"{spec.prompt}\n\n<coverage_failure>\n"
                    "A tua tentativa anterior omitiu estes itens obrigatorios:\n"
                    + "\n".join(f"- {item}" for item in missing)
                    + "\n\nEscreve a pagina completa de novo. Cada item acima TEM de "
                    "ter a sua propria seccao, com o texto exato indicado. Nao omitas "
                    "nenhum, nem os que ja tinhas coberto.\n</coverage_failure>"
                )
                retry = await runner.run(
                    retry_prompt, self.system_prompt, f'{spec.key}.cobertura'
                )
                retried = self._clean(retry.text, spec)
                still_missing = [m for m in spec.required_markers if m not in retried]
                if len(still_missing) < len(missing):
                    markdown, response = retried, retry
                    missing = still_missing
                if missing:
                    markdown += (
                        "\n\n> **Aviso de cobertura:** esta pagina nao documenta "
                        + ", ".join(f"`{m.lstrip('# ')}`" for m in missing)
                        + ". Consulta o codigo diretamente para esses ficheiros.\n"
                    )
        except ClaudeError as exc:
            self._report(spec, "failed", str(exc))
            return PageResult(spec=spec, status="failed", error=str(exc))
        finally:
            self._in_flight -= 1

        markdown = self._add_frontmatter(spec, markdown)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        self.manifest.set(spec, fingerprint)

        self._report(spec, "generated")
        return PageResult(
            spec=spec,
            status="generated",
            markdown=markdown,
            cost_usd=response.cost_usd,
            duration_ms=response.duration_ms,
            num_turns=response.num_turns,
        )

    # ------------------------------------------------------------------
    def _clean(self, raw: str, spec: PageSpec) -> str:
        markdown = trim_preamble(strip_code_fence(raw))
        return dedupe_leading_heading(ensure_heading(markdown, spec.title))

    def _add_frontmatter(self, spec: PageSpec, markdown: str) -> str:
        # Links entre paginas da wiki sao wikilinks do Obsidian, resolvidos a
        # partir da raiz do vault — nao caminhos relativos.
        base_dir = spec.path.rsplit("/", 1)[0] if "/" in spec.path else ""
        markdown = markdown_links_to_wikilinks(markdown, base_dir)
        footer = (
            "\n\n---\n\n"
            f"{wikilink('README', '<- Indice da wiki')}  \n"
            f"<sub>Gerada por wiki-generator ({self.config.model}) a partir de "
            f"{len(spec.scope_files)} ficheiro(s) de `{self.config.resolved_project_name}`. "
            "Nao editar a mao: as alteracoes sao perdidas na proxima geracao.</sub>\n"
        )
        return markdown.rstrip() + footer

    def _report(self, spec: PageSpec, status: str, detail: str = "") -> None:
        self._printed += 1
        self._done += 1
        icon = {"generated": "+", "cached": "=", "failed": "!"}.get(status, "?")
        line = f"[{self._printed}/{self._total}] {icon} {spec.path}"
        if detail:
            line += f"  -> {detail[:160]}"
        stream = sys.stderr if status == "failed" else sys.stdout
        print(line, file=stream, flush=True)
