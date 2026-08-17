"""What every client is doing right now, on one screen.

With one client the log reads fine: pages arrive in order and the run is the
only thing happening. With three clients working different repositories at once
the same log interleaves into noise — you cannot tell which line belongs to
which agent, nor how much is left.

So each lane owns a line, the lines are redrawn in place, and the run keeps a
single counter of what is done, what is left, and what came back incomplete.
Falls back to plain appended lines when stdout is not a terminal, because that
is what a CloudWatch log or a piped file can actually show.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Active:
    repo: str
    started_at: float
    pages_done: int = 0
    pages_total: int = 0


@dataclass
class Lane:
    """One client. It may be working several repositories at once."""

    client: str
    active: dict = field(default_factory=dict)
    completed: int = 0
    last: str = ""

    def line(self, width: int) -> str:
        if not self.active:
            body = f"idle  ({self.completed} done)" if self.completed else "idle"
            if self.last:
                body += f"   last: {self.last}"
        else:
            # One repository gets named; several get counted, because three
            # names and their page counts do not fit on a line and the count is
            # what tells you the lane is saturated.
            parts = []
            for entry in list(self.active.values())[:2]:
                elapsed = time.monotonic() - entry.started_at
                pages = (f"{entry.pages_done}/{entry.pages_total}"
                         if entry.pages_total else "scan")
                parts.append(f"{entry.repo} {pages} {elapsed / 60:.0f}m")
            more = len(self.active) - len(parts)
            body = "  |  ".join(parts) + (f"  (+{more} more)" if more > 0 else "")
        return f"[{self.client:<9}] {body}"[:width]


class Board:
    """Shared, thread-safe view of the run. Rendered by one ticker."""

    def __init__(self, clients: list[str], total_repos: int, stream=None) -> None:
        self.lanes = {name: Lane(client=name) for name in clients}
        self.total_repos = total_repos
        self.done = 0
        self.failed = 0
        self.incomplete = 0
        self._lock = threading.Lock()
        self._stream = stream or sys.stdout
        self._painted = 0
        self._live = bool(getattr(self._stream, "isatty", lambda: False)())
        self._last_logged = ""

    # ------------------------------------------------------------------
    def start(self, client: str, repo: str, pages_total: int = 0) -> None:
        with self._lock:
            self.lanes[client].active[repo] = Active(
                repo=repo, started_at=time.monotonic(), pages_total=pages_total
            )
        self._announce(f"{client} -> {repo}")

    def plan(self, client: str, pages_total: int, repo: str = "") -> None:
        with self._lock:
            for entry in self._targets(client, repo):
                entry.pages_total = pages_total

    def page(self, client: str, repo: str = "") -> None:
        with self._lock:
            for entry in self._targets(client, repo):
                entry.pages_done += 1

    def _targets(self, client: str, repo: str) -> list:
        """Named repository when known; otherwise the lane's only active one.

        With several repositories in flight an unattributed page cannot be
        assigned, so it is dropped rather than credited to the wrong one.
        """
        lane = self.lanes[client]
        if repo:
            entry = lane.active.get(repo)
            return [entry] if entry else []
        return list(lane.active.values()) if len(lane.active) == 1 else []

    def finish(self, client: str, status: str, detail: str = "",
               repo: str = "") -> None:
        with self._lock:
            lane = self.lanes[client]
            if not repo:
                repo = next(iter(lane.active), "")
            lane.active.pop(repo, None)
            lane.completed += 1
            lane.last = f"{repo} {status}"
            if status == "failed":
                self.failed += 1
            elif status == "incomplete":
                self.incomplete += 1
            else:
                self.done += 1
        self._announce(f"{client} finished {repo}: {status}"
                       + (f" ({detail})" if detail else ""))

    # ------------------------------------------------------------------
    def summary(self) -> str:
        remaining = self.total_repos - self.done - self.failed - self.incomplete
        return (
            f"repositories: {self.done} done, {self.incomplete} incomplete, "
            f"{self.failed} failed, {remaining} left of {self.total_repos}"
        )

    def render(self) -> None:
        """Redraw the lanes in place, or append one status line when piped."""
        width = shutil.get_terminal_size((100, 24)).columns - 1
        with self._lock:
            lines = [lane.line(width) for lane in self.lanes.values()]
            lines.append(f"  {self.summary()}")
        if not self._live:
            # A log file gets a line only when something changed; a terminal
            # gets the live redraw. Repeating an unchanged summary every few
            # seconds is how a CloudWatch log becomes unreadable.
            current = " | ".join(lines)
            if current != self._last_logged:
                self._last_logged = current
                print(f"  {self.summary()}", file=self._stream, flush=True)
            return
        if self._painted:
            self._stream.write(f"\033[{self._painted}A")
        for line in lines:
            self._stream.write("\033[2K" + line + "\n")
        self._stream.flush()
        self._painted = len(lines)

    def _announce(self, message: str) -> None:
        """A durable line above the live area — the part a log file keeps."""
        if self._live and self._painted:
            self._stream.write(f"\033[{self._painted}A\033[J")
            self._painted = 0
        print(f"  {message}", file=self._stream, flush=True)

    def stop(self) -> None:
        if self._live and self._painted:
            self._stream.write("\n")
            self._stream.flush()
        self._painted = 0


class TriageProgress:
    """Live feedback while a large tree is classified.

    The question this answers is "is it stuck?", so the heartbeat matters more
    than the tally: one repository with tens of thousands of files takes long
    enough on its own to look like a hang, and only a clock that keeps moving
    tells the two apart.
    """

    def __init__(self, total: int, stream=None, heartbeat: float = 2.0) -> None:
        self.total = total
        self._stream = stream or sys.stdout
        self._live = bool(getattr(self._stream, "isatty", lambda: False)())
        self._heartbeat = heartbeat
        self._lock = threading.Lock()
        self._current = ""
        self._started = 0.0
        self._index = 0
        self._counts: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "TriageProgress":
        if self._live:
            self._thread = threading.Thread(target=self._beat, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self._live:
            self._stream.write("\r\033[2K")
            self._stream.flush()

    # ------------------------------------------------------------------
    def __call__(self, phase: str, index: int, total: int, name: str, state) -> None:
        if phase == "start":
            with self._lock:
                self._current, self._started, self._index = name, time.monotonic(), index
            if self._live:
                self._paint()
            return
        with self._lock:
            self._counts[state.state] = self._counts.get(state.state, 0) + 1
            elapsed = time.monotonic() - self._started
        if not self._live:
            # Piped: one line each, so the log holds the whole classification.
            print(f"  [{index}/{total}] {name} — {state.state} ({elapsed:.1f}s)",
                  file=self._stream, flush=True)
        elif elapsed >= 5.0:
            # On a terminal only the slow ones earn a permanent line.
            self._stream.write("\r\033[2K")
            print(f"  [{index}/{total}] {name} — {state.state} ({elapsed:.0f}s)",
                  file=self._stream, flush=True)

    def _beat(self) -> None:
        while not self._stop.wait(self._heartbeat):
            self._paint()

    def _paint(self) -> None:
        with self._lock:
            if not self._current:
                return
            elapsed = time.monotonic() - self._started
            tally = " ".join(f"{k}:{v}" for k, v in sorted(self._counts.items()))
            line = (f"  [{self._index}/{self.total}] {self._current}  "
                    f"{elapsed:.0f}s   {tally}") if self._current else (
                    f"  [{self._index}/{self.total}]   {tally}")
        width = shutil.get_terminal_size((100, 24)).columns - 1
        self._stream.write("\r\033[2K" + line[:width])
        self._stream.flush()
