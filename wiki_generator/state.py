"""Which repositories are done, which are half-done, and which client gets each.

A long multi-repository run is not one job, it is dozens, and the expensive
mistake is redoing one that was already finished. This module answers that
question without calling a model: every signal it uses is on disk.

Three states, and the boundary between them is what matters:

  done        every planned page exists, its fingerprint still matches, and the
              wiki carries no failure marker. Never touched again.
  incomplete  a wiki exists but something is missing, stale, interrupted, or
              recorded as failed. Resume it.
  untouched   no wiki. Start from zero.

The routing rule is size, because size is what the clients differ on: a fast
cheap client on the small repositories, the one that stays coherent over a large
tree on the giant ones, and the default in between.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import STRUCTURE_VERSION, __version__
from .cartography import build_graph, graph_context
from .config import WikiConfig
from .generator import MANIFEST_NAME, Manifest, _fingerprint
from .i18n import STRINGS, translator
from .interfaces import detect as detect_interfaces
from .journal import STATE_FILE as RUN_MARKER, iter_pages
from .planner import build_plan
from .scanner import EmptyRepositoryError, count_repo_files, scan_repo, substance

CONTROL_FILE = "wiki-control.json"

DONE = "done"
INCOMPLETE = "incomplete"
UNTOUCHED = "untouched"
SKIPPED = "skipped"

# What a failed page leaves behind in the index, per client. The generator writes
# the CLI's own diagnostic, so the marker is whatever that CLI says when it dies.
FAILURE_MARKERS = (
    "exited with code",
    "Could not load credentials",
    "Failed after",
    "Timed out after",
    "Not signed in",
    "token refresh failed",
    "API Error",
    "no diagnostic",
)


@dataclass
class Routing:
    """Size decides the client, because size is what the clients differ on."""

    small_max_files: int = 200
    large_min_files: int = 2000
    small: str = "opencode"
    medium: str = "claude"
    large: str = "grok"

    def client_for(self, files: int) -> str:
        if files <= self.small_max_files:
            return self.small
        if files >= self.large_min_files:
            return self.large
        return self.medium


@dataclass
class RepoState:
    name: str
    path: str
    wiki_path: str
    files: int
    client: str
    state: str
    reason: str = ""
    pages_expected: int = 0
    pages_present: int = 0
    pages_stale: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def needs_work(self) -> bool:
        return self.state in {INCOMPLETE, UNTOUCHED}


# ----------------------------------------------------------------------
ERROR = "error"


def triage(
    config: WikiConfig, repos: list[Path], routing: Routing,
    output_root: Path | None = None, on_event=None, workers: int = 1,
) -> list[RepoState]:
    """Classify every repository. Deterministic: no model is called.

    `on_event(phase, index, total, name, state)` is called before and after each
    repository. Over a thousand repositories this is the difference between a
    long silence and a run you can see the shape of — and each repository costs a
    full scan, so the silence is minutes, not seconds.
    """
    total = len(repos)

    def classify(repo: Path, index: int = 0) -> RepoState:
        if on_event and workers > 1:
            # Without this the parallel path reported only completions, so a
            # repository that takes ten minutes showed nothing at all — which is
            # exactly the moment the question "is it stuck?" gets asked.
            on_event("start", index, total, repo.name, None)
        try:
            return _triage_one(config, repo, routing, output_root)
        except Exception as exc:  # noqa: BLE001 - one bad repo must not stop 1499
            return RepoState(
                name=repo.name, path=str(repo), wiki_path="", files=0,
                client=routing.medium, state=ERROR,
                reason=f"could not be classified: {exc}"[:200],
            )

    if workers <= 1:
        states: list[RepoState] = []
        for index, repo in enumerate(repos, start=1):
            if on_event:
                on_event("start", index, total, repo.name, None)
            state = classify(repo)
            states.append(state)
            if on_event:
                on_event("done", index, total, repo.name, state)
        return states

    # Classifying is walking and hashing files — I/O, not computation — so
    # threads help despite the GIL, and on a tree of thousands this is the
    # difference between minutes and an hour. Results are put back in the input
    # order, so the report does not depend on which repository finished first.
    from concurrent.futures import ThreadPoolExecutor

    done = 0
    lock = threading.Lock()
    results: dict[int, RepoState] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(classify, repo, i + 1): (i, repo)
                   for i, repo in enumerate(repos)}
        for future in as_completed(futures):
            index, repo = futures[future]
            state = future.result()
            results[index] = state
            with lock:
                done += 1
                position = done
            if on_event:
                on_event("done", position, total, repo.name, state)
    return [results[i] for i in range(len(repos))]


def _wiki_path(config: WikiConfig, repo: Path, output_root: Path | None) -> Path:
    return Path(output_root) / repo.name if output_root else repo / "wiki"


def _triage_one(
    config: WikiConfig, repo: Path, routing: Routing, output_root: Path | None
) -> RepoState:
    from dataclasses import replace as dc_replace

    files = count_repo_files(repo)
    client = routing.client_for(files)
    wiki = _wiki_path(config, repo, output_root)
    base = RepoState(
        name=repo.name, path=str(repo), wiki_path=str(wiki),
        files=files, client=client, state=UNTOUCHED,
    )

    # An interrupted run is decided before anything else: the next run rolls it
    # back, so whatever is on disk now is not what will survive.
    if wiki.is_dir() and (wiki / RUN_MARKER).is_file():
        base.state = INCOMPLETE
        base.reason = "a previous run did not finish (it will be rolled back)"
        return base

    # A finished wiki is judged against whatever wrote it, not against the
    # client routing would pick today. "Done is never touched again" has to
    # survive a change of routing, or every rebalance re-bills the whole tree.
    manifest = Manifest(wiki / MANIFEST_NAME)
    wrote_client, wrote_model = manifest.provenance()
    # A manifest with no provenance was written before this version of the tool.
    # Its fingerprints cannot be compared with today's: the prompt text is hashed
    # into them and the prompts have changed since, so *every* page of *every*
    # older wiki would read as stale no matter what client or language it is
    # checked against. Comparing them is not conservative, it is meaningless —
    # and acting on it means regenerating hundreds of pages that are fine.
    #
    # So an older wiki is judged on what can still be judged: are all the pages
    # there, and did anything fail. What it was written with is read off the
    # index for the record, not to compare against.
    legacy = not wrote_client and not wrote_model
    wrote_language = ""
    if legacy:
        wrote_model, wrote_language = provenance_from_index(wiki)
        wrote_client = "claude"  # the only client that existed then

    # Planning needs a scan, which is the expensive part — so it happens only
    # for repositories that have a wiki worth comparing against.
    scoped = dc_replace(
        config, repo_path=repo, output_path=wiki, project_name=None,
        client=wrote_client or client,
        model=wrote_model or config.model,
        language=wrote_language or config.language,
    )
    try:
        scan = scan_repo(scoped)
    except EmptyRepositoryError as exc:
        base.state = SKIPPED
        base.reason = str(exc)
        return base

    lines, count = substance(scan)
    if scoped.min_lines > 0 and lines < scoped.min_lines:
        base.state = SKIPPED
        base.reason = (
            f"only {lines} line(s) across {count} file(s), below --min-lines"
        )
        return base

    # The graph context goes into every page's prompt, and the prompt is hashed
    # into the fingerprint — so triage has to rebuild it exactly as a real run
    # does, or every page looks stale. Deterministic and modelless, like the rest
    # of this module.
    graph_ctx = ""
    if scoped.extra.get("cartography", True):
        graph_ctx = graph_context(build_graph(scan, scoped))
    # Same reason, and the same cost: detection decides which interface pages
    # the plan contains, so a triage that skipped it would count pages the run
    # will generate as pages the run did not.
    iscan = detect_interfaces(scan, scoped) if scoped.interfaces else None
    specs = build_plan(scan, scoped, graph_ctx, iscan)
    base.pages_expected = len(specs)

    # Only now, with the scan done, is "untouched" a fact rather than an
    # absence: a repository with nothing to document is skipped, not pending,
    # and classifying it as pending leaves a run that can never reach zero.
    if not wiki.is_dir() or not any(wiki.glob("**/*.md")):
        base.reason = "no wiki on disk"
        return base

    present = stale = 0
    for spec in specs:
        if not (wiki / spec.path).is_file():
            continue
        present += 1
        if legacy:
            continue
        if manifest.get(spec.key) != _fingerprint(spec, scan, scoped):
            stale += 1

    base.pages_present = present
    base.pages_stale = stale
    base.failures = _failures_in(wiki)

    if base.failures:
        base.state = INCOMPLETE
        base.reason = f"{len(base.failures)} page(s) recorded as failed"
    elif present < len(specs):
        base.state = INCOMPLETE
        base.reason = f"{len(specs) - present} of {len(specs)} pages missing"
    elif stale:
        base.state = INCOMPLETE
        base.reason = f"{stale} of {len(specs)} pages out of date"
    else:
        base.state = DONE
        written_by = f"{wrote_client}/{wrote_model}" if wrote_client else "an earlier run"
        base.reason = (
            f"all {len(specs)} pages present, written by {written_by} before this "
            "version — not re-checked against today's prompts"
            if legacy else
            f"all {len(specs)} pages present and current, by {written_by}"
        )
        # Report who owns it, not who would be assigned: nothing is going to run.
        base.client = wrote_client or client
    return base


def provenance_from_index(wiki: Path) -> tuple[str, str]:
    """The model and language a wiki was written with, read from its index.

    Both are in the fingerprint, so recovering only the model would still leave
    a Portuguese wiki looking stale under the default English. The row label is
    itself translated, which is what makes the language recoverable: whichever
    language's label matches is the language the wiki was written in.
    """
    index = wiki / "README.md"
    if not index.is_file():
        return "", ""
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    labels = {translator(lang)("index.model"): lang for lang in STRINGS}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] in labels:
            return cells[1].strip("`").strip(), labels[cells[0]]
    return "", ""


def model_in_index(wiki: Path) -> str:
    return provenance_from_index(wiki)[0]


def _failures_in(wiki: Path) -> list[str]:
    """Pages the index records as failed, whatever the client called the error.

    Only the index is read: a page that failed was never written, so the record
    of it lives in the index's failure section and nowhere else.
    """
    index = wiki / "README.md"
    if not index.is_file():
        return []
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return []
    if "### " not in text:
        return []
    found: list[str] = []
    for line in text.splitlines():
        if any(marker in line for marker in FAILURE_MARKERS):
            found.append(" ".join(line.split())[:160])
    return found


# ----------------------------------------------------------------------
def load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def restore(path: Path) -> tuple[list[RepoState], str]:
    """Rehydrate a saved triage. Returns the states and when it was taken.

    On a large tree classification costs hours, so the run has to be able to
    start from one already done rather than paying for it again.
    """
    payload = load(path)
    if payload.get("schema") != 1:
        return [], ""
    fields = {f for f in RepoState.__dataclass_fields__}
    states = []
    for entry in payload.get("repos", []):
        try:
            states.append(RepoState(**{k: v for k, v in entry.items() if k in fields}))
        except TypeError:
            continue
    return states, payload.get("updated_at", "")


def retriage_one(
    config: WikiConfig, state: RepoState, routing: Routing,
    output_root: Path | None,
) -> RepoState:
    """Re-classify a single repository, after work was done on it."""
    try:
        return _triage_one(config, Path(state.path), routing, output_root)
    except Exception as exc:  # noqa: BLE001
        state.state = ERROR
        state.reason = f"could not be re-classified: {exc}"[:200]
        return state


def save(path: Path, states: list[RepoState], routing: Routing, config: WikiConfig,
         source: Path, output_root: Path | None) -> Path:
    """Write the control file. It is a record, never an input to generation."""
    payload = {
        "schema": 1,
        "wiki_generator_version": __version__,
        "structure_version": STRUCTURE_VERSION,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(source),
        "output": str(output_root) if output_root else None,
        "routing": asdict(routing),
        "totals": totals(states),
        "repos": [asdict(state) for state in states],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


def totals(states: list[RepoState]) -> dict:
    counts = {DONE: 0, INCOMPLETE: 0, UNTOUCHED: 0, SKIPPED: 0, ERROR: 0}
    by_client: dict[str, int] = {}
    for state in states:
        counts[state.state] = counts.get(state.state, 0) + 1
        if state.needs_work:
            by_client[state.client] = by_client.get(state.client, 0) + 1
    return {"repositories": len(states), **counts, "pending_by_client": by_client}
