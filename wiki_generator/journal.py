"""Run journal: a generation either completes or leaves nothing behind.

A run that dies halfway — Ctrl-C, a crash, a laptop lid — leaves a wiki whose
index, cartography and pages disagree with each other. That half-state is worse
than no wiki at all, because nothing about it says it is half: the pages that did
land look finished.

So a run is transactional. Before touching anything, the current wiki is copied
aside and a marker is written. The marker is only cleared on a clean finish, so
finding one at startup is proof the previous run did not complete, and the copy
is restored.

Granularity is one repository. In a multi-repo run, a crash on the fourth repo
rolls back the fourth only — the three already committed stay committed, which is
what you want: they are complete and correct.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

# Both live inside the output directory so the state travels with the wiki and
# survives a reboot. Every reader of the wiki must skip them — see EXCLUDED_DIRS.
STATE_FILE = ".wiki-run.json"
BACKUP_DIR = ".wiki-backup"

# Directories inside a wiki that are bookkeeping, not content.
EXCLUDED_DIRS = frozenset({BACKUP_DIR})


def is_internal(path: Path, wiki_root: Path) -> bool:
    """True for bookkeeping paths that are not wiki content."""
    try:
        parts = path.relative_to(wiki_root).parts
    except ValueError:
        return False
    return bool(parts) and parts[0] in EXCLUDED_DIRS


def iter_pages(wiki_root: Path) -> list[Path]:
    """Every markdown page in the wiki, excluding bookkeeping directories."""
    return sorted(
        p for p in wiki_root.rglob("*.md") if not is_internal(p, wiki_root)
    )


@dataclass
class RecoveryReport:
    recovered: bool
    started_at: str = ""
    restored_files: int = 0
    removed_files: int = 0
    wiki_existed: bool = True


class RunJournal:
    """Makes one repository's generation atomic: all of it, or none of it."""

    def __init__(self, output_path: Path) -> None:
        self.output = Path(output_path)
        self.state_path = self.output / STATE_FILE
        self.backup_path = self.output / BACKUP_DIR

    # ------------------------------------------------------------------
    def _read_state(self) -> dict | None:
        if not self.state_path.is_file():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # An unreadable marker still means a run was interrupted — treat it
            # as such rather than assuming everything is fine.
            return {"state": "in_progress", "started_at": "unknown"}

    def pending(self) -> dict | None:
        """The state of an interrupted previous run, or None if the last run was clean."""
        state = self._read_state()
        if state and state.get("state") == "in_progress":
            return state
        return None

    # ------------------------------------------------------------------
    def recover(self) -> RecoveryReport:
        """Restore the wiki to its pre-run state if the previous run was interrupted."""
        state = self.pending()
        if not state:
            return RecoveryReport(recovered=False)

        wiki_existed = bool(state.get("wiki_existed", True))
        if not wiki_existed:
            # There was no wiki before: rolling back means removing what was made.
            removed = len(iter_pages(self.output)) if self.output.is_dir() else 0
            shutil.rmtree(self.output, ignore_errors=True)
            return RecoveryReport(
                recovered=True,
                started_at=str(state.get("started_at", "")),
                removed_files=removed,
                wiki_existed=False,
            )

        restored = removed = 0
        if self.backup_path.is_dir():
            # Clear the current content, then put the backup back. Bookkeeping
            # files are handled separately so the backup is never destroyed.
            for entry in list(self.output.iterdir()):
                if entry.name in (STATE_FILE, BACKUP_DIR):
                    continue
                removed += 1
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            for entry in list(self.backup_path.iterdir()):
                target = self.output / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, target)
                else:
                    shutil.copy2(entry, target)
                restored += 1

        shutil.rmtree(self.backup_path, ignore_errors=True)
        self.state_path.unlink(missing_ok=True)
        return RecoveryReport(
            recovered=True,
            started_at=str(state.get("started_at", "")),
            restored_files=restored,
            removed_files=removed,
        )

    # ------------------------------------------------------------------
    def begin(self, metadata: dict | None = None) -> None:
        """Snapshot the current wiki and mark a run as in progress."""
        wiki_existed = self.output.is_dir() and any(
            entry.name not in (STATE_FILE, BACKUP_DIR)
            for entry in self.output.iterdir()
        )

        shutil.rmtree(self.backup_path, ignore_errors=True)
        if wiki_existed:
            self.backup_path.mkdir(parents=True, exist_ok=True)
            for entry in self.output.iterdir():
                if entry.name in (STATE_FILE, BACKUP_DIR):
                    continue
                target = self.backup_path / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, target)
                else:
                    shutil.copy2(entry, target)

        self.output.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "state": "in_progress",
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "wiki_existed": wiki_existed,
                    **(metadata or {}),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def commit(self) -> None:
        """Mark the run complete and drop the snapshot."""
        shutil.rmtree(self.backup_path, ignore_errors=True)
        self.state_path.unlink(missing_ok=True)

    def abort(self) -> RecoveryReport:
        """Roll back immediately — used when a run fails inside this process."""
        return self.recover()
