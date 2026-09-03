"""Tests for the water meter.

The parsing tests matter most: Claude Code logs each response several times, so
the difference between a correct total and one that is four times too high is
entirely in the deduplication.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent))

from html_report import build_html
from usage_reader import collect
from water_model import TokenCounts, WaterModel, format_volume, nearest_comparison
from water_meter import fill_reference, render_tank, sparkline

HERE = Path(__file__).parent


def entry(msg_id, req_id, *, output=100, cache_read=0, cache_write=0, inp=0,
          ts="2026-09-03T11:00:00.000Z", model="claude-opus-5", cwd="/home/u/proj",
          sidechain=False):
    return json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "requestId": req_id,
        "sessionId": "s1",
        "cwd": cwd,
        "isSidechain": sidechain,
        "uuid": f"{msg_id}-{req_id}",
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": inp,
                "output_tokens": output,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        },
    })


class TranscriptParsing(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "projects" / "-home-u-proj"
        self.root.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, *lines, name="a.jsonl"):
        (self.root / name).write_text("\n".join(lines) + "\n")
        return self.root.parent

    def test_repeated_records_of_one_response_count_once(self):
        # Four log lines, one API response: the usage block repeats verbatim.
        root = self.write(*[entry("msg_1", "req_1", output=500)] * 4)
        totals = collect(root)
        self.assertEqual(totals.calls, 1)
        self.assertEqual(totals.tokens.output, 500)

    def test_distinct_responses_all_count(self):
        root = self.write(
            entry("msg_1", "req_1", output=500),
            entry("msg_2", "req_2", output=300),
        )
        totals = collect(root)
        self.assertEqual(totals.calls, 2)
        self.assertEqual(totals.tokens.output, 800)

    def test_dedup_spans_separate_transcript_files(self):
        # A resumed session can replay the same response into a new file.
        self.write(entry("msg_1", "req_1", output=500), name="a.jsonl")
        root = self.write(entry("msg_1", "req_1", output=500), name="b.jsonl")
        self.assertEqual(collect(root).calls, 1)

    def test_all_four_token_kinds_are_read(self):
        root = self.write(entry("m", "r", output=7, inp=3, cache_read=11, cache_write=5))
        t = collect(root).tokens
        self.assertEqual((t.output, t.input, t.cache_read, t.cache_write), (7, 3, 11, 5))
        self.assertEqual(t.total, 26)

    def test_non_assistant_and_malformed_lines_are_skipped(self):
        root = self.write(
            '{"type":"user","message":{"role":"user"}}',
            "not json at all",
            '{"type":"assistant"}',                       # no message
            '{"type":"assistant","message":{"id":"x"}}',  # no usage
            "",
            entry("msg_1", "req_1", output=42),
        )
        totals = collect(root)
        self.assertEqual(totals.calls, 1)
        self.assertEqual(totals.tokens.output, 42)

    def test_sidechains_included_by_default_and_excludable(self):
        root = self.write(
            entry("msg_1", "req_1", output=100),
            entry("msg_2", "req_2", output=900, sidechain=True),
        )
        self.assertEqual(collect(root).tokens.output, 1000)
        self.assertEqual(collect(root, include_sidechains=False).tokens.output, 100)

    def test_grouping_by_date_model_and_project(self):
        root = self.write(
            entry("m1", "r1", output=10, ts="2026-09-01T10:00:00.000Z", model="opus", cwd="/a/alpha"),
            entry("m2", "r2", output=20, ts="2026-09-02T10:00:00.000Z", model="sonnet", cwd="/a/beta"),
            entry("m3", "r3", output=30, ts="2026-09-02T18:00:00.000Z", model="opus", cwd="/a/alpha"),
        )
        totals = collect(root)
        self.assertEqual(totals.active_days, 2)
        self.assertEqual(totals.by_date["2026-09-02"].output, 50)
        self.assertEqual(totals.by_model["opus"].output, 40)
        self.assertEqual(totals.by_project["alpha"].output, 40)
        self.assertEqual(totals.first.date().isoformat(), "2026-09-01")
        self.assertEqual(totals.last.date().isoformat(), "2026-09-02")

    def test_since_filter_excludes_older_calls(self):
        from datetime import date
        root = self.write(
            entry("m1", "r1", output=10, ts="2026-01-01T10:00:00.000Z"),
            entry("m2", "r2", output=20, ts="2026-09-03T10:00:00.000Z"),
        )
        totals = collect(root, since=date(2026, 6, 1))
        self.assertEqual(totals.tokens.output, 20)

    def test_missing_root_is_not_an_error(self):
        totals = collect(Path(self._tmp.name) / "nope")
        self.assertEqual(totals.calls, 0)


class Model(unittest.TestCase):
    def test_generating_costs_more_than_reading_cache(self):
        m = WaterModel()
        self.assertGreater(
            m.water_ml(TokenCounts(output=1000)),
            m.water_ml(TokenCounts(cache_read=1000)),
        )

    def test_water_scales_linearly_with_tokens(self):
        m = WaterModel()
        one = m.water_ml(TokenCounts(output=1000))
        self.assertAlmostEqual(m.water_ml(TokenCounts(output=2000)), one * 2, places=9)

    def test_ml_per_wh_is_overridable(self):
        t = TokenCounts(output=1000)
        self.assertAlmostEqual(
            WaterModel(ml_per_wh=2.16).water_ml(t),
            WaterModel(ml_per_wh=1.08).water_ml(t) * 2,
            places=9,
        )

    def test_a_typical_agent_turn_lands_in_single_digit_millilitres(self):
        # Guards against an order-of-magnitude slip in the constants.
        turn = TokenCounts(input=2, output=538, cache_write=16418, cache_read=27630)
        self.assertTrue(0.1 < WaterModel().water_ml(turn) < 10)

    def test_empty_usage_draws_no_water(self):
        self.assertEqual(WaterModel().water_ml(TokenCounts()), 0)

    def test_token_counts_add(self):
        total = TokenCounts(input=1, output=2) + TokenCounts(cache_read=3, output=4)
        self.assertEqual((total.input, total.output, total.cache_read), (1, 6, 3))


class Formatting(unittest.TestCase):
    def test_volume_units_step_up_with_magnitude(self):
        self.assertIn("mL", format_volume(0.5))
        self.assertIn("mL", format_volume(250))
        self.assertIn("L", format_volume(2_500))
        self.assertIn("m³", format_volume(2_500_000))

    def test_comparison_picks_a_reference_the_volume_exceeds(self):
        label, count = nearest_comparison(130_000)
        self.assertEqual(label, "a 10-minute shower")
        self.assertAlmostEqual(count, 2.0)

    def test_tiny_volumes_fall_back_to_the_smallest_reference(self):
        label, count = nearest_comparison(1)
        self.assertEqual(label, "a teaspoon")
        self.assertLess(count, 1)

    def test_zero_is_handled(self):
        self.assertEqual(nearest_comparison(0)[1], 0.0)

    def test_fill_reference_is_larger_than_the_volume(self):
        _, capacity = fill_reference(300)
        self.assertGreater(capacity, 300)

    def test_tank_is_emptier_for_less_water(self):
        low = "\n".join(render_tank(10)).count("#")
        high = "\n".join(render_tank(40)).count("#")
        self.assertLess(low, high)

    def test_sparkline_matches_input_length(self):
        self.assertEqual(len(sparkline([1, 5, 3, 9])), 4)
        self.assertEqual(sparkline([]), "")
        self.assertEqual(len(sparkline([0, 0])), 2)


class Report(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name) / "projects" / "-p"
        root.mkdir(parents=True)
        (root / "a.jsonl").write_text("\n".join([
            entry("m1", "r1", output=500, cache_read=20000, ts="2026-09-01T10:00:00.000Z"),
            entry("m2", "r2", output=800, cache_read=30000, ts="2026-09-02T10:00:00.000Z"),
        ]) + "\n")
        self.totals = collect(root.parent)

    def tearDown(self):
        self._tmp.cleanup()

    def test_html_is_a_full_document_by_default(self):
        page = build_html(self.totals, WaterModel())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("</html>", page)

    def test_fragment_omits_the_document_skeleton(self):
        page = build_html(self.totals, WaterModel(), fragment=True)
        self.assertNotIn("<!doctype", page)
        self.assertNotIn("<body>", page)
        self.assertIn("<title>", page)

    def test_every_colour_token_is_defined_in_the_bare_root_block(self):
        # A token defined only under a media query renders unreadably in the
        # default, un-stamped theme.
        page = build_html(self.totals, WaterModel(), fragment=True)
        bare = page.split(":root {")[1].split("}")[0]
        used = set(re.findall(r"var\((--[a-z-]+)\)", page))
        declared = set(re.findall(r"(--[a-z-]+):", bare))
        self.assertEqual(used - declared, set())

    def test_empty_history_renders_without_crashing(self):
        from usage_reader import UsageTotals
        page = build_html(UsageTotals(), WaterModel())
        self.assertIn("No Claude Code transcripts", page)

    def test_cli_json_output_is_valid_and_complete(self):
        proc = subprocess.run(
            [sys.executable, str(HERE / "water_meter.py"), "--json", "--root",
             str(Path(self._tmp.name) / "projects")],
            capture_output=True, text=True, cwd=HERE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["api_calls"], 2)
        self.assertEqual(data["total"]["tokens"]["output"], 1300)
        self.assertGreater(data["total"]["water_ml"], 0)
        self.assertIn("2026-09-01", data["by_date"])

    def test_cli_writes_an_html_file(self):
        out = Path(self._tmp.name) / "r.html"
        proc = subprocess.run(
            [sys.executable, str(HERE / "water_meter.py"), "--html", str(out),
             "--root", str(Path(self._tmp.name) / "projects")],
            capture_output=True, text=True, cwd=HERE,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Claude Water Meter", out.read_text())


import re  # noqa: E402  (used by the CSS-token test above)

if __name__ == "__main__":
    unittest.main(verbosity=2)
