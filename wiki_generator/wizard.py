"""The interactive setup, for `wiki-generator` with no arguments.

The flag surface is large because the tool grew capable, and a first-time user
should not have to read `--help` to find out that three clients exist, that one
of them is not installed on this machine, and that a wiki already generated will
not be regenerated. So: ask, check, and offer to save the answers.

Everything it asks is something the run genuinely cannot infer. Everything it
can check — which CLIs exist, whether a path is a repository, how many
repositories are under a directory — it checks instead of asking.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import clients, ollama
from .scanner import count_repo_files, find_repositories

PROFILE_SUFFIX = ".wiki-profile.json"
# Saved answers go somewhere findable from any directory. A profile written to
# whatever directory you happened to be in is a profile you will not find again,
# which makes "save this to reuse later" a promise the tool does not keep.
PROFILE_DIR = Path.home() / ".config" / "wiki-generator"
DEFAULT_PROFILE = PROFILE_DIR / "profile.json"


class Cancelled(Exception):
    """The user stopped the wizard."""


def available_clients() -> dict[str, str | None]:
    """Which client CLIs are actually on this machine."""
    return {
        name: shutil.which(client.binary)
        for name, client in sorted(clients.CLIENTS.items())
    }


# ----------------------------------------------------------------------
def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise Cancelled from None
    return answer or default


def _ask_choice(prompt: str, options: list[str], default: str) -> str:
    while True:
        answer = _ask(f"{prompt} ({'/'.join(options)})", default)
        if answer in options:
            return answer
        print(f"    Pick one of: {', '.join(options)}")


def _ask_yes(prompt: str, default: bool = True) -> bool:
    return _ask_choice(prompt, ["y", "n"], "y" if default else "n") == "y"


def _ask_int(prompt: str, default: int) -> int:
    while True:
        answer = _ask(prompt, str(default))
        try:
            return int(answer)
        except ValueError:
            print("    A whole number, please.")


def _ask_path(prompt: str, default: str = "", must_exist: bool = True) -> Path:
    while True:
        answer = _ask(prompt, default)
        if not answer:
            print("    A path is required.")
            continue
        path = Path(answer).expanduser().resolve()
        if must_exist and not path.exists():
            print(f"    {path} does not exist.")
            continue
        return path


# ----------------------------------------------------------------------
def run() -> list[str]:
    """Ask what to do and return the argv for it. Raises Cancelled on Ctrl-C."""
    print("wiki-generator — interactive setup")
    print("Press Ctrl-C at any point to stop.\n")

    in_docker = _ask_where()
    if in_docker:
        # Every client is in the image, so what is installed here stops mattering.
        installed = {name: "in the image" for name in sorted(clients.CLIENTS)}
        usable = sorted(clients.CLIENTS)
        print("Clients (from the container image):")
        for name in usable:
            print(f"  yes  {name}")
        print()
    else:
        installed = available_clients()
        usable = [name for name, path in installed.items() if path]
        print("Clients on this machine:")
        for name, path in installed.items():
            mark = "yes" if path else "no "
            hint = "" if path else f"   ({_install_hint(name)})"
            print(f"  {mark}  {name:<9} {path or ''}{hint}")
        if not usable:
            print("\nNo client CLI is installed here. Running in Docker would "
                  "supply all three.")
            raise Cancelled
        print()

    # --- what to document -------------------------------------------------
    source = _ask_path("Repository, or a folder containing several", str(Path.cwd()))
    repos = find_repositories(source)
    if len(repos) > 1:
        print(f"    {len(repos)} git repositories found under {source}.")
    elif repos:
        print(f"    One repository: {repos[0].name} "
              f"({count_repo_files(repos[0])} files).")
    else:
        print(f"    No git repository found under {source}; it will be treated "
              "as a single tree.")

    output = _ask_path(
        "Where the wikis go (one folder per repository)",
        str(source.parent / "wikis"), must_exist=False,
    )

    argv = ["--source" if len(repos) > 1 else "--repo", str(source),
            "--output", str(output)]
    if in_docker:
        argv.append("--docker")

    # --- who does the work ------------------------------------------------
    multi = len(repos) > 1 and len(usable) > 1 and _ask_yes(
        f"Split the {len(repos)} repositories across {len(usable)} clients by size",
        default=True,
    )
    tiers: list[str] = []
    if multi:
        argv.append("--multiclient")
        small = _ask_int("Small repositories are at or below this many files", 200)
        large = _ask_int("Large repositories are at or above this many files", 2000)
        argv += ["--small-max-files", str(small), "--large-min-files", str(large)]
        for tier, default in (("small", "opencode"), ("medium", "claude"),
                              ("large", "grok")):
            pick = _ask_choice(f"Client for {tier} repositories", usable,
                               default if default in usable else usable[0])
            argv += [f"--client-{tier}", pick]
            tiers.append((tier, pick))
    else:
        client = _ask_choice("Client", usable,
                             "claude" if "claude" in usable else usable[0])
        argv += ["--client", client]
        tiers.append(("", client))

    if "opencode" in (argv + usable) and not clients.get(
        "opencode"
    ).capabilities.tool_restriction:
        argv.append("--allow-unrestricted-client")

    # Bedrock is asked before any model, because it changes what a model is
    # called: the subscription takes aliases like `haiku`, Bedrock takes a full
    # inference-profile id. Asking in the other order offers an alias and then
    # silently keeps it.
    names = [name for _, name in tiers]
    bedrock = _ask_bedrock(multi) if "claude" in names else []
    argv += bedrock

    on_bedrock = bool(bedrock)
    for tier, name in tiers:
        model = _ask_model(name, bedrock=on_bedrock and name == "claude")
        if not model:
            continue
        argv += ([f"--model-{tier}", model] if tier else ["--model", model])

    # --- how ---------------------------------------------------------------
    language = _ask("Wiki language (en, pt, pt-br)", "en")
    argv += ["--language", language]
    concurrency = _ask_int("Pages generated at once, per repository", 4)
    argv += ["--concurrency", str(concurrency)]
    min_lines = _ask_int("Skip repositories with fewer content lines than", 50)
    argv += ["--min-lines", str(min_lines)]

    if _ask_yes("Check the finished wiki's claims against the code (--verify), "
                "slow and costly", default=False):
        argv.append("--verify")

    # --- keep it -----------------------------------------------------------
    print()
    if _ask_yes("Save these answers to reuse later", default=True):
        target = Path(_ask("Profile file", str(DEFAULT_PROFILE)))
        try:
            written = save_profile(target, argv)
        except OSError as exc:
            print(f"    Could not save: {exc}")
        else:
            print(f"    Saved to {written}")
            print("    The next run with no arguments will offer to reuse it.")

    print("\nStarting.\n")
    return argv


def _ask_where() -> bool:
    """Local machine, or the container image. Asked first because it changes
    which clients are even available."""
    from . import dockerrun

    ok, detail = dockerrun.available()
    if not ok:
        print(f"Docker is not usable here ({detail}); running locally.\n")
        return False

    have = dockerrun.image_exists()
    state = ("the image is already built" if have
             else "the image will be built on first run, which takes a few minutes")
    print("Where should this run?")
    print(f"  1. On this machine — uses the client CLIs installed here")
    print(f"  2. In Docker — every client already installed ({state})")
    choice = _ask_choice("Choose", ["1", "2"], "1")
    print()
    return choice == "2"


def _ask_model(client: str, bedrock: bool = False) -> str:
    """The model for one client, asked in the terms that client actually uses."""
    default = clients.get(client).default_model
    if bedrock:
        print("    On Bedrock a model is named by its full inference-profile id,")
        print("    not by an alias — for example:")
        print("      us.anthropic.claude-haiku-4-5-20251001-v1:0")
        print("    `aws bedrock list-inference-profiles` shows what your account")
        print("    has been granted in this region.")
        return _ask("Bedrock model id", default)
    if client != "opencode" or not ollama.is_running():
        return _ask("Model", default)

    local = ollama.usable_models()
    unusable = [m for m in ollama.installed() if not m.usable]
    if local:
        print("    Local models that can call tools (needed to read the repo):")
        for model in local:
            print(f"      {model.name:<26} {model.size_gb:.1f} GB")
    if unusable:
        print("    Installed but unusable here (no tool calling): "
              + ", ".join(m.name for m in unusable))
    if not local:
        print("    None of the installed local models can call tools.")

    # Walk the recommendations in preference order, not the installed list in
    # alphabetical order — otherwise the ordering of RECOMMENDED means nothing.
    names = {m.name for m in local}
    suggestion = next(
        (name for name in ollama.RECOMMENDED if name in names),
        local[0].name if local else ollama.RECOMMENDED[0],
    )
    answer = _ask("Model (a name not listed will be pulled)",
                  ollama.PREFIX + suggestion)
    return answer if "/" in answer else ollama.PREFIX + answer


def _ask_bedrock(multi: bool) -> list[str]:
    """Offer Amazon Bedrock for the Claude client, and settle the region here.

    A missing region is the failure that otherwise surfaces on the first model
    call, tens of minutes into a run — so it is asked for now, with whatever the
    environment already resolves as the default.
    """
    from . import providers

    scope = "the Claude lanes" if multi else "Claude"
    if not _ask_yes(f"Run {scope} on Amazon Bedrock instead of the subscription",
                    default=False):
        return []

    resolved = providers.resolved_region(None)
    if resolved:
        print(f"    Region from your environment: {resolved}")
    region = _ask("AWS region", resolved or "us-east-1")

    if providers.has_credentials():
        print("    AWS credentials found; the usual chain will be used.")
    else:
        print("    No AWS credentials in the environment or ~/.aws. That is "
              "normal on EC2/ECS/EKS, where the role is resolved at call time, "
              "and a misconfiguration anywhere else.")
    print("    Model access is granted per region in the Bedrock console; a "
          "model you have not been granted returns AccessDeniedException.")
    return ["--bedrock", "--aws-region", region]


def _install_hint(name: str) -> str:
    return {
        "claude": "npm i -g @anthropic-ai/claude-code",
        "grok": "see grok's install docs",
        "opencode": "see opencode's install docs",
    }.get(name, "not installed")


# ----------------------------------------------------------------------
def save_profile(path: Path, argv: list[str]) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "argv": argv}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_profile(path: Path) -> list[str]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    argv = data.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ValueError(f"{path} does not hold a saved profile.")
    return argv


def find_profiles() -> list[Path]:
    """Saved profiles, nearest first: this directory, then the user's own."""
    found = sorted(Path.cwd().glob(f"*{PROFILE_SUFFIX}"))
    if PROFILE_DIR.is_dir():
        found += sorted(p for p in PROFILE_DIR.glob("*.json") if p not in found)
    return found


