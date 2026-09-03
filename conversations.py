"""Read a claude.ai data export.

The export arrives as ZIPs of JSON. Unlike Claude Code transcripts it carries no
token counts, only message text, so tokens are estimated from text length at
roughly four characters per token. Prompt caching is invisible here, which makes
a chat total rougher than a Claude Code one -- treat it as the weaker number.
"""

import json
import zipfile
from pathlib import Path

from usage_reader import UsageTotals
from water_model import TokenCounts

CHARS_PER_TOKEN = 4


def _message_text(message: dict) -> str:
    """Pull the text out of a message, across export format versions."""
    text = message.get("text")
    if isinstance(text, str) and text:
        return text
    blocks = message.get("content")
    if isinstance(blocks, list):
        return " ".join(
            b["text"] for b in blocks
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def iter_json_documents(path: Path):
    """Yield every parsed JSON document in a file, expanding ZIPs as needed."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.endswith("/") or not name.lower().endswith(".json"):
                    continue
                try:
                    yield json.loads(archive.read(name))
                except (ValueError, OSError):
                    continue  # one bad member should not lose the archive
        return
    try:
        yield json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return


def collect_conversations(paths, totals: UsageTotals = None) -> UsageTotals:
    """Aggregate chat usage from one or more export files."""
    totals = totals if totals is not None else UsageTotals()
    from datetime import datetime

    for path in paths:
        for document in iter_json_documents(Path(path)):
            conversations = document if isinstance(document, list) else document.get("conversations")
            if not isinstance(conversations, list):
                continue

            for convo in conversations:
                if not isinstance(convo, dict):
                    continue
                messages = convo.get("chat_messages") or convo.get("messages")
                if not isinstance(messages, list):
                    continue

                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    text = _message_text(message)
                    if not text:
                        continue
                    stamp = message.get("created_at") or convo.get("created_at")
                    if not isinstance(stamp, str) or len(stamp) < 10:
                        continue
                    try:
                        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    estimated = -(-len(text) // CHARS_PER_TOKEN)  # round up
                    is_assistant = (message.get("sender") or message.get("role")) == "assistant"
                    tokens = (
                        TokenCounts(output=estimated) if is_assistant
                        else TokenCounts(input=estimated)
                    )

                    from usage_reader import ApiCall

                    totals.add(
                        ApiCall(
                            timestamp=when,
                            model="claude-chat",
                            project="claude.ai",
                            session_id=str(convo.get("uuid") or ""),
                            tokens=tokens,
                        ),
                        counts_as_call=is_assistant,
                    )
    return totals
