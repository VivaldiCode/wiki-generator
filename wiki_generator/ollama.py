"""Local models through Ollama: is it there, can it do the job, and pulling it.

Two failures this exists to catch before a run starts rather than during it.

A model that is not installed surfaces as an opaque provider error from the
client, minutes in. A model that *is* installed but cannot call tools is worse:
the run completes, every page is written, and the wiki has no `file:line`
citations in it at all — because the model was never able to open a file. Ollama
declares both facts (`/api/tags`, `/api/show` → `capabilities`), so neither has
to be guessed at.

Nothing here downloads anything on its own. A pull is gigabytes over someone
else's network; it happens when asked for, and not otherwise.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

BASE_URL = "http://localhost:11434"
PREFIX = "ollama/"

# Tool calling is not a preference here, it is the mechanism: the generator
# never sends the repository to the model, it gives the model tools to read it.
# A model without them writes a wiki about a repository it never opened.
REQUIRED_CAPABILITY = "tools"

# Small, tool-capable, and commonly available. Ordered by how well they held up
# on this workload; the list is a starting point, not a restriction.
RECOMMENDED = (
    "qwen2.5:latest",
    "qwen2.5-coder:latest",
    "llama3.2:latest",
)


class OllamaError(RuntimeError):
    """Ollama is unusable for this run, and retrying will not change it."""


@dataclass
class Model:
    name: str
    size_bytes: int = 0
    capabilities: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return REQUIRED_CAPABILITY in self.capabilities

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1e9


def model_name(model: str) -> str:
    """`ollama/qwen2.5:latest` -> `qwen2.5:latest`."""
    return model[len(PREFIX):] if model.startswith(PREFIX) else model


def is_local(model: str) -> bool:
    return model.startswith(PREFIX)


# ----------------------------------------------------------------------
def _get(path: str, payload: dict | None = None, timeout: int = 10):
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def is_running() -> bool:
    try:
        _get("/api/version", timeout=4)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def installed() -> list[Model]:
    """Every local model, with what it can do. Empty when Ollama is not up."""
    try:
        payload = _get("/api/tags", timeout=8)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    models: list[Model] = []
    for entry in payload.get("models", []):
        name = entry.get("name", "")
        if not name:
            continue
        models.append(
            Model(name=name, size_bytes=int(entry.get("size") or 0),
                  capabilities=tuple(_capabilities(name)))
        )
    return sorted(models, key=lambda m: m.name)


def _capabilities(name: str) -> list[str]:
    try:
        return list(_get("/api/show", {"model": name}, timeout=15).get(
            "capabilities", []
        ))
    except (urllib.error.URLError, OSError, ValueError):
        return []


def usable_models() -> list[Model]:
    return [m for m in installed() if m.usable]


def pull(name: str, on_progress=None) -> None:
    """Download a model. Only ever called after someone asked for it.

    Uses the CLI rather than the API so the download shows the same progress the
    user would see running it themselves, and so a Ctrl-C behaves the same way.
    """
    try:
        process = subprocess.Popen(
            ["ollama", "pull", name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except OSError as exc:
        raise OllamaError(f"could not run `ollama pull`: {exc}") from None
    assert process.stdout is not None
    for line in process.stdout:
        if on_progress:
            on_progress(line.rstrip())
    if process.wait() != 0:
        raise OllamaError(f"`ollama pull {name}` failed")


# ----------------------------------------------------------------------
def check(model: str) -> tuple[str, str]:
    """Classify the situation. Returns (state, detail).

    States: `ok`, `not_running`, `missing`, `no_tools`.
    """
    name = model_name(model)
    if not is_running():
        return "not_running", (
            f"Ollama is not answering on {BASE_URL}. Start it with `ollama serve`."
        )
    local = {m.name: m for m in installed()}
    found = local.get(name) or local.get(f"{name}:latest")
    if found is None:
        return "missing", f"`{name}` is not installed."
    if not found.usable:
        return "no_tools", (
            f"`{found.name}` cannot call tools ({', '.join(found.capabilities) or 'none'}). "
            "The generator gives the model tools to read the repository — without "
            "them it will write pages about code it never opened."
        )
    return "ok", found.name
