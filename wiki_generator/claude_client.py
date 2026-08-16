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
import tempfile
from pathlib import Path
from dataclasses import dataclass, field

from . import providers
from .clients import Client, get as get_client
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
class TokenUsage:
    """What the call consumed, in the unit every backend agrees on.

    Cost is optional — Bedrock bills the account and may report nothing — but
    tokens always come back. Recording them is what lets a run be priced later
    from the provider's own rate card.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        return self

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass
class ClaudeResponse:
    text: str
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    session_id: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)


INSTALL_HINT = {
    "claude": "Install Claude Code and authenticate (`claude`, then /login).",
    "grok": "Install Grok and authenticate (`grok login`, or set XAI_API_KEY).",
}


def ensure_cli_available(config: WikiConfig) -> str:
    binary = config.binary
    path = shutil.which(binary)
    if not path:
        hint = INSTALL_HINT.get(config.client, "")
        raise ClaudeError(f"CLI '{binary}' not found on PATH. {hint}".strip())
    return path


class ClaudeRunner:
    """Runs prompts against the CLI, with bounded concurrency and retries."""

    def __init__(self, config: WikiConfig) -> None:
        self.config = config
        self.client: Client = get_client(config.client)
        self.binary = ensure_cli_available(config)
        self._semaphore = asyncio.Semaphore(max(1, config.concurrency))
        self.total_cost_usd = 0.0
        self.total_calls = 0
        # Not every backend prices the call for us. Counting the calls that came
        # back with a cost is what tells a zero total apart from a silent one —
        # and a silent one turns every dollar ceiling into no ceiling at all.
        self.calls_with_cost = 0
        self.usage = TokenUsage()

    # ------------------------------------------------------------------
    def _build_argv(self, system_prompt: str, options: CallOptions) -> list[str]:
        return self.client.argv(self.binary, self.config, system_prompt, options)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Useful marker for user-side hooks and telemetry.
        env["WIKI_GENERATOR"] = "1"
        # The CLI selects its provider from the environment, not from argv, and
        # which variables those are is the client's business.
        env.update(self.client.env(self.config))
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

        # A dense reference page's prompt exceeds the argv length limit, so a
        # client that cannot read stdin gets a file instead of a longer command.
        stdin_payload = self.client.stdin_payload(prompt)
        prompt_file: Path | None = None
        if self.client.prompt_mode == "file":
            prompt_file = Path(
                tempfile.mkstemp(prefix="wiki-prompt-", suffix=".txt")[1]
            )
            prompt_file.write_text(prompt, encoding="utf-8")
        self.client.attach_prompt(argv, prompt, prompt_file)

        try:
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
                    process.communicate(stdin_payload),
                    timeout=options.timeout or self.config.timeout,
                )
            except asyncio.TimeoutError:
                await _terminate(process)
                raise ClaudeError(
                    f"Timed out after {options.timeout or self.config.timeout}s"
                ) from None
            except asyncio.CancelledError:
                # The run is being torn down (Ctrl-C, a failure elsewhere).
                # Without this the CLI child keeps running detached, burning
                # quota against a wiki that is about to be rolled back.
                await _terminate(process)
                raise
        finally:
            if prompt_file is not None:
                prompt_file.unlink(missing_ok=True)

        stdout_text = stdout.decode("utf-8", "replace")
        stderr_text = stderr.decode("utf-8", "replace").strip()
        self._write_log(log_name, attempt, argv, prompt, system_prompt,
                        stdout_text, stderr_text, process.returncode)
        if process.returncode != 0:
            raise ClaudeError(
                _diagnose(stdout_text, stderr_text, process.returncode, self.client)
            )

        response = _parse_json_output(stdout_text, self.client)
        self.total_cost_usd += response.cost_usd
        self.total_calls += 1
        if response.cost_usd > 0:
            self.calls_with_cost += 1
        self.usage += response.usage
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
    """The JSON object the CLI printed, tolerating noise around it.

    Output shapes differ: Claude prints one compact line, Grok pretty-prints and
    on failure appends a plain `Error: ...` line after the JSON. Decoding from
    the first `{` and ignoring whatever trails it covers every combination —
    a line-based scan does not, because a pretty-printed object's first line is
    a bare `{`.
    """
    raw = raw.strip()
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _diagnose(
    stdout_text: str, stderr_text: str, returncode: int | None,
    client: Client | None = None,
) -> str:
    """Explain a nonzero exit.

    In `--output-format json` the CLI reports the real cause on **stdout** — an
    unknown model, an expired session, an API status — and leaves stderr empty.
    Reading only stderr produced "exited with code 1: (no stderr)", which says
    nothing to the user and, worse, hid the "429"/"usage limit" text that decides
    whether the call is retried at all.
    """
    payload = _payload_from(stdout_text)
    if payload:
        client = client or get_client("claude")
        # The CLI's own words first: they are the only thing that names the
        # actual cause (an unknown model, an expired session, a missing role).
        detail = client.error_from(payload) or ""
        parts = [p for p in (detail, stderr_text) if p]
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


def _parse_json_output(raw: str, client: Client | None = None) -> ClaudeResponse:
    client = client or get_client("claude")
    raw = raw.strip()
    if not raw:
        raise ClaudeError("The CLI returned empty stdout.")
    payload = _payload_from(raw)
    if payload is None:
        raise ClaudeError(f"Non-JSON output from the CLI: {raw[:500]}") from None

    error = client.error_from(payload)
    if error:
        raise ClaudeError(error)

    fields = client.parse(payload)
    text = fields.get("text")
    # Under --json-schema the CLI returns a JSON *string* today, but a future
    # version returning a parsed object must not hard-fail with a misleading error.
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False)
    if not isinstance(text, str) or not text.strip():
        raise ClaudeError("Response has no usable 'result' field.")

    raw_usage = fields.get("usage") or {}

    def _count(name: str) -> int:
        try:
            return int(raw_usage.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    return ClaudeResponse(
        text=text,
        cost_usd=float(fields.get("cost_usd") or 0.0),
        duration_ms=int(fields.get("duration_ms") or 0),
        num_turns=int(fields.get("num_turns") or 0),
        session_id=str(fields.get("session_id") or ""),
        usage=TokenUsage(
            input_tokens=_count("input_tokens"),
            output_tokens=_count("output_tokens"),
            cache_read_input_tokens=_count("cache_read_input_tokens"),
            cache_creation_input_tokens=_count("cache_creation_input_tokens"),
        ),
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
    # An expired SSO session needs `aws sso login`, not another attempt. It was
    # already not being retried, but only because no retryable marker happened
    # to match — which is luck, not a decision.
    "sso session token", "aws sso login", "was not found or is invalid",
)


def _is_retryable(message: str) -> bool:
    lowered = message.lower().replace(" ", "")
    if any(marker.replace(" ", "") in lowered for marker in _PERMANENT_MARKERS):
        return False
    return any(marker.replace(" ", "") in lowered for marker in _RETRYABLE_MARKERS)
