"""Wrapper assincrono sobre o CLI `claude` em modo headless (-p).

Usa a subscricao ja autenticada no CLI: nao le nem exige ANTHROPIC_API_KEY.
O prompt vai por stdin (evita limites de argv) e a resposta vem em JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass

from .config import WikiConfig


class ClaudeError(RuntimeError):
    """Falha ao invocar o CLI ou resposta invalida."""


@dataclass
class ClaudeResponse:
    text: str
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    session_id: str = ""


def ensure_cli_available(config: WikiConfig) -> str:
    path = shutil.which(config.claude_bin)
    if not path:
        raise ClaudeError(
            f"CLI '{config.claude_bin}' nao encontrado no PATH. "
            "Instala o Claude Code e autentica com a tua subscricao (`claude auth`)."
        )
    return path


class ClaudeRunner:
    """Executa prompts contra o CLI, com concorrencia limitada e retries."""

    def __init__(self, config: WikiConfig) -> None:
        self.config = config
        self.binary = ensure_cli_available(config)
        self._semaphore = asyncio.Semaphore(max(1, config.concurrency))
        self.total_cost_usd = 0.0
        self.total_calls = 0

    # ------------------------------------------------------------------
    def _build_argv(self, system_prompt: str) -> list[str]:
        config = self.config
        argv = [
            self.binary,
            "--print",
            "--output-format", "json",
            "--model", config.model,
            "--permission-mode", config.permission_mode,
            "--no-session-persistence",
        ]
        if config.tools:
            argv += ["--tools", ",".join(config.tools)]
        if system_prompt:
            argv += ["--append-system-prompt", system_prompt]
        if config.fallback_model:
            argv += ["--fallback-model", config.fallback_model]
        if config.max_budget_usd is not None:
            argv += ["--max-budget-usd", str(config.max_budget_usd)]
        if config.isolated:
            # Sem servidores MCP e sem skills: geracao deterministica e mais barata.
            argv += ["--strict-mcp-config", "--disable-slash-commands"]
        return argv

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Marcador util para hooks/telemetria do lado do utilizador.
        env["WIKI_GENERATOR"] = "1"
        return env

    # ------------------------------------------------------------------
    async def run(
        self, prompt: str, system_prompt: str = "", log_name: str | None = None
    ) -> ClaudeResponse:
        """Corre um prompt e devolve o texto final. Faz retry em falhas transitorias."""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2 ** attempt, 30))
            try:
                async with self._semaphore:
                    return await self._run_once(
                        prompt, system_prompt, log_name, attempt
                    )
            except ClaudeError as exc:
                last_error = exc
                if not _is_retryable(str(exc)):
                    raise
        raise ClaudeError(
            f"Falhou apos {self.config.max_retries + 1} tentativas: {last_error}"
        )

    async def _run_once(
        self,
        prompt: str,
        system_prompt: str,
        log_name: str | None = None,
        attempt: int = 0,
    ) -> ClaudeResponse:
        argv = self._build_argv(system_prompt)
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.config.repo_path),
            env=self._env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=self.config.timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ClaudeError(f"Timeout apos {self.config.timeout}s") from None

        stdout_text = stdout.decode("utf-8", "replace")
        stderr_text = stderr.decode("utf-8", "replace").strip()
        self._write_log(log_name, attempt, argv, prompt, system_prompt,
                        stdout_text, stderr_text, process.returncode)
        if process.returncode != 0:
            raise ClaudeError(
                f"claude saiu com codigo {process.returncode}: {stderr_text[:800] or '(sem stderr)'}"
            )

        response = _parse_json_output(stdout_text)
        self.total_cost_usd += response.cost_usd
        self.total_calls += 1
        return response


    # ------------------------------------------------------------------
    def _write_log(
        self, log_name: str | None, attempt: int, argv: list[str], prompt: str,
        system_prompt: str, stdout_text: str, stderr_text: str, returncode: int | None,
    ) -> None:
        """Grava a chamada completa quando --log-dir esta ligado.

        Guarda o que foi enviado e o que voltou, em bruto: sem isto, uma pagina
        que sai errada nao tem forma de ser investigada depois.
        """
        if not self.config.log_dir or not log_name:
            return
        try:
            directory = self.config.log_dir / self.config.repo_path.name
            directory.mkdir(parents=True, exist_ok=True)
            suffix = f".retry{attempt}" if attempt else ""
            safe = log_name.replace("/", "_")
            (directory / f"{safe}{suffix}.json").write_text(
                json.dumps(
                    {
                        "page": log_name,
                        "attempt": attempt,
                        "returncode": returncode,
                        # argv sem o binario, para nao gravar caminhos da maquina
                        "argv": argv[1:],
                        "system_prompt": system_prompt,
                        "prompt": prompt,
                        "stdout": stdout_text,
                        "stderr": stderr_text,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # o log e um extra de debug: nunca deve derrubar a geracao


# ----------------------------------------------------------------------
def _parse_json_output(raw: str) -> ClaudeResponse:
    raw = raw.strip()
    if not raw:
        raise ClaudeError("O CLI devolveu stdout vazio.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: o CLI pode ter escrito ruido antes do JSON — tenta a ultima linha.
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            raise ClaudeError(f"Saida nao-JSON do CLI: {raw[:500]}") from None

    if isinstance(payload, dict) and payload.get("is_error"):
        raise ClaudeError(f"Erro reportado pelo CLI: {payload.get('result', payload)}")

    text = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ClaudeError("Resposta sem campo 'result' utilizavel.")

    return ClaudeResponse(
        text=text,
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        duration_ms=int(payload.get("duration_ms") or 0),
        num_turns=int(payload.get("num_turns") or 0),
        session_id=str(payload.get("session_id") or ""),
    )


_RETRYABLE_MARKERS = (
    "timeout", "overloaded", "rate limit", "rate_limit", "529", "503", "502",
    "connection", "temporarily", "econnreset", "socket hang up",
)


def _is_retryable(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _RETRYABLE_MARKERS)
