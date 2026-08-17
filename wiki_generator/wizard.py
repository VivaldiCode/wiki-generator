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

    installed = available_clients()
    usable = [name for name, path in installed.items() if path]
    print("Clients on this machine:")
    for name, path in installed.items():
        mark = "yes" if path else "no "
        hint = "" if path else f"   ({_install_hint(name)})"
        print(f"  {mark}  {name:<9} {path or ''}{hint}")
    if not usable:
        print("\nNo client CLI is installed, so nothing can be generated yet.")
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

    # --- who does the work ------------------------------------------------
    multi = len(repos) > 1 and len(usable) > 1 and _ask_yes(
        f"Split the {len(repos)} repositories across {len(usable)} clients by size",
        default=True,
    )
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
    else:
        client = _ask_choice("Client", usable,
                             "claude" if "claude" in usable else usable[0])
        argv += ["--client", client]
        argv += ["--model", _ask_model(client)]

    if "opencode" in (argv + usable) and not clients.get(
        "opencode"
    ).capabilities.tool_restriction:
        argv.append("--allow-unrestricted-client")

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
        target = Path(_ask("Profile file", str(Path.cwd() / f"wiki{PROFILE_SUFFIX}")))
        save_profile(target, argv)
        print(f"    Saved. Reuse with: wiki-generator --profile {target}")

    print("\nStarting.\n")
    return argv


def _ask_model(client: str) -> str:
    """For opencode, offer the local models that can actually do the job."""
    default = clients.get(client).default_model
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


def should_offer() -> bool:
    """Only when there is a person there to answer."""
    return sys.stdin.isatty() and sys.stdout.isatty()
