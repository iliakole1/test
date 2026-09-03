"""Tests for reading a claude.ai data export.

The export ships as ZIPs of JSON whose shape has changed across versions, so the
reader has to tolerate both message layouts and survive junk members.
"""

import json
import subprocess
import sys
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent))

from conversations import CHARS_PER_TOKEN, collect_conversations, iter_json_documents

HERE = Path(__file__).parent


def convo(messages, uuid="c1", created="2026-09-01T10:00:00Z"):
    return {"uuid": uuid, "created_at": created, "chat_messages": messages}


def msg(sender, text, created="2026-09-01T10:00:00Z", block_style=False):
    base = {"sender": sender, "created_at": created}
    if block_style:
        base["content"] = [{"type": "text", "text": text}]
    else:
        base["text"] = text
    return base


class Reading(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_json(self, data, name="conversations.json"):
        path = self.dir / name
        path.write_text(json.dumps(data))
        return path

    def write_zip(self, data, name="conversations-000.zip", inner="conversations.json"):
        path = self.dir / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(inner, json.dumps(data))
        return path

    def test_reads_a_plain_json_export(self):
        path = self.write_json([convo([msg("assistant", "y" * 800)])])
        totals = collect_conversations([path])
        self.assertEqual(totals.calls, 1)
        self.assertEqual(totals.tokens.output, 800 // CHARS_PER_TOKEN)

    def test_reads_the_same_data_out_of_a_zip(self):
        data = [convo([msg("assistant", "y" * 800)])]
        from_json = collect_conversations([self.write_json(data)])
        from_zip = collect_conversations([self.write_zip(data)])
        self.assertEqual(from_zip.tokens.output, from_json.tokens.output)
        self.assertEqual(from_zip.calls, from_json.calls)

    def test_human_text_counts_as_input_and_is_not_an_api_call(self):
        path = self.write_json([convo([
            msg("human", "x" * 400),
            msg("assistant", "y" * 800),
        ])])
        totals = collect_conversations([path])
        self.assertEqual(totals.tokens.input, 100)
        self.assertEqual(totals.tokens.output, 200)
        self.assertEqual(totals.calls, 1)  # only the assistant reply is a call

    def test_both_message_layouts_are_understood(self):
        plain = collect_conversations([self.write_json([convo([msg("assistant", "y" * 400)])])])
        blocks = collect_conversations([
            self.write_json([convo([msg("assistant", "y" * 400, block_style=True)])], name="b.json")
        ])
        self.assertEqual(plain.tokens.output, blocks.tokens.output)

    def test_conversations_may_be_wrapped_in_an_object(self):
        data = {"conversations": [convo([msg("assistant", "y" * 400)])]}
        self.assertEqual(collect_conversations([self.write_json(data)]).calls, 1)

    def test_usage_is_grouped_by_message_date(self):
        path = self.write_json([convo([
            msg("assistant", "y" * 400, created="2026-09-01T10:00:00Z"),
            msg("assistant", "y" * 800, created="2026-09-02T10:00:00Z"),
        ])])
        totals = collect_conversations([path])
        self.assertEqual(totals.active_days, 2)
        self.assertEqual(totals.by_date["2026-09-02"].output, 200)

    def test_malformed_members_do_not_lose_the_archive(self):
        path = self.dir / "mixed.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("broken.json", "{not json")
            z.writestr("notes.txt", "ignored")
            z.writestr("folder/", "")
            z.writestr("conversations.json", json.dumps([convo([msg("assistant", "y" * 400)])]))
        self.assertEqual(collect_conversations([path]).calls, 1)

    def test_messages_without_text_or_a_usable_date_are_skipped(self):
        path = self.write_json([convo([
            {"sender": "assistant", "created_at": "2026-09-01T10:00:00Z"},   # no text
            {"sender": "assistant", "text": "hi", "created_at": "nonsense"},  # bad date
            msg("assistant", "y" * 400),
        ])])
        self.assertEqual(collect_conversations([path]).calls, 1)

    def test_a_message_without_a_timestamp_inherits_the_conversation_date(self):
        # Better to date it approximately than to drop the usage entirely.
        path = self.write_json([convo([{"sender": "assistant", "text": "y" * 400}],
                                      created="2026-09-05T10:00:00Z")])
        totals = collect_conversations([path])
        self.assertEqual(totals.calls, 1)
        self.assertIn("2026-09-05", totals.by_date)

    def test_several_export_parts_accumulate(self):
        a = self.write_json([convo([msg("assistant", "y" * 400)], uuid="a")], name="a.json")
        b = self.write_json([convo([msg("assistant", "y" * 800)], uuid="b")], name="b.json")
        totals = collect_conversations([a, b])
        self.assertEqual(totals.calls, 2)
        self.assertEqual(totals.tokens.output, 300)

    def test_chat_usage_merges_into_existing_totals(self):
        from usage_reader import UsageTotals
        totals = UsageTotals()
        collect_conversations([self.write_json([convo([msg("assistant", "y" * 400)])])], totals)
        collect_conversations([self.write_json([convo([msg("assistant", "y" * 400)], uuid="d")],
                                               name="d.json")], totals)
        self.assertEqual(totals.calls, 2)

    def test_a_missing_or_unreadable_file_is_not_fatal(self):
        self.assertEqual(collect_conversations([self.dir / "nope.json"]).calls, 0)
        junk = self.dir / "junk.json"
        junk.write_text("<html>not json</html>")
        self.assertEqual(collect_conversations([junk]).calls, 0)

    def test_iter_json_documents_skips_non_json_members(self):
        path = self.dir / "z.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("a.json", "{}")
            z.writestr("b.png", "binary")
        self.assertEqual(len(list(iter_json_documents(path))), 1)

    def test_cli_accepts_a_conversations_export(self):
        path = self.write_zip([convo([msg("assistant", "y" * 4000)])])
        proc = subprocess.run(
            [sys.executable, str(HERE / "water_meter.py"), "--json",
             "--root", str(self.dir / "empty"), "--conversations", str(path)],
            capture_output=True, text=True, cwd=HERE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["total"]["tokens"]["output"], 1000)
        self.assertIn("claude-chat", data["by_model"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
