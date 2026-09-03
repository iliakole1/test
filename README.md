# AI Water Meter

Estimates the water your AI usage costs the datacenters running it — Claude,
Weavy, Gemini — from your own data, on your own device.

- **Web app** (`site/`) — imports your Claude history, tracks credit- and
  request-based services, installs to an Android home screen as a PWA.
- **Chrome extension** (`extension/`) — the same app in a toolbar popup.
- **Command line** (`water_meter.py`) — a terminal meter and an HTML report.

Everything runs locally. Your usage is never uploaded, and the app has no
backend to upload it to.

## Getting your Claude usage in

There is no API that hands over your Claude history, so the app reads the files
Claude already keeps on your machine. Three ways in, all client-side:

| You have | Do this |
| --- | --- |
| Claude Code | Drop your `~/.claude/projects` folder on the page |
| Claude chat | Settings → Privacy → Export data, drop `conversations-000.zip` (no need to unzip) |
| A huge history, or a phone | Run `water_meter.py --export usage.json`, drop that one file |

Claude Code transcripts carry exact token counts. The chat export carries only
message text, so tokens there are estimated at ~4 characters per token and are
correspondingly rougher.

The export downloads as ZIPs, and both the app and the CLI read them directly —
the app unpacks them in the browser with `DecompressionStream`, no library and
no upload. Note the download links in the export manifest are **single-use**, so
spend them on the machine you are logged in from.

## Other services

**Weavy** is priced in credits, so the meter is too: log an operation and its
credit cost, and the water follows. Image generation (9), video generation (164)
and video upscaling (12) ship as presets; add your own for anything else.
**Gemini Pro** is counted per request. Both rates are editable under Assumptions.

## Command line

```
python3 water_meter.py                  # terminal meter
python3 water_meter.py --days 30        # last 30 days only
python3 water_meter.py --html out.html  # visual report
python3 water_meter.py --export u.json  # compact file for the web app
python3 water_meter.py --conversations conversations-000.zip
python3 water_meter.py --json           # machine-readable totals
```

No dependencies beyond the standard library.

## How the estimate works

Usage becomes energy, energy becomes water. Every constant lives in
`site/constants.json`, which the web app and the CLI both read, so the two can
never disagree.

**Claude** is counted from tokens. Generating a token is one forward pass that
uses the hardware poorly, at about `0.6 mWh`. Reading the prompt is roughly ten
times cheaper per token because the whole prompt goes through in parallel, and
cached tokens cheaper again; the four rates are scaled by their billing ratios.

**Weavy** is counted from credits at `0.32 Wh` each, anchored on Luccioni et al.
(2024), which measured ~2.9 Wh for one image from the largest generation models
tested — 9 credits per image at Weavy's rate. Credits then price every other
operation for free.

**Gemini Pro** defaults to `1 Wh` per request: Google puts a *median* Gemini text
prompt at 0.24 Wh, and Pro is the larger model and usually reasons first. The
roughest number here.

**Energy to water** is `1.08 mL/Wh`, implied by Google's 2025 disclosure that a
median Gemini text prompt uses 0.24 Wh and 0.26 mL. It covers on-site cooling
plus the water used generating the electricity.

## How wrong is it?

Order-of-magnitude. Real intensity swings more than tenfold with model size,
hardware, batch size, and above all datacenter location — an evaporatively
cooled site in a hot dry region drinks far more than a cool-climate one. These
numbers are useful for relative comparisons (which tool, which week, what a
cache hit saves you) and for a sense of scale. They are not a bill.

For scale: heavy daily agentic use lands in the tens of millilitres per day, so
a year of it is a few dozen litres — under a single bath, against the ~1,100 L
an average household gets through in a day. The water cost of AI is a real
question, but it is a question about datacenter siting and aggregate demand, not
about whether you personally send one more prompt.

## Counting

Claude Code writes each API response to its transcript several times as it
streams, and every copy repeats the same cumulative usage block. Both the Python
and JavaScript readers deduplicate on `(message id, request id)` before summing
— without that, every total comes out three or four times too high. Subagent
calls count by default; `--no-sidechains` excludes them.

## Building and testing

```
python3 -m unittest discover -p "test_*.py" -v   # 56 tests, incl. the JS model via node
python3 build_extension.py                        # assemble extension/ from site/
python3 build_artifact.py out.html                # inline the app into one file
```

`extension/` is generated from `site/` rather than kept as a second copy, so the
two cannot drift; only its `manifest.json` is checked in.

## Deploying

`.github/workflows/pages.yml` runs the tests and publishes `site/` to GitHub
Pages on every push to the default branch. The repository must be public (or on
a plan with private Pages), and Pages must be set to deploy from **GitHub
Actions** under Settings → Pages.
