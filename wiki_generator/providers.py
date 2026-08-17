"""Where the model runs: the Claude subscription, or Amazon Bedrock.

The CLI decides this from environment variables, not flags, so the whole
integration is `_env()` plus a preflight. What justifies a module is the
preflight: a generation run costs tens of minutes, and every way this can be
misconfigured — no region, no credentials, an unentitled model — fails on the
first call and is indistinguishable from a transient error at that point.
Checking before the run turns a 40-minute discovery into a one-line message.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SUBSCRIPTION = "subscription"
BEDROCK = "bedrock"
PROVIDERS = (SUBSCRIPTION, BEDROCK)

# Standard AWS resolution order. Any one of these being present means the SDK
# inside the CLI has something to work with; only the absence of all of them is
# a certain failure.
CREDENTIAL_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_ROLE_ARN",
    # ECS and EKS inject these; on EC2 the instance metadata service answers
    # with no environment variable at all, which is why absence is a warning
    # rather than an error.
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
)

REGION_ENV = ("AWS_REGION", "AWS_DEFAULT_REGION")

# The instance metadata service. On EC2 this is where an attached IAM role — an
# instance profile — supplies both the credentials and the region, with no
# `~/.aws` and no environment variable anywhere. Off EC2 the address is
# unroutable, so every call here is on a tight timeout and answered once.
IMDS_HOST = "169.254.169.254"
IMDS_TIMEOUT = 1.0
# A link-local address answers in single-digit milliseconds on EC2 and not at
# all anywhere else, so reachability is settled once with a bare socket. Without
# it, two HTTP lookups on a laptop cost four seconds of every Bedrock preflight.
IMDS_PROBE_TIMEOUT = 0.3
_imds_cache: dict[str, str | None] = {}
_imds_reachable: bool | None = None


class ProviderError(RuntimeError):
    """The provider is not usable, and no amount of retrying will change that."""


def bedrock_env(region: str | None) -> dict[str, str]:
    """The variables that switch the CLI to Bedrock.

    Everything else — credentials, endpoint overrides, proxy settings — is left
    to the AWS SDK's own resolution, which already handles the task role, the
    instance profile and the shared config file.
    """
    env = {"CLAUDE_CODE_USE_BEDROCK": "1"}
    if region:
        env["AWS_REGION"] = region
        # Some AWS SDK versions read only the older name.
        env.setdefault("AWS_DEFAULT_REGION", region)
    return env


def _imds(path: str) -> str | None:
    """One metadata lookup, IMDSv2 first, answered once per process.

    IMDSv2 needs a token from a PUT before any GET; v1 answers the GET directly.
    Both are tried because an instance can be configured either way, and the
    whole thing is bounded by a one-second timeout so a laptop that is not on
    EC2 pays a second at most, once.
    """
    if path in _imds_cache:
        return _imds_cache[path]
    if not _reachable():
        _imds_cache[path] = None
        return None

    import urllib.error
    import urllib.request

    headers: dict[str, str] = {}
    try:
        token_request = urllib.request.Request(
            f"http://{IMDS_HOST}/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        with urllib.request.urlopen(token_request, timeout=IMDS_TIMEOUT) as response:
            headers["X-aws-ec2-metadata-token"] = response.read().decode()
    except Exception:  # noqa: BLE001 - not on EC2, or v1-only; the GET decides
        pass

    try:
        request = urllib.request.Request(
            f"http://{IMDS_HOST}/latest/meta-data/{path}", headers=headers
        )
        with urllib.request.urlopen(request, timeout=IMDS_TIMEOUT) as response:
            value = response.read().decode().strip() or None
    except Exception:  # noqa: BLE001 - no metadata service reachable
        value = None
    _imds_cache[path] = value
    return value


def _reachable() -> bool:
    """Is anything listening on the metadata address? Asked once."""
    global _imds_reachable
    if _imds_reachable is not None:
        return _imds_reachable
    import socket

    try:
        with socket.create_connection((IMDS_HOST, 80), IMDS_PROBE_TIMEOUT):
            _imds_reachable = True
    except OSError:
        _imds_reachable = False
    return _imds_reachable


def instance_role() -> str | None:
    """The IAM role attached to this EC2 instance, if there is one."""
    names = _imds("iam/security-credentials/")
    return names.splitlines()[0].strip() if names else None


def instance_region() -> str | None:
    return _imds("placement/region")


def resolved_region(region: str | None) -> str | None:
    if region:
        return region
    for name in REGION_ENV:
        value = os.environ.get(name)
        if value:
            return value
    from_config = _region_from_aws_config()
    if from_config:
        return from_config
    # Last, because it is the only source that costs a network call — and the
    # one that makes an EC2 instance with an attached role work with no
    # configuration at all.
    return instance_region()


def _region_from_aws_config() -> str | None:
    """The region in ~/.aws/config, read without importing botocore."""
    profile = os.environ.get("AWS_PROFILE", "default")
    path = Path(os.environ.get("AWS_CONFIG_FILE", "~/.aws/config")).expanduser()
    if not path.is_file():
        return None
    wanted = "[default]" if profile == "default" else f"[profile {profile}]"
    in_section = False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_section = stripped == wanted
            elif in_section and stripped.startswith("region"):
                _, _, value = stripped.partition("=")
                return value.strip() or None
    except OSError:
        return None
    return None


def has_credentials() -> bool:
    if any(os.environ.get(name) for name in CREDENTIAL_ENV):
        return True
    # The shared credentials file is the last thing the SDK tries before the
    # instance metadata service. An empty file counts as absent: `aws configure`
    # creates one before anything is written to it, and treating it as proof of
    # credentials silences the warning on exactly the machine that needs it.
    path = Path(
        os.environ.get("AWS_SHARED_CREDENTIALS_FILE", "~/.aws/credentials")
    ).expanduser()
    try:
        if path.is_file() and path.stat().st_size > 0:
            return True
    except OSError:
        pass
    # An attached instance role is real credentials, and leaves no trace on disk
    # or in the environment. Reporting "no credentials" here sends people
    # looking for a file that is not supposed to exist.
    return instance_role() is not None


def preflight(provider: str, region: str | None, verbose: bool = False) -> list[str]:
    """Check the provider before a run starts. Returns non-fatal warnings.

    Raises ProviderError for what is certainly broken; warns about what merely
    looks wrong, because EC2 and EKS supply credentials through channels that
    are invisible from here.
    """
    if provider not in PROVIDERS:
        raise ProviderError(
            f"Unknown provider '{provider}'. Use one of: {', '.join(PROVIDERS)}."
        )
    if provider != BEDROCK:
        return []

    warnings: list[str] = []
    if not resolved_region(region):
        raise ProviderError(
            "Bedrock needs a region. Pass --aws-region, or set AWS_REGION. "
            "On EC2 it is read from the instance metadata service; if this is "
            "an EC2 instance, that service did not answer — inside a container "
            "that usually means the IMDSv2 hop limit is 1 (raise it to 2)."
        )
    role = instance_role()
    if role:
        warnings.append(f"Using the instance role attached to this machine: {role}.")
    elif not has_credentials():
        warnings.append(
            "No AWS credentials found: nothing in the environment, nothing in "
            "~/.aws, and no instance role answering. On ECS the task role is "
            "resolved at call time and this is expected; anywhere else it is a "
            "misconfiguration."
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        # Not fatal — the CLI prefers the Bedrock path once the flag is set —
        # but a key in the environment of an AWS job is worth saying out loud.
        warnings.append(
            "ANTHROPIC_API_KEY is set but ignored under --bedrock. Unset it so "
            "the credential in use is unambiguous."
        )
    if verbose:
        warnings.extend(_identity_note())
    return warnings


def ollama_preflight(model: str, interactive: bool) -> str:
    """Settle the local model before a run starts. Returns the model to use.

    Interactively this offers to pull what is missing or to pick something else;
    scripted it raises, because a run that silently swaps the model is worse
    than one that stops. Nothing is downloaded without being asked for: a pull
    is gigabytes over someone else's connection.
    """
    from . import ollama

    state, detail = ollama.check(model)
    if state == "ok":
        return model
    if state == "not_running":
        raise ProviderError(detail)

    usable = ollama.usable_models()
    if not interactive:
        options = ", ".join(m.name for m in usable) or "none installed"
        # The remedy differs by cause: pulling a model again does not give it
        # tool support, so only the missing case gets a `pull` suggestion.
        remedy = (
            f"Install it with `ollama pull {ollama.model_name(model)}`, or pick"
            if state == "missing" else "Pick"
        )
        raise ProviderError(f"{detail} {remedy} one that can: {options}.")
    return _choose_local_model(model, detail, usable)


def _choose_local_model(model: str, detail: str, usable: list) -> str:
    from . import ollama

    wanted = ollama.model_name(model)
    print(f"  ! {detail}")
    options: list[tuple[str, str]] = []
    if not any(m.name == wanted for m in usable):
        options.append((f"pull {wanted}", ollama.PREFIX + wanted))
    options += [(f"use {m.name} ({m.size_gb:.1f} GB, already here)",
                 ollama.PREFIX + m.name) for m in usable]
    options.append(("pull something else", ""))

    for index, (label, _) in enumerate(options, start=1):
        print(f"      {index}. {label}")
    while True:
        try:
            answer = input("    Choose [1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            raise ProviderError("no model chosen") from None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            label, chosen = options[int(answer) - 1]
            break
        print(f"      A number from 1 to {len(options)}.")

    if not chosen:  # pull something else
        try:
            name = input("    Model name (e.g. qwen2.5-coder:latest): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise ProviderError("no model chosen") from None
        if not name:
            raise ProviderError("no model chosen")
        chosen = ollama.PREFIX + name

    name = ollama.model_name(chosen)
    if not any(m.name == name for m in usable):
        print(f"    Pulling {name} — this downloads several gigabytes.")
        try:
            ollama.pull(name, on_progress=lambda line: print(f"      {line}"))
        except ollama.OllamaError as exc:
            raise ProviderError(str(exc)) from None
        state, detail = ollama.check(chosen)
        if state != "ok":
            raise ProviderError(detail)
    print(f"    Using {chosen}.")
    return chosen


def _identity_note() -> list[str]:
    """Which AWS identity is in play, when the CLI is available to say so."""
    if not shutil.which("aws"):
        return []
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        detail = " ".join((result.stderr or "").split())[:200]
        return [f"aws sts get-caller-identity failed: {detail}"]
    return [f"AWS identity: {result.stdout.strip()}"]
