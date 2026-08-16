"""Which agentic CLI runs the prompts.

The generator never sends a repository's contents to a model. It runs a coding
CLI with read-only tools and lets the model open the files it needs, which is
what keeps per-page cost flat on a large repository. Any CLI that can do four
things can drive it:

  1. run a single prompt and exit (headless / print mode)
  2. read files itself (Read/Glob/Grep or equivalents)
  3. be restricted to reading only
  4. report the result as JSON

Everything else — subagents, JSON schemas, per-call cost — is optional, and the
features that need them check `capabilities` before running rather than failing
mid-way through a paid run.

Adding a client means adding a class here. Nothing else in the codebase names a
binary or a flag.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

_OPENCODE_CONFIG_PATH: str | None = None


@dataclass(frozen=True)
class Capabilities:
    """What a client can do, so callers can degrade instead of failing."""

    # Constrain the output to a JSON Schema. `--verify` needs it.
    json_schema: bool = False
    # Spawn parallel subagents from one call. `--verify` needs it.
    subagents: bool = False
    # Restrict the model to an explicit tool allowlist. Without this the
    # generator cannot promise it will not write to the repository.
    tool_restriction: bool = False
    # Report a price per call. Without it, dollar budgets become count budgets.
    cost: bool = False
    # Accept the prompt on stdin. Falls back to argv, which has a length limit
    # that a reference page's prompt can exceed.
    stdin_prompt: bool = False
    # Whether this adapter's flags and JSON envelope were confirmed against a
    # running, signed-in CLI — as opposed to read off `--help`. An unverified
    # adapter says so at startup instead of implying a guarantee it has not
    # earned, because `--tools` naming an unknown tool may restrict nothing.
    verified: bool = False


class Client:
    """Base adapter. Subclasses map the generator's needs onto one CLI."""

    name: str = ""
    binary: str = ""
    default_model: str = ""
    capabilities = Capabilities()

    # Tool names are per-CLI and are a safety boundary, not a preference: an
    # allowlist naming tools a CLI does not have restricts nothing and fails
    # open. Measured on Grok — `--tools Read,Glob,Grep` left the terminal tool
    # in place, `--tools read_file,list_dir,grep` removed it.
    read_tools: tuple[str, ...] = ()
    subagent_tool: str = ""
    # Denied by name as a second lock, in case an allowlist is ever ignored.
    write_tools: tuple[str, ...] = ()

    def tool_set(self, with_subagents: bool = False) -> tuple[str, ...]:
        extra = (self.subagent_tool,) if with_subagents and self.subagent_tool else ()
        return self.read_tools + extra

    def warnings(self) -> list[str]:
        """What the user should know before a paid run starts."""
        if self.capabilities.verified:
            return []
        return [
            f"The '{self.name}' client is mapped from its `--help` output and has "
            "not been confirmed against a signed-in run. If the tool allowlist "
            "names tools this CLI does not have, it may restrict nothing — mount "
            "the repository read-only if that matters.",
        ]

    # ------------------------------------------------------------------
    def argv(self, binary: str, config, system_prompt: str, options) -> list[str]:
        raise NotImplementedError

    def env(self, config) -> dict[str, str]:
        return {}

    # How the prompt reaches the CLI. "stdin" avoids every length limit;
    # "file" writes a temp file and names it with `prompt_file_flag`; "argv"
    # appends it as a positional argument and is bounded by ARG_MAX.
    prompt_mode: str = "stdin"
    prompt_file_flag: str = ""

    def stdin_payload(self, prompt: str) -> bytes | None:
        """What to write to the child's stdin, or None when it goes in argv."""
        return prompt.encode("utf-8") if self.prompt_mode == "stdin" else None

    def attach_prompt(self, argv: list[str], prompt: str, path) -> None:
        """Add the prompt to argv for the non-stdin modes."""
        if self.prompt_mode == "file":
            argv += [self.prompt_file_flag, str(path)]
        elif self.prompt_mode == "argv":
            argv.append(prompt)

    def parse(self, payload: dict) -> dict:
        """Normalise the CLI's JSON envelope to the generator's shape.

        Returns a dict with `text`, `cost_usd`, `duration_ms`, `num_turns`,
        `session_id` and `usage`. Raising is left to the caller so every client
        produces the same error type.
        """
        raise NotImplementedError

    def error_from(self, payload: dict) -> str | None:
        """The CLI's own description of a failure, if the payload carries one."""
        return None


