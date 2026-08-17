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
class Lane:
    """One client, working one repository at a time."""

    client: str
    repo: str = ""
    pages_done: int = 0
    pages_total: int = 0
    started_at: float = 0.0
    status: str = "idle"        # idle | working | done | failed
    detail: str = ""
    completed: int = 0          # repositories this lane finished

    def line(self, width: int) -> str:
        if self.status == "idle":
            body = "idle"
        elif self.status == "working":
            elapsed = time.monotonic() - self.started_at if self.started_at else 0
            pages = (f"{self.pages_done}/{self.pages_total} pages"
                     if self.pages_total else "scanning")
            body = f"{self.repo}  {pages}  {elapsed / 60:.0f}m"
        else:
            body = f"{self.repo}  {self.status}{f' — {self.detail}' if self.detail else ''}"
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
            lane = self.lanes[client]
            lane.repo, lane.pages_total, lane.pages_done = repo, pages_total, 0
            lane.started_at, lane.status, lane.detail = time.monotonic(), "working", ""
        self._announce(f"{client} -> {repo}")

    def plan(self, client: str, pages_total: int) -> None:
        with self._lock:
            self.lanes[client].pages_total = pages_total

    def page(self, client: str) -> None:
        with self._lock:
            self.lanes[client].pages_done += 1

    def finish(self, client: str, status: str, detail: str = "") -> None:
        with self._lock:
            lane = self.lanes[client]
            lane.status, lane.detail = status, detail
            lane.completed += 1
            if status == "failed":
                self.failed += 1
            elif status == "incomplete":
                self.incomplete += 1
            else:
                self.done += 1
            repo = lane.repo
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