def describe(argv: list[str]) -> str:
    """A one-line account of what a saved profile will do."""
    pairs = dict(zip(argv, argv[1:]))
    parts = []
    for flag in ("--repo", "--source"):
        if flag in pairs:
            parts.append(f"{flag[2:]} {pairs[flag]}")
    if "--output" in pairs:
        parts.append(f"-> {pairs['--output']}")
    if "--multiclient" in argv:
        tiers = [f"{t}:{pairs.get(f'--client-{t}', '?')}"
                 for t in ("small", "medium", "large")]
        parts.append("multiclient " + " ".join(tiers))
    elif "--client" in pairs:
        parts.append(f"{pairs['--client']}/{pairs.get('--model', 'default')}")
    if "--verify" in argv:
        parts.append("--verify")
    return "  ".join(parts) or " ".join(argv[:6])


def offer_saved(profiles: list[Path]) -> list[str] | None:
    """Ask whether to reuse a saved run. Returns its argv, or None to start fresh."""
    print("Saved runs found:\n")
    for index, path in enumerate(profiles, start=1):
        try:
            argv = load_profile(path)
        except (OSError, ValueError, json.JSONDecodeError):
            print(f"  {index}. {path}  (unreadable — will be ignored)")
            continue
        print(f"  {index}. {path.name}")
        print(f"     {describe(argv)}")
    print(f"  {len(profiles) + 1}. set up a new run\n")

    while True:
        answer = _ask("Choose", "1")
        if answer.isdigit() and 1 <= int(answer) <= len(profiles):
            try:
                return load_profile(profiles[int(answer) - 1])
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"    Could not read it: {exc}")
                return None
        if answer.isdigit() and int(answer) == len(profiles) + 1:
            return None
        print(f"    A number from 1 to {len(profiles) + 1}.")


def should_offer() -> bool:
    """Only when there is a person there to answer."""
    return sys.stdin.isatty() and sys.stdout.isatty()
