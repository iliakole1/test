"""Tests for the web app's shared pieces.

The JS model is exercised through node so the browser code is covered by the
same suite as the CLI, and the two implementations are checked against each
other rather than trusted to stay in step by hand.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from water_model import TokenCounts, WaterModel, load_constants

HERE = Path(__file__).parent
SITE = HERE / "site"
NODE = shutil.which("node")


def run_js(body: str):
    """Evaluate a snippet against site/water-model.js and return its JSON result."""
    script = (
        "global.window = global;\n"
        f"require({str(SITE / 'water-model.js')!r});\n"
        "const W = global.WaterModel;\n"
        f"{body}\n"
    )
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


class SharedConstants(unittest.TestCase):
    def test_constants_file_is_valid_and_complete(self):
        data = load_constants()
        self.assertIn("ml_per_wh", data["water"])
        for key in ("output", "input", "cache_write", "cache_read"):
            self.assertIn(key, data["claude_tokens_wh"])
        self.assertTrue(data["comparisons"])
        self.assertTrue(data["services"])

    def test_python_model_reads_the_shared_file(self):
        data = load_constants()
        model = WaterModel.from_constants()
        self.assertEqual(model.ml_per_wh, data["water"]["ml_per_wh"])
        self.assertEqual(model.wh_per_output_token, data["claude_tokens_wh"]["output"])

    def test_comparisons_are_sorted_so_the_dial_picks_the_next_one_up(self):
        sizes = [c["ml"] for c in load_constants()["comparisons"]]
        self.assertEqual(sizes, sorted(sizes))

    def test_every_credit_service_prices_its_operations(self):
        for name, svc in load_constants()["services"].items():
            if svc.get("kind") == "tokens":
                continue
            self.assertIn("wh_per_unit", svc, name)
            self.assertTrue(svc.get("operations"), name)
            for op in svc["operations"]:
                self.assertGreaterEqual(op["units"], 0, name)

    def test_weavy_operations_match_the_documented_credit_costs(self):
        ops = {o["label"]: o["units"] for o in load_constants()["services"]["weavy"]["operations"]}
        self.assertEqual(ops["Image generation"], 9)
        self.assertEqual(ops["Video generation"], 164)
        self.assertEqual(ops["Video upscaling"], 12)


@unittest.skipIf(NODE is None, "node is not available")
class JsModel(unittest.TestCase):
    def test_js_and_python_agree_on_the_same_tokens(self):
        tokens = {"input": 2, "output": 538, "cache_write": 16418, "cache_read": 27630}
        rates = load_constants()["claude_tokens_wh"]
        js = run_js(
            f"console.log(JSON.stringify(W.tokenEnergyWh({json.dumps(tokens)}, "
            f"{json.dumps({k: rates[k] for k in ('output','input','cache_write','cache_read')})})));"
        )
        py = WaterModel.from_constants().energy_wh(TokenCounts(**tokens))
        self.assertAlmostEqual(js, py, places=9)

    def test_js_dedupes_repeated_records_of_one_response(self):
        line = json.dumps({
            "type": "assistant", "timestamp": "2026-09-03T10:00:00Z", "requestId": "r1",
            "message": {"id": "m1", "usage": {"output_tokens": 500, "cache_read_input_tokens": 20000}},
        })
        result = run_js(
            "const seen = new Set(); const out = W.emptyImport();\n"
            f"W.parseTranscript({json.dumps(chr(10).join([line] * 4))}, seen, out);\n"
            "console.log(JSON.stringify({calls: out.calls, output: out.tokens.output}));"
        )
        self.assertEqual(result, {"calls": 1, "output": 500})

    def test_js_dedup_spans_files_via_the_shared_seen_set(self):
        line = json.dumps({
            "type": "assistant", "timestamp": "2026-09-03T10:00:00Z", "requestId": "r1",
            "message": {"id": "m1", "usage": {"output_tokens": 500}},
        })
        result = run_js(
            "const seen = new Set(); const out = W.emptyImport();\n"
            f"W.parseTranscript({json.dumps(line)}, seen, out);\n"
            f"W.parseTranscript({json.dumps(line)}, seen, out);\n"
            "console.log(JSON.stringify(out.calls));"
        )
        self.assertEqual(result, 1)

    def test_js_groups_transcript_usage_by_date(self):
        lines = "\n".join(json.dumps({
            "type": "assistant", "timestamp": f"2026-09-0{d}T10:00:00Z", "requestId": f"r{d}",
            "message": {"id": f"m{d}", "usage": {"output_tokens": 100 * d}},
        }) for d in (1, 2))
        result = run_js(
            "const out = W.emptyImport();\n"
            f"W.parseTranscript({json.dumps(lines)}, new Set(), out);\n"
            "console.log(JSON.stringify(Object.keys(out.days).sort()));"
        )
        self.assertEqual(result, ["2026-09-01", "2026-09-02"])

    def test_js_skips_malformed_and_non_assistant_lines(self):
        junk = "\n".join([
            '{"type":"user"}', "not json", '{"type":"assistant"}',
            '{"type":"assistant","message":{"id":"x"}}', "",
        ])
        result = run_js(
            "const out = W.emptyImport();\n"
            f"W.parseTranscript({json.dumps(junk)}, new Set(), out);\n"
            "console.log(JSON.stringify(out.calls));"
        )
        self.assertEqual(result, 0)

    def test_js_reads_both_conversation_export_shapes(self):
        convos = [{
            "created_at": "2026-09-01T10:00:00Z",
            "chat_messages": [
                {"sender": "human", "text": "x" * 400, "created_at": "2026-09-01T10:00:00Z"},
                {"sender": "assistant", "created_at": "2026-09-01T10:00:01Z",
                 "content": [{"type": "text", "text": "y" * 800}]},
            ],
        }]
        result = run_js(
            "const out = W.emptyImport();\n"
            f"W.parseConversations({json.dumps(convos)}, out);\n"
            "console.log(JSON.stringify({calls: out.calls, i: out.tokens.input, o: out.tokens.output}));"
        )
        self.assertEqual(result, {"calls": 1, "i": 100, "o": 200})

    def test_js_volume_formatting_matches_python(self):
        from water_model import format_volume
        for value in (0.5, 32.5, 250, 2500, 2_500_000):
            js = run_js(f"console.log(JSON.stringify(W.formatVolume({value})));")
            self.assertEqual(js, format_volume(value), value)

    def test_js_comparison_helpers_match_python(self):
        from water_model import nearest_comparison
        comparisons = json.dumps(load_constants()["comparisons"])
        for value in (1, 130_000, 500):
            js = run_js(f"console.log(JSON.stringify(W.nearestComparison({value}, {comparisons}).label));")
            self.assertEqual(js, nearest_comparison(value)[0], value)


class Packaging(unittest.TestCase):
    def test_site_has_everything_the_page_and_manifest_reference(self):
        for name in ("index.html", "style.css", "app.js", "water-model.js",
                     "constants.json", "manifest.webmanifest", "sw.js",
                     "icon.svg", "icon-192.png", "icon-512.png"):
            self.assertTrue((SITE / name).exists(), name)

    def test_service_worker_precaches_only_files_that_exist(self):
        shell = re.search(r"var SHELL = \[(.*?)\];", (SITE / "sw.js").read_text(), re.S).group(1)
        for name in re.findall(r'"\./([^"]*)"', shell):
            if name:
                self.assertTrue((SITE / name).exists(), name)

    def test_manifest_icons_exist(self):
        manifest = json.loads((SITE / "manifest.webmanifest").read_text())
        for icon in manifest["icons"]:
            self.assertTrue((SITE / icon["src"]).exists(), icon["src"])

    def test_every_css_variable_used_is_declared_on_bare_root(self):
        css = (SITE / "style.css").read_text()
        bare = css.split(":root {")[1].split("}")[0]
        declared = set(re.findall(r"(--[a-z-]+):", bare))
        used = set(re.findall(r"var\((--[a-z-]+)\)", css))
        self.assertEqual(used - declared, set())

    def test_js_referenced_element_ids_exist_in_the_page(self):
        page = (SITE / "index.html").read_text()
        present = set(re.findall(r'id="([^"]+)"', page))
        wanted = set(re.findall(r'el\("([^"]+)"\)', (SITE / "app.js").read_text()))
        self.assertEqual(wanted - present, set())

    def test_extension_build_is_reproducible_and_drops_pwa_only_tags(self):
        import build_extension
        build_extension.build()
        page = (HERE / "extension" / "index.html").read_text()
        self.assertNotIn("manifest.webmanifest", page)
        self.assertIn("water-model.js", page)
        for name in build_extension.COPY:
            self.assertTrue((HERE / "extension" / name).exists(), name)

    def test_extension_manifest_is_valid_mv3(self):
        manifest = json.loads((HERE / "extension" / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["action"]["default_popup"], "index.html")
        self.assertIn("storage", manifest["permissions"])

    def test_pages_workflow_publishes_the_site_directory(self):
        workflow = (HERE / ".github" / "workflows" / "pages.yml").read_text()
        self.assertIn("path: site", workflow)
        self.assertIn("actions/deploy-pages", workflow)
        self.assertIn("needs: test", workflow)


class Export(unittest.TestCase):
    def test_export_payload_carries_totals_and_no_prompt_content(self):
        from usage_reader import collect
        from water_meter import export_payload
        payload = export_payload(collect())
        self.assertEqual(payload["format"], "ai-water-meter-export")
        self.assertEqual(payload["source"], "claude-code")
        self.assertIn("days", payload)
        self.assertEqual(
            set(payload["tokens"]), {"input", "output", "cache_write", "cache_read"}
        )
        # Nothing in the payload should be able to carry prompt or file text.
        blob = json.dumps(payload)
        self.assertNotIn("content", blob)
        self.assertNotIn("cwd", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SingleFileBuild(unittest.TestCase):
    """The inlined build must carry the app exactly once and reach nothing external."""

    def setUp(self):
        import build_artifact
        self.standalone = build_artifact.build(fragment=False)
        self.fragment = build_artifact.build(fragment=True)

    def test_standalone_is_a_whole_document(self):
        self.assertTrue(self.standalone.lstrip().startswith("<!doctype html>"))
        self.assertIn("</html>", self.standalone)

    def test_fragment_omits_the_document_skeleton_but_keeps_the_title(self):
        self.assertNotIn("<!doctype", self.fragment)
        self.assertNotIn("<body>", self.fragment)
        self.assertIn("<title>", self.fragment)

    def test_assets_are_inlined_exactly_once(self):
        for build in (self.standalone, self.fragment):
            self.assertEqual(build.count("window.WATER_CONSTANTS = "), 1)
            self.assertEqual(build.count("global.WaterModel = {"), 1)
            # Declared three times by design (bare root, dark media query, dark
            # stamp), so key the CSS check on a genuinely unique marker.
            self.assertEqual(build.count("an instrument panel, not a dashboard"), 1)

    def test_no_relative_asset_references_survive(self):
        for build in (self.standalone, self.fragment):
            for ref in ('href="style.css"', 'src="app.js"', 'src="water-model.js"',
                        'href="manifest.webmanifest"', 'href="icon.svg"'):
                self.assertNotIn(ref, build, ref)

    def test_the_only_external_host_is_google_fonts(self):
        hosts = set(re.findall(r'https?://([^/"\s]+)', self.fragment))
        self.assertEqual(hosts - {"fonts.googleapis.com", "fonts.gstatic.com",
                                  "github.com", "www.w3.org"}, set())

    def test_inlined_constants_parse_and_match_the_source_file(self):
        blob = re.search(r"window\.WATER_CONSTANTS = (\{.*?\});", self.fragment, re.S).group(1)
        self.assertEqual(json.loads(blob), load_constants())
