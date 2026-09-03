# Claude Water Meter

Reads your local Claude Code transcripts and estimates how much water the
datacenters consumed running them.

```
python3 water_meter.py                  # meter for all recorded usage
python3 water_meter.py --days 30        # only the last 30 days
python3 water_meter.py --html out.html  # a visual report
python3 water_meter.py --json           # machine-readable totals
```

No dependencies beyond the standard library. Run it on the machine where you
actually use Claude Code — it reads `~/.claude/projects/**/*.jsonl`, which is
where Claude Code logs its sessions, and never sends anything anywhere.

```
  CLAUDE WATER METER
  ==============================================

   ______________________
  |                      |
  |                      |
  |----------------------|
  |######################|
  |######################|
  |______________________|

    32.5 mL  =  73.9% of a shot glass
```

## How the estimate works

Nobody publishes a per-token water figure for Claude, so this is a model, not a
meter reading. It runs in two steps.

**Tokens to energy.** Generating a token means one forward pass through the
model to produce one token, which uses the hardware poorly and costs roughly
`0.6 mWh`. Reading the prompt is far cheaper per token, because the whole prompt
goes through in parallel — about a tenth as much for fresh input, and less again
for tokens served from the prompt cache. The four rates are scaled against each
other by their billing ratios, which track how much work each actually skips.

**Energy to water.** At `1.08 mL/Wh`, the rate implied by Google's 2025
disclosure that a median Gemini text prompt uses 0.24 Wh and 0.26 mL. That
covers both on-site cooling and the water used generating the electricity.

Every constant lives in `water_model.py` and is overridable; `--ml-per-wh` sets
the water intensity from the command line.

## How wrong is it?

Order-of-magnitude. The real figure swings by more than tenfold with model size,
hardware, batch size, and above all datacenter location — an evaporatively
cooled site in a hot dry region drinks far more than a cool-climate one. The
numbers here are useful for relative comparisons (which project, which week,
what a cache hit saves you) and for a sense of scale. They are not a bill.

For scale: heavy daily agentic use lands in the tens of millilitres per day, so
a year of it is a few dozen litres — under a single bath, and a rounding error
against the ~1,100 L a household gets through in a day. The water cost of AI is
a real question, but it is a question about datacenter siting and aggregate
demand, not about whether you personally send one more prompt.

## Counting

Claude Code writes each API response to the transcript several times as it
streams, and every copy repeats the same cumulative usage block. Entries are
deduplicated on `(message id, request id)` before anything is summed — without
that, every total comes out three or four times too high.

Subagent calls are counted by default, since they are real usage;
`--no-sidechains` excludes them.

## Tests

```
python3 -m unittest test_water_meter -v
```
