"""Async wrapper around the `claude` CLI in headless mode (-p).

Uses the subscription already authenticated in the CLI: it neither reads nor
requires ANTHROPIC_API_KEY. The prompt goes over stdin (avoiding argv limits)
and the response comes back as JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass

from . import providers
from .config import WikiConfig


class ClaudeError(RuntimeError):
    """Failed to invoke the CLI, or the response was invalid."""


@dataclass
class CallOptions:
    """Per-call overrides. Anything left None falls back to the run configuration.

    Verification needs a different model, an extra tool, subagent definitions and a
    much longer timeout than page generation — but it must share the runner, because
    the semaphore and the cost counter are per-instance. Two runners would double the
    real concurrency and split the reported cost in half.
    """

    model: str | None = None
    tools: tuple[str, ...] | None = None
    agents_json: str | None = None
    json_schema: str | None = None
    timeout: int | None = None


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
            f"CLI '{config.claude_bin}' not found on PATH. "
            "Install Claude Code and authenticate with your subscription (`claude auth`)."
        )
    return path


class ClaudeRunner:
    """Runs prompts against the CLI, with bounded concurrency and retries."""

    def __init__(self, config: WikiConfig) -> None:
        self.config = config
        self.binary = ensure_cli_available(config)
        self._semaphore = asyncio.Semaphore(max(1, config.concurrency))
        self.total_cost_usd = 0.0
        self.total_calls = 0
        # Not every backend prices the call for us. Counting the calls that came
        # back with a cost is what tells a zero total apart from a silent one —
        # and a silent one turns every dollar ceiling into no ceiling at all.
        self.calls_with_cost = 0

    # ------------------------------------------------------------------
    def _build_argv(self, system_prompt: str, options: CallOptions) -> list[str]:
        config = self.config
        tools = options.tools if options.tools is not None else config.tools
        argv = [
            self.binary,
            "--print",
            "--output-format", "json",
            "--model", options.model or config.model,
            "--permission-mode", config.permission_mode,
            "--no-session-persistence",
        ]
        if tools:
            argv += ["--tools", ",".join(tools)]
        if options.agents_json:
            argv += ["--agents", options.agents_json]
        if options.json_schema:
            argv += ["--json-schema", options.json_schema]
        if system_prompt:
            argv += ["--append-system-prompt", system_prompt]
        if config.fallback_model:
            argv += ["--fallback-model", config.fallback_model]
        if config.max_budget_usd is not None:
            argv += ["--max-budget-usd", str(config.max_budget_usd)]
        if config.isolated:
            # No MCP servers and no skills: deterministic and cheaper generation.
            argv += ["--strict-mcp-config", "--disable-slash-commands"]
        return argv

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Useful marker for user-side hooks and telemetry.
        env["WIKI_GENERATOR"] = "1"
        if self.config.provider == providers.BEDROCK:
            # The CLI selects its provider from the environment, not from argv.
            # Credentials are deliberately left to the AWS chain: on ECS and EKS
            # the task role is resolved at call time and there is nothing here
            # to pass along.
            env.update(
                providers.bedrock_env(providers.resolved_region(self.config.aws_region))
            )
        return env

    # ------------------------------------------------------------------
    async def run(
        self,
        prompt: str,
        system_prompt: str = "",
        log_name: str | None = None,
        options: CallOptions | None = None,
    ) -> ClaudeResponse:
        """Run a prompt and return the final text. Retries on transient failures."""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2 ** attempt, 30))
            try:
                async with self._semaphore:
                    return await self._run_once(
                        prompt, system_prompt, log_name, attempt,
                        options or CallOptions(),
                    )
            except ClaudeError as exc:
                last_error = exc
                if not _is_retryable(str(exc)):
                    raise
        raise ClaudeError(
            f"Failed after {self.config.max_retries + 1} attempts: {last_error}"
        )

    async def _run_once(
        self,
        prompt: str,
        system_prompt: str,
        log_name: str | None = None,
        attempt: int = 0,
        options: CallOptions | None = None,
    ) -> ClaudeResponse:
        options = options or CallOptions()
        argv = self._build_argv(system_prompt, options)
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
                timeout=options.timeout or self.config.timeout,
            )
        except asyncio.TimeoutError:
            await _terminate(process)
            raise ClaudeError(
                f"Timed out after {options.timeout or self.config.timeout}s"
            ) from None
        except asyncio.CancelledError:
            # The run is being torn down (Ctrl-C, a failure elsewhere). Without
            # this the CLI child keeps running detached, burning quota against a
            # wiki that is about to be rolled back.
            await _terminate(process)
            raise

        stdout_text = stdout.decode("utf-8", "replace")
        stderr_text = stderr.decode("utf-8", "replace").strip()
        self._write_log(log_name, attempt, argv, prompt, system_prompt,
                        stdout_text, stderr_text, process.returncode)
        if process.returncode != 0:
            raise ClaudeError(_diagnose(stdout_text, stderr_text, process.returncode))

        response = _parse_json_output(stdout_text)
        self.total_cost_usd += response.cost_usd
        self.total_calls += 1
        if response.cost_usd > 0:
            self.calls_with_cost += 1
        return response

    @property
    def cost_is_reported(self) -> bool:
        """False once calls have completed and none of them carried a price."""
        return self.calls_with_cost > 0 or self.total_calls == 0


    # ------------------------------------------------------------------
    def _write_log(
        self, log_name: str | None, attempt: int, argv: list[str], prompt: str,
        system_prompt: str, stdout_text: str, stderr_text: str, returncode: int | None,
    ) -> None:
        """Record the full call when --log-dir is on.

        Stores what was sent and what came back, raw: without this, a page that
        comes out wrong has no way of being investigated afterwards.
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
                        # argv without the binary, to avoid recording machine paths
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
            pass  # the log is a debug extra: it must never break generation


