"""Running the whole thing in a container instead of on this machine.

The appeal is that the image already has every client CLI installed, so nothing
has to be set up on the host. What it costs is that a container sees a different
filesystem, and the two things that have to cross that boundary are the ones
that matter most: the repositories, and the credentials.

Paths are translated rather than assumed. `--repo /home/me/code/api` on the host
becomes `--repo /repos/api` inside, with `/home/me/code` mounted read-only at
`/repos` — the generator only ever reads a repository, and the mount makes that
a guarantee rather than a promise.

Credentials are mounted per client and never copied: only the directory the
chosen client actually authenticates from is exposed, read-only where the client
tolerates it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

IMAGE = "wiki-generator:local"
DOCKERFILE = "deploy/Dockerfile"

REPOS_MOUNT = "/repos"
WIKIS_MOUNT = "/wikis"

# Where each client keeps what it needs to authenticate. Mounted only for the
# client actually in use — there is no reason for a Grok run to see AWS keys.
CREDENTIALS = {
    "claude": [("~/.claude", "/home/node/.claude", "rw")],
    "grok": [("~/.grok", "/home/node/.grok", "rw")],
    "opencode": [
        ("~/.local/share/opencode", "/home/node/.local/share/opencode", "rw"),
        ("~/.config/opencode", "/home/node/.config/opencode", "ro"),
    ],
    "bedrock": [("~/.aws", "/home/node/.aws", "ro")],
}

PATH_FLAGS = ("--repo", "--source")


class DockerError(RuntimeError):
    """Docker cannot be used for this run."""


@dataclass
class Plan:
    """Everything needed to start the container, and nothing hidden."""

    docker_argv: list[str] = field(default_factory=list)
    inner_argv: list[str] = field(default_factory=list)
    mounts: list[tuple[str, str, str]] = field(default_factory=list)
    missing_credentials: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def argv(self) -> list[str]:
        return self.docker_argv + self.inner_argv


# ----------------------------------------------------------------------
def available() -> tuple[bool, str]:
    """Docker installed *and* its daemon answering — the two fail differently."""
    if not shutil.which("docker"):
        return False, "docker is not on PATH."
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run docker: {exc}"
    if result.returncode != 0:
        detail = " ".join((result.stderr or "").split())[:160]
        return False, f"the docker daemon is not answering ({detail})"
    return True, result.stdout.strip()


def image_exists(tag: str = IMAGE) -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def build(tag: str = IMAGE, context: Path | None = None, on_line=None,
          clients: tuple[str, ...] = ("claude", "grok", "opencode")) -> None:
    """Build the image from this checkout. Streams output so it is watchable."""
    root = Path(context or _repo_root())
    dockerfile = root / DOCKERFILE
    if not dockerfile.is_file():
        raise DockerError(
            f"{dockerfile} not found — build needs a checkout of the repository."
        )
    argv = ["docker", "build", "-t", tag, "-f", str(dockerfile),
            "--build-arg", f"WITH_GROK={1 if 'grok' in clients else 0}",
            "--build-arg", f"WITH_OPENCODE={1 if 'opencode' in clients else 0}",
            str(root)]
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
    except OSError as exc:
        raise DockerError(f"could not start the build: {exc}") from None
    assert process.stdout is not None
    for line in process.stdout:
        if on_line:
            on_line(line.rstrip())
    if process.wait() != 0:
        raise DockerError("the image build failed")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
def plan(argv: list[str], tag: str = IMAGE, interactive: bool = True) -> Plan:
    """Translate a host command line into a container one.

    Pure: no daemon is contacted, so what the container will be told can be
    inspected — and tested — without Docker running at all.
    """
    argv = list(argv)
    result = Plan()
    mounts: list[tuple[str, str, str]] = []

    source_flag = next((f for f in PATH_FLAGS if f in argv), None)
    if source_flag is None:
        raise DockerError("nothing to document: no --repo or --source given.")
    source = Path(argv[argv.index(source_flag) + 1]).expanduser().resolve()

    # A single repository is mounted by name so its wiki keeps that name; a tree
    # is mounted whole.
    if source_flag == "--repo":
        host_root, inner = source.parent, f"{REPOS_MOUNT}/{source.name}"
    else:
        host_root, inner = source, REPOS_MOUNT
    mounts.append((str(host_root), REPOS_MOUNT, "ro"))
    argv[argv.index(source_flag) + 1] = inner

    if "--output" in argv:
        index = argv.index("--output") + 1
        output = Path(argv[index]).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        mounts.append((str(output), WIKIS_MOUNT, "rw"))
        argv[index] = WIKIS_MOUNT
    else:
        raise DockerError(
            "--output is required in a container: without it the wiki is written "
            "inside the repository mount, which is read-only, and is lost when "
            "the container exits."
        )

    # The control file follows the output, so a triage survives the container.
    if "--control-file" in argv:
        argv[argv.index("--control-file") + 1] = f"{WIKIS_MOUNT}/wiki-control.json"
    else:
        argv += ["--control-file", f"{WIKIS_MOUNT}/wiki-control.json"]

    from . import providers

    # An attached instance role needs nothing mounted: the container reaches the
    # metadata service directly. Reporting ~/.aws as missing there would send
    # someone looking for a file that is not supposed to exist.
    needed = _clients_in(argv)
    # Only asked when Bedrock is actually in play: the probe is cheap but it is
    # still a socket, and a Grok run has no business touching it.
    on_instance = "bedrock" in needed and providers.instance_role() is not None
    for name in needed:
        if name == "bedrock" and on_instance:
            result.notes.append(
                "Bedrock will use this machine's instance role; nothing mounted. "
                "If the container cannot reach the metadata service, the IMDSv2 "
                "hop limit is 1 by default and has to be 2 for containers: "
                "aws ec2 modify-instance-metadata-options "
                "--http-put-response-hop-limit 2 --instance-id <id>"
            )
            continue
        for host, inner_path, mode in CREDENTIALS.get(name, []):
            path = Path(host).expanduser()
            if path.exists():
                mounts.append((str(path), inner_path, mode))
            else:
                result.missing_credentials.append(f"{name}: {host}")

    docker_argv = ["docker", "run", "--rm"]
    if interactive:
        docker_argv.append("-it")
    for host, inner_path, mode in mounts:
        docker_argv += ["-v", f"{host}:{inner_path}:{mode}"]
    for name in ("AWS_REGION", "AWS_PROFILE", "AWS_ACCESS_KEY_ID",
                 "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                 # ECS puts the task role behind this; without it a task falls
                 # back to the instance role, which is a different identity.
                 "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                 "AWS_CONTAINER_CREDENTIALS_FULL_URI", "XAI_API_KEY"):
        if os.environ.get(name):
            docker_argv += ["-e", name]
    docker_argv.append(tag)

    result.docker_argv = docker_argv
    result.inner_argv = argv
    result.mounts = mounts
    return result


def _clients_in(argv: list[str]) -> list[str]:
    """Which credential sets this run actually needs."""
    pairs = dict(zip(argv, argv[1:]))
    names = set()
    if "--client" in pairs:
        names.add(pairs["--client"])
    for tier in ("small", "medium", "large"):
        flag = f"--client-{tier}"
        if flag in pairs:
            names.add(pairs[flag])
    if "--multiclient" in argv and not names:
        names |= {"claude", "grok", "opencode"}
    if not names:
        names.add("claude")
    if "--bedrock" in argv:
        names.add("bedrock")
        # Bedrock replaces the subscription, so the subscription's credentials
        # have no reason to be in the container.
        names.discard("claude")
    return sorted(names)