# ----------------------------------------------------------------------
class ClaudeClient(Client):
    """Claude Code (`claude -p`). The reference implementation."""

    name = "claude"
    binary = "claude"
    capabilities = Capabilities(
        json_schema=True, subagents=True, tool_restriction=True,
        cost=True, stdin_prompt=True, verified=True,
    )
    default_model = "haiku"
    read_tools = ("Read", "Glob", "Grep")
    subagent_tool = "Agent"
    write_tools = ("Write", "Edit", "Bash")
    prompt_mode = "stdin"

    def argv(self, binary: str, config, system_prompt: str, options) -> list[str]:
        tools = options.tools if options.tools is not None else self.tool_set()
        argv = [
            binary,
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

    def env(self, config) -> dict[str, str]:
        from . import providers

        if config.provider == providers.BEDROCK:
            return providers.bedrock_env(providers.resolved_region(config.aws_region))
        return {}

    def parse(self, payload: dict) -> dict:
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return {
            "text": payload.get("result"),
            "cost_usd": payload.get("total_cost_usd"),
            "duration_ms": payload.get("duration_ms"),
            "num_turns": payload.get("num_turns"),
            "session_id": payload.get("session_id"),
            "usage": usage,
        }

    def error_from(self, payload: dict) -> str | None:
        if not payload.get("is_error"):
            return None
        result = payload.get("result")
        detail = result if isinstance(result, str) and result.strip() else ""
        parts = [p for p in (detail,) if p]
        status = payload.get("api_error_status")
        reason = payload.get("terminal_reason")
        if status:
            parts.append(f"HTTP {status}")
        if reason and reason not in detail:
            parts.append(str(reason))
        return " | ".join(parts) or "the CLI reported an error with no detail"


# ----------------------------------------------------------------------
class GrokClient(Client):
    """Grok (`grok -p`).

    The flag surface is close enough to Claude Code's to be a near-mapping, and
    the differences are the interesting part:

      * `--rules` appends to the system prompt; `--system-prompt-override`
        replaces it. Appending is what the generator wants — the CLI's own
        instructions about reading files are load-bearing.
      * There is no `--no-session-persistence`. Sessions are written under
        `~/.grok`; a long multi-repo run leaves them behind, which is why
        `GROK_SESSION_DIR` is pointed somewhere disposable when set.
      * The prompt goes in argv (`-p`), except that `--prompt-file` avoids the
        argv length limit, which a dense reference page can otherwise reach.
    """

    name = "grok"
    binary = "grok"
    capabilities = Capabilities(
        json_schema=True, subagents=True, tool_restriction=True,
        # Confirmed on a signed-in run: the envelope carries `total_cost_usd`
        # and a `usage` block with the same token field names.
        cost=True,
        # `-p` takes the prompt as an argument; `--prompt-file` is used instead.
        stdin_prompt=False,
        verified=True,
    )
    # From `grok models`. Note the internal name reported in `modelUsage`
    # ("grok-4.6-build") is not a valid `--model` value.
    default_model = "grok-4.6"
    read_tools = ("read_file", "list_dir", "grep")
    subagent_tool = "spawn_subagent"
    write_tools = ("run_terminal_command", "search_replace", "write_file")
    prompt_mode = "file"
    prompt_file_flag = "--prompt-file"

    def argv(self, binary: str, config, system_prompt: str, options) -> list[str]:
        tools = options.tools if options.tools is not None else self.tool_set()
        argv = [
            binary,
            "--output-format", "json",
            "--model", options.model or config.model,
            "--permission-mode", config.permission_mode,
            # The prompt is written verbatim; the CLI must not reinterpret it.
            "--verbatim",
        ]
        if tools:
            argv += ["--tools", ",".join(tools)]
        if options.agents_json:
            argv += ["--agents", options.agents_json]
        else:
            # Page generation is a single call by design: an unasked-for subagent
            # is unbudgeted cost and unpredictable output.
            argv += ["--no-subagents"]
        if options.json_schema:
            argv += ["--json-schema", options.json_schema]
        if system_prompt:
            # Append, never override: the CLI's own tool instructions must stay.
            argv += ["--rules", system_prompt]
        # A second lock, in case an allowlist is ever ignored.
        for rule in self.write_tools:
            argv += ["--deny", rule]
        if config.isolated:
            argv += ["--disable-web-search", "--no-memory", "--no-plan"]
        return argv

    def parse(self, payload: dict) -> dict:
        # Field names differ between CLIs and versions; each candidate list is
        # tried in order so a rename degrades to a clear error rather than a
        # wrong number silently entering the ledger.
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return {
            # Confirmed shape: {"text": ..., "sessionId": ..., "usage": {...},
            # "num_turns": N, "total_cost_usd": F}. No duration is reported.
            "text": _first(payload, "text", "result", "response", "content"),
            "cost_usd": _first(payload, "total_cost_usd", "cost_usd"),
            "duration_ms": _first(payload, "duration_ms", "elapsed_ms"),
            "num_turns": _first(payload, "num_turns", "turns"),
            "session_id": _first(payload, "session_id", "sessionId", "id"),
            "usage": usage,
        }

    def error_from(self, payload: dict) -> str | None:
        # Observed shape when signed out:
        #   {"type":"error","message":"Not signed in. ..."}
        if payload.get("type") == "error" or payload.get("is_error"):
            message = _first(payload, "message", "error", "result")
            if isinstance(message, dict):
                message = json.dumps(message, ensure_ascii=False)
            return str(message) if message else "the CLI reported an error"
        return None


class OpenCodeClient(Client):
    """opencode (`opencode run`).

    The furthest of the three from the generator's shape, and the differences
    are structural rather than cosmetic:

      * **No tool allowlist flag.** Permissions live in a config file, and
        headless blocks on an approval prompt unless `--auto` is passed — which
        auto-approves everything not explicitly denied. The adapter therefore
        writes its own config with `bash`/`edit`/`write` denied and points
        `OPENCODE_CONFIG` at it, so containment does not depend on whatever the
        user has configured. That the CLI validates config keys (an invented key
        is rejected) is evidence the keys are real; it is not evidence that the
        denial takes effect.
      * **No `--json-schema` and no inline subagent definitions**, so `--verify`
        cannot run on this client. The capability flags say so and the CLI
        refuses before generating rather than after.
      * **`--format json` emits an event stream**, not one result object, so the
        text has to be assembled from events rather than read from a field.
    """

    name = "opencode"
    binary = "opencode"
    capabilities = Capabilities(
        json_schema=False,
        subagents=False,
        # No allowlist flag exists. Denial is configured, and unconfirmed.
        tool_restriction=False,
        cost=False,
        stdin_prompt=False,
        verified=False,
    )
    default_model = "anthropic/claude-haiku-4-5"
    read_tools = ()
    subagent_tool = ""
    write_tools = ("bash", "edit", "write", "patch", "webfetch")
    # `opencode run [message..]` takes the prompt positionally; there is no
    # stdin or prompt-file path.
    prompt_mode = "argv"

    def warnings(self) -> list[str]:
        return [
            "The 'opencode' client has no tool-allowlist flag: read-only access "
            "is configured, not enforced by argv, and could not be confirmed "
            "behaviourally here. Run it against a repository you can afford to "
            "have modified, or mount the repository read-only.",
            "`--verify` is unavailable on this client: it has no JSON-schema "
            "output and no inline subagent definitions.",
        ]

    def config_payload(self) -> dict:
        """The config the adapter imposes, independent of the user's own."""
        return {"permission": {tool: "deny" for tool in self.write_tools}}

    def env(self, config) -> dict[str, str]:
        # Written once per process and pointed at by OPENCODE_CONFIG, so the
        # denials do not depend on the user's own config file. Confirmed that
        # the variable is honoured and that the CLI rejects unknown keys.
        global _OPENCODE_CONFIG_PATH
        if _OPENCODE_CONFIG_PATH is None:
            handle, path = tempfile.mkstemp(prefix="wiki-opencode-", suffix=".json")
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(self.config_payload(), out)
            _OPENCODE_CONFIG_PATH = path
        return {"OPENCODE_CONFIG": _OPENCODE_CONFIG_PATH}

    def argv(self, binary: str, config, system_prompt: str, options) -> list[str]:
        argv = [
            binary, "run",
            "--model", options.model or config.model,
            "--format", "json",
            # Without this the run blocks on an approval prompt that headless
            # has no way to answer. It is only safe alongside the denials in
            # `config_payload`, which is why the two ship together.
            "--auto",
        ]
        return argv

    def parse(self, payload: dict) -> dict:
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return {
            "text": _first(payload, "text", "result", "content", "message"),
            "cost_usd": _first(payload, "cost", "total_cost_usd", "cost_usd"),
            "duration_ms": _first(payload, "duration_ms"),
            "num_turns": _first(payload, "num_turns", "turns"),
            "session_id": _first(payload, "sessionID", "session_id", "id"),
            "usage": usage,
        }

    def error_from(self, payload: dict) -> str | None:
        if payload.get("type") == "error" or payload.get("name") in {
            "UnknownError", "ProviderAuthError",
        }:
            data = payload.get("data")
            message = (data or {}).get("message") if isinstance(data, dict) else None
            return str(message or payload.get("message") or payload.get("name"))
        return None


def _first(payload: dict, *names: str):
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return None


# ----------------------------------------------------------------------
CLIENTS: dict[str, Client] = {
    ClaudeClient.name: ClaudeClient(),
    GrokClient.name: GrokClient(),
    OpenCodeClient.name: OpenCodeClient(),
}

DEFAULT_CLIENT = ClaudeClient.name


def get(name: str) -> Client:
    try:
        return CLIENTS[name]
    except KeyError:
        raise ValueError(
            f"Unknown client '{name}'. Available: {', '.join(sorted(CLIENTS))}."
        ) from None
