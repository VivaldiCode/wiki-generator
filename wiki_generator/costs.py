"""What each run consumed, written next to the wiki so it survives the container.

On a laptop the cost line in the terminal is enough. On ECS the process exits,
the logs roll over, and the only thing that outlives the task is the volume — so
the ledger lives there.

One file per run, never appended to. Two tasks writing the same volume at the
same time is normal (one per repository, fanned out), and an append-and-rewrite
ledger loses records to that race. A directory of immutable records does not,
and it aggregates with a glob.

Tokens are recorded even when cost is not, because on Bedrock cost frequently
is not: the account is billed directly and the CLI reports `0`. A record with
tokens can be priced afterwards from the provider's rate card; a record with a
`0` that means "unknown" can only mislead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import STRUCTURE_VERSION, __version__
from .claude_client import TokenUsage

COSTS_DIR = ".wiki-costs"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Stage:
    """One priced phase of a run: page generation, or verification."""

    cost_usd: float = 0.0
    cost_reported: bool = True
    calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict:
        return {
            # Null rather than 0.0 when the backend priced nothing: a reader
            # summing these must not silently add "unknown" as "free".
            "cost_usd": round(self.cost_usd, 6) if self.cost_reported else None,
            "cost_reported": self.cost_reported,
            "calls": self.calls,
            "tokens": self.usage.to_dict(),
        }


@dataclass
class RunRecord:
    repo: str
    repo_path: str
    wiki_path: str
    status: str               # generated | cached | skipped | failed
    provider: str
    model: str
    aws_region: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    duration_s: float = 0.0
    pages_generated: int = 0
    pages_cached: int = 0
    pages_failed: int = 0
    skip_reason: str = ""
    generation: Stage = field(default_factory=Stage)
    verification: Stage | None = None

    def to_dict(self) -> dict:
        stages = [self.generation] + ([self.verification] if self.verification else [])
        priced = [s for s in stages if s.cost_reported]
        total_usage = TokenUsage()
        for stage in stages:
            total_usage += stage.usage
        return {
            "schema": 1,
            "wiki_generator_version": __version__,
            "structure_version": STRUCTURE_VERSION,
            "repo": self.repo,
            "repo_path": self.repo_path,
            "wiki_path": self.wiki_path,
            "status": self.status,
            "skip_reason": self.skip_reason or None,
            "provider": self.provider,
            "model": self.model,
            "aws_region": self.aws_region,
            "started_at": self.started_at,
            "finished_at": self.finished_at or _now(),
            "duration_s": round(self.duration_s, 1),
            "pages": {
                "generated": self.pages_generated,
                "cached": self.pages_cached,
                "failed": self.pages_failed,
            },
            "generation": self.generation.to_dict(),
            "verification": self.verification.to_dict() if self.verification else None,
            # The figure a reader wants first. Null when any priced stage went
            # unpriced, so a partial total is never mistaken for a full one.
            "total_cost_usd": (
                round(sum(s.cost_usd for s in stages), 6)
                if len(priced) == len(stages) else None
            ),
            "total_tokens": total_usage.to_dict(),
        }


def write(record: RunRecord, output_root: Path) -> Path:
    """Write one immutable record. Never raises: accounting must not fail a run."""
    directory = Path(output_root) / COSTS_DIR / _safe(record.repo)
    stamp = record.finished_at or _now()
    # The pid disambiguates two tasks finishing the same repository in the same
    # second — rare, and silent data loss if it happens.
    name = f"{stamp.replace(':', '')}-{os.getpid()}.json"
    target = directory / name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise CostWriteError(str(exc)) from None
    return target


class CostWriteError(RuntimeError):
    """The ledger could not be written. Reported, never fatal."""


def _safe(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_." else "-" for c in name]
    return "".join(keep).strip("-") or "repo"


# ----------------------------------------------------------------------
def summarize(output_root: Path) -> dict:
    """Aggregate every record on the volume. Used by `--costs-report`."""
    root = Path(output_root) / COSTS_DIR
    runs: list[dict] = []
    for path in sorted(root.glob("*/*.json")):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    by_repo: dict[str, dict] = {}
    total = TokenUsage()
    total_usd = 0.0
    unpriced = 0
    for run in runs:
        entry = by_repo.setdefault(
            run.get("repo", "?"),
            {"runs": 0, "cost_usd": 0.0, "unpriced_runs": 0, "tokens": TokenUsage()},
        )
        entry["runs"] += 1
        cost = run.get("total_cost_usd")
        if cost is None:
            entry["unpriced_runs"] += 1
            unpriced += 1
        else:
            entry["cost_usd"] += cost
            total_usd += cost
        tokens = run.get("total_tokens") or {}
        usage = TokenUsage(**{k: int(v or 0) for k, v in tokens.items()
                              if k in TokenUsage.__dataclass_fields__})
        entry["tokens"] += usage
        total += usage

    return {
        "runs": len(runs),
        "repositories": {
            name: {**entry, "tokens": entry["tokens"].to_dict(),
                   "cost_usd": round(entry["cost_usd"], 6)}
            for name, entry in sorted(by_repo.items())
        },
        "total_cost_usd": round(total_usd, 6),
        "unpriced_runs": unpriced,
        "total_tokens": total.to_dict(),
    }