# ----------------------------------------------------------------------
async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Stop a child CLI process, escalating to SIGKILL if it ignores SIGTERM."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    except ProcessLookupError:
        pass


def _payload_from(raw: str) -> dict | None:
    """The JSON object the CLI printed, tolerating noise around it."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    return payload if isinstance(payload, dict) else None


def _diagnose(stdout_text: str, stderr_text: str, returncode: int | None) -> str:
    """Explain a nonzero exit.

    In `--output-format json` the CLI reports the real cause on **stdout** — an
    unknown model, an expired session, an API status — and leaves stderr empty.
    Reading only stderr produced "exited with code 1: (no stderr)", which says
    nothing to the user and, worse, hid the "429"/"usage limit" text that decides
    whether the call is retried at all.
    """
    payload = _payload_from(stdout_text)
    if payload:
        result = payload.get("result")
        detail = result if isinstance(result, str) and result.strip() else ""
        status = payload.get("api_error_status")
        reason = payload.get("terminal_reason")
        parts = [p for p in (detail, stderr_text) if p]
        if status:
            parts.append(f"HTTP {status}")
        if reason and reason not in detail:
            parts.append(str(reason))
        if parts:
            return " | ".join(parts)[:800]
    if stderr_text:
        return f"claude exited with code {returncode}: {stderr_text[:800]}"
    excerpt = " ".join(stdout_text.split())[:300]
    return (
        f"claude exited with code {returncode} without a diagnostic"
        + (f" (stdout: {excerpt})" if excerpt else "")
        + ". Run with --log-dir to record the full call."
    )


def _parse_json_output(raw: str) -> ClaudeResponse:
    raw = raw.strip()
    if not raw:
        raise ClaudeError("The CLI returned empty stdout.")
    payload = _payload_from(raw)
    if payload is None:
        raise ClaudeError(f"Non-JSON output from the CLI: {raw[:500]}") from None

    if payload.get("is_error"):
        raise ClaudeError(_diagnose(raw, "", None))

    text = payload.get("result") if isinstance(payload, dict) else None
    # Under --json-schema the CLI returns a JSON *string* today, but a future
    # version returning a parsed object must not hard-fail with a misleading error.
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False)
    if not isinstance(text, str) or not text.strip():
        raise ClaudeError("Response has no usable 'result' field.")

    return ClaudeResponse(
        text=text,
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        duration_ms=int(payload.get("duration_ms") or 0),
        num_turns=int(payload.get("num_turns") or 0),
        session_id=str(payload.get("session_id") or ""),
    )


_RETRYABLE_MARKERS = (
    # "timed out" as well as "timeout": this module raises "Timed out after Ns",
    # which does not contain "timeout" — without both, every timeout was treated
    # as permanent and re-raised on the first attempt.
    "timeout", "timed out", "overloaded", "rate limit", "rate_limit", "429",
    "usage limit", "quota", "529", "503", "502",
    "connection", "temporarily", "econnreset", "socket hang up",
    # Bedrock names its transient failures rather than numbering them, and it
    # throttles harder than the subscription: without these, a run against a
    # busy account gives up on the first burst.
    "throttling", "throttled", "toomanyrequests", "serviceunavailable",
    "modelnotready", "model is not ready", "internalfailure",
    "internalservererror", "requesttimeout", "capacity",
)

# Failures where retrying only burns the clock: the caller must fix something.
_PERMANENT_MARKERS = (
    "could not load credentials", "security token", "accessdenied",
    "unrecognizedclient", "expiredtoken", "invalidsignature",
    "validationexception", "resourcenotfound", "is not authorized",
)


def _is_retryable(message: str) -> bool:
    lowered = message.lower().replace(" ", "")
    if any(marker.replace(" ", "") in lowered for marker in _PERMANENT_MARKERS):
        return False
    return any(marker.replace(" ", "") in lowered for marker in _RETRYABLE_MARKERS)
