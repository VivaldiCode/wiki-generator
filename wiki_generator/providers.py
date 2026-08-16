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


def resolved_region(region: str | None) -> str | None:
    if region:
        return region
    for name in REGION_ENV:
        value = os.environ.get(name)
        if value:
            return value
    return _region_from_aws_config()


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
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


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
            "It is not inferred from the instance."
        )
    if not has_credentials():
        warnings.append(
            "No AWS credentials found in the environment or in ~/.aws. This is "
            "expected on EC2/ECS/EKS, where the role is resolved at call time, "
            "and a misconfiguration otherwise."
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
