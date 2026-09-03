"""Read token usage out of local Claude Code transcripts.

Claude Code appends newline-delimited JSON to ~/.claude/projects/<slug>/<id>.jsonl
as a session runs. Assistant entries carry a `message.usage` block, but the same
API response is written several times -- once per content block as the response
streams in -- each copy repeating the same cumulative usage. Summing them
naively triple- or quadruple-counts every response, so entries are deduplicated
on (message id, request id) before anything is added up.
"""

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from water_model import TokenCounts

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


@dataclass
class ApiCall:
    """One deduplicated response from the API."""

    timestamp: datetime
    model: str
    project: str
    session_id: str
    tokens: TokenCounts

    @property
    def date(self) -> str:
        return self.timestamp.date().isoformat()


@dataclass
class UsageTotals:
    """Aggregated usage, sliced the ways the report needs."""

    tokens: TokenCounts = field(default_factory=TokenCounts)
    calls: int = 0
    by_date: dict = field(default_factory=lambda: defaultdict(TokenCounts))
    by_model: dict = field(default_factory=lambda: defaultdict(TokenCounts))
    by_project: dict = field(default_factory=lambda: defaultdict(TokenCounts))
    calls_by_date: dict = field(default_factory=lambda: defaultdict(int))
    first: datetime = None
    last: datetime = None

    def add(self, call: ApiCall, counts_as_call: bool = True) -> None:
        """Fold a call into the totals.

        `counts_as_call` is False for usage that consumed tokens without being a
        response of its own -- a human chat message, whose text is billed as
        input to the reply that follows it.
        """
        self.tokens = self.tokens + call.tokens
        if counts_as_call:
            self.calls += 1
        self.by_date[call.date] = self.by_date[call.date] + call.tokens
        self.by_model[call.model] = self.by_model[call.model] + call.tokens
        self.by_project[call.project] = self.by_project[call.project] + call.tokens
        self.calls_by_date[call.date] += 1
        if self.first is None or call.timestamp < self.first:
            self.first = call.timestamp
        if self.last is None or call.timestamp > self.last:
            self.last = call.timestamp

    @property
    def active_days(self) -> int:
        return len(self.by_date)


def _parse_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating the trailing Z Claude Code writes."""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _project_name(entry: dict, transcript: Path) -> str:
    """Name the project a call belongs to, preferring the recorded cwd."""
    cwd = entry.get("cwd")
    if cwd:
        return os.path.basename(cwd.rstrip("/")) or cwd
    # Fall back to the directory slug, which is the cwd with separators flattened.
    return transcript.parent.name.strip("-").replace("-", "/")


def iter_api_calls(root: Path = DEFAULT_ROOT, include_sidechains: bool = True):
    """Yield every deduplicated API call found under `root`.

    Sidechain entries are the calls made by subagents. They are real usage, so
    they count by default, but can be excluded to see only the main thread.
    """
    if not root.exists():
        return
    seen = set()
    for transcript in sorted(root.rglob("*.jsonl")):
        try:
            handle = transcript.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a half-written line from a live session
                if entry.get("type") != "assistant":
                    continue
                if not include_sidechains and entry.get("isSidechain"):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue

                # The same response is logged once per content block; each copy
                # repeats the full usage, so count only the first sighting.
                key = (message.get("id"), entry.get("requestId"))
                if key == (None, None):
                    key = (entry.get("uuid"), None)  # nothing better to key on
                if key in seen:
                    continue
                seen.add(key)

                timestamp = entry.get("timestamp")
                if not timestamp:
                    continue
                try:
                    when = _parse_timestamp(timestamp)
                except ValueError:
                    continue

                yield ApiCall(
                    timestamp=when,
                    model=message.get("model") or "unknown",
                    project=_project_name(entry, transcript),
                    session_id=entry.get("sessionId") or transcript.stem,
                    tokens=TokenCounts(
                        input=usage.get("input_tokens") or 0,
                        output=usage.get("output_tokens") or 0,
                        cache_write=usage.get("cache_creation_input_tokens") or 0,
                        cache_read=usage.get("cache_read_input_tokens") or 0,
                    ),
                )


def collect(root: Path = DEFAULT_ROOT, include_sidechains: bool = True, since=None) -> UsageTotals:
    """Aggregate all usage under `root`, optionally only calls on/after `since`."""
    totals = UsageTotals()
    for call in iter_api_calls(root, include_sidechains):
        if since and call.timestamp.date() < since:
            continue
        totals.add(call)
    return totals
