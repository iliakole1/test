"""Render the water meter as an HTML instrument panel.

The page is laid out like the face of a real water meter: a cumulative digit
register, a sweep dial, then the consumption detail beneath it. `fragment=True`
emits just the title, styles and body for publishing as an Artifact, which
supplies its own document skeleton; the default writes a standalone file.
"""

import html
import math
from pathlib import Path

from water_model import COMPARISONS, WaterModel, format_volume

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=IBM+Plex+Mono:wght@400;500;600&"
    "family=IBM+Plex+Sans+Condensed:wght@500;600;700&"
    'family=IBM+Plex+Sans:wght@400;500;600&display=swap">'
)

CSS = """
:root {
  --paper: #EBEFF2;
  --surface: #F7F9FA;
  --ink: #15212A;
  --ink-soft: #4A5D68;
  --ink-faint: #8397A1;
  --rule: #CFD8DD;
  --water: #1F7A8C;
  --water-soft: #B9D6DC;
  --brass: #A0742C;
  --signal: #B3402F;
  --register-face: #1B2830;
  --register-digit: #F2F6F7;
  --shadow: 0 1px 2px rgba(21, 33, 42, .07), 0 8px 24px -16px rgba(21, 33, 42, .3);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0E1519;
    --surface: #151F25;
    --ink: #DCE6EA;
    --ink-soft: #9DB0B9;
    --ink-faint: #6C808A;
    --rule: #26343C;
    --water: #4FB3C6;
    --water-soft: #24454E;
    --brass: #C99B4E;
    --signal: #E0705C;
    --register-face: #060B0E;
    --register-digit: #EAF2F4;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .8);
  }
}
:root[data-theme="dark"] {
  --paper: #0E1519;
  --surface: #151F25;
  --ink: #DCE6EA;
  --ink-soft: #9DB0B9;
  --ink-faint: #6C808A;
  --rule: #26343C;
  --water: #4FB3C6;
  --water-soft: #24454E;
  --brass: #C99B4E;
  --signal: #E0705C;
  --register-face: #060B0E;
  --register-digit: #EAF2F4;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .8);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.sheet { max-width: 940px; margin: 0 auto; padding: 40px 24px 64px; }

.label {
  font-family: "IBM Plex Sans Condensed", ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 11px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--ink-faint);
}
.num { font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; }

/* Masthead reads like the header of a utility statement. */
.masthead {
  display: flex; flex-wrap: wrap; gap: 16px 24px;
  align-items: baseline; justify-content: space-between;
  border-bottom: 2px solid var(--ink); padding-bottom: 14px; margin-bottom: 32px;
}
.masthead h1 {
  font-family: "IBM Plex Sans Condensed", ui-sans-serif, system-ui, sans-serif;
  font-size: clamp(28px, 5vw, 40px); font-weight: 700; letter-spacing: -.01em;
  margin: 0; line-height: 1.05; text-wrap: balance;
}
.masthead .period { text-align: right; }
.masthead .period .num { font-size: 13px; color: var(--ink-soft); display: block; }

/* The meter face: register + dial, the two things a real meter shows. */
.meter {
  display: grid; grid-template-columns: 1fr auto; gap: 32px; align-items: center;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 3px;
  padding: 28px 32px; box-shadow: var(--shadow); margin-bottom: 12px;
}
@media (max-width: 640px) { .meter { grid-template-columns: 1fr; justify-items: start; } }

.register { display: flex; align-items: stretch; gap: 3px; }
.register .digit {
  background: var(--register-face); color: var(--register-digit);
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums;
  font-size: clamp(30px, 6vw, 46px); font-weight: 500; line-height: 1;
  padding: 12px 9px; border-radius: 2px; min-width: 34px; text-align: center;
  box-shadow: inset 0 -6px 12px -6px rgba(0,0,0,.7), inset 0 6px 10px -6px rgba(255,255,255,.14);
}
.register .digit.frac { background: var(--signal); color: #FFF6F4; }
.register .point {
  align-self: flex-end; padding-bottom: 10px; margin: 0 2px;
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-weight: 600;
  font-size: 22px; color: var(--ink-faint); line-height: 1;
}
.register .unit {
  align-self: flex-end; padding-bottom: 12px; margin-left: 8px;
  font-family: "IBM Plex Sans Condensed", ui-sans-serif, sans-serif;
  font-weight: 600; font-size: 15px; color: var(--brass); letter-spacing: .06em;
}
.reading-note { margin: 12px 0 0; color: var(--ink-soft); font-size: 14px; max-width: 46ch; }
.reading-note strong { color: var(--ink); font-weight: 600; }

.dial { flex-shrink: 0; }

/* Log rail: places the total against everyday volumes. */
.rail { margin: 28px 0 8px; }
.rail-wrap { overflow-x: auto; }

/* Statement rows: label / value pairs, no card chrome needed. */
.rows { display: grid; gap: 0; margin: 0; }
.row {
  display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: baseline;
  padding: 9px 0; border-bottom: 1px solid var(--rule);
}
.row:last-child { border-bottom: 0; }
.row dt { color: var(--ink-soft); font-size: 14px; }
.row dd { margin: 0; font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums; font-size: 14px; font-weight: 500; }
.row.total { border-top: 2px solid var(--ink); border-bottom: 0; padding-top: 12px; margin-top: 4px; }
.row.total dt { color: var(--ink); font-weight: 600; }
.row.total dd { font-size: 17px; font-weight: 600; color: var(--water); }
.row .sub { color: var(--ink-faint); }

section { margin-top: 40px; }
.sheet h2 {
  font-family: "IBM Plex Sans Condensed", ui-sans-serif, system-ui, sans-serif;
  font-size: 13px; font-weight: 700; letter-spacing: .13em; text-transform: uppercase;
  color: var(--ink); margin: 0 0 4px; padding-bottom: 8px; border-bottom: 1px solid var(--ink);
}
.sheet .caption { color: var(--ink-faint); font-size: 13px; margin: 8px 0 18px; max-width: 62ch; }

.split { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 32px; }
.chart-wrap { overflow-x: auto; }

/* Composition bar: which kind of token draws the water. */
.comp { display: flex; height: 30px; border-radius: 2px; overflow: hidden; margin-bottom: 14px; }
.comp span { display: block; }
.comp-key { display: grid; gap: 7px; }
.comp-key div { display: grid; grid-template-columns: 11px 1fr auto; gap: 10px; align-items: center; font-size: 13px; }
.comp-key i { width: 11px; height: 11px; border-radius: 2px; display: block; }
.comp-key .v { font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums; color: var(--ink-soft); }

.note {
  margin-top: 44px; padding-top: 18px; border-top: 1px solid var(--rule);
  color: var(--ink-soft); font-size: 13.5px; line-height: 1.65;
}
.note h2 {
  font-family: "IBM Plex Sans Condensed", ui-sans-serif, system-ui, sans-serif;
  font-size: 12px; letter-spacing: .13em; text-transform: uppercase;
  color: var(--ink-faint); margin: 0 0 10px; font-weight: 600;
  border-bottom: 0; padding-bottom: 0;
}
.note p { margin: 0 0 10px; max-width: 68ch; }
.note code { font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .93em; color: var(--ink); }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
"""


def _register(ml: float) -> str:
    """Digit wheels, whole units dark and the fraction red, as on a real meter."""
    if ml < 10_000:
        value, unit, whole_digits = ml, "mL", 5
    elif ml < 10_000_000:
        value, unit, whole_digits = ml / 1_000, "LITRES", 5
    else:
        value, unit, whole_digits = ml / 1_000_000, "m³", 5

    whole = int(value)
    frac = int(round((value - whole) * 100))
    if frac == 100:  # rounding carried into the whole part
        whole, frac = whole + 1, 0
    text = str(whole).rjust(whole_digits, "0")[-whole_digits:]

    cells = "".join(f'<span class="digit">{d}</span>' for d in text)
    cells += '<span class="point">.</span>'
    cells += "".join(f'<span class="digit frac">{d}</span>' for d in f"{frac:02d}")
    return f'<div class="register" role="img" aria-label="Meter reading {value:.2f} {unit}">{cells}<span class="unit">{unit}</span></div>'


def _dial(ml: float, capacity: float, label: str) -> str:
    """A sweep dial showing how far the total has filled the reference volume."""
    fraction = min(ml / capacity, 1.0) if capacity else 0.0
    size, r, cx, cy = 132, 52, 66, 66
    circumference = 2 * math.pi * r
    # Needle sweeps clockwise from 12 o'clock.
    angle = fraction * 2 * math.pi - math.pi / 2
    nx, ny = cx + math.cos(angle) * (r - 11), cy + math.sin(angle) * (r - 11)

    ticks = []
    for i in range(12):
        a = i * math.pi / 6 - math.pi / 2
        inner = r - (9 if i % 3 == 0 else 5)
        ticks.append(
            f'<line x1="{cx + math.cos(a) * inner:.1f}" y1="{cy + math.sin(a) * inner:.1f}" '
            f'x2="{cx + math.cos(a) * r:.1f}" y2="{cy + math.sin(a) * r:.1f}" '
            f'stroke="var(--rule)" stroke-width="{2 if i % 3 == 0 else 1}" />'
        )
    return f"""<div class="dial">
<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img"
     aria-label="{fraction * 100:.0f} percent of {html.escape(label)}">
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="var(--surface)" stroke="var(--rule)" stroke-width="1" />
  {''.join(ticks)}
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="var(--water)" stroke-width="5"
          stroke-linecap="round" stroke-dasharray="{fraction * circumference:.1f} {circumference:.1f}"
          transform="rotate(-90 {cx} {cy})" />
  <line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="var(--signal)" stroke-width="2" stroke-linecap="round" />
  <circle cx="{cx}" cy="{cy}" r="3.5" fill="var(--signal)" />
  <text x="{cx}" y="{cy + 30}" text-anchor="middle" fill="var(--ink-soft)"
        font-family="IBM Plex Mono, monospace" font-size="12">{fraction * 100:.1f}%</text>
</svg></div>"""


def _rail(ml: float) -> str:
    """A logarithmic rail placing the total among everyday volumes."""
    marks = [
        ("teaspoon", 5), ("cup", 240), ("kettle", 1_700), ("flush", 6_000),
        ("shower", 65_000), ("bathtub", 150_000), ("household/day", 1_135_000),
    ]
    lo, hi = math.log10(1), math.log10(2_000_000)
    w, h = 900, 78
    pad_l, pad_r = 14, 14
    span = w - pad_l - pad_r

    def x_of(v: float) -> float:
        return pad_l + (math.log10(max(v, 1)) - lo) / (hi - lo) * span

    axis_y = 30
    parts = [f'<line x1="{pad_l}" y1="{axis_y}" x2="{w - pad_r}" y2="{axis_y}" stroke="var(--rule)" stroke-width="1.5" />']
    for name, v in marks:
        x = x_of(v)
        parts.append(f'<line x1="{x:.1f}" y1="{axis_y - 5}" x2="{x:.1f}" y2="{axis_y + 5}" stroke="var(--rule)" stroke-width="1.5" />')
        parts.append(
            f'<text x="{x:.1f}" y="{axis_y + 20}" text-anchor="middle" fill="var(--ink-faint)" '
            f'font-family="IBM Plex Sans Condensed, sans-serif" font-size="10.5" '
            f'letter-spacing=".05em">{html.escape(name)}</text>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{axis_y + 33}" text-anchor="middle" fill="var(--ink-faint)" '
            f'font-family="IBM Plex Mono, monospace" font-size="9.5">{format_volume(v)}</text>'
        )

    mx = x_of(max(ml, 1))
    parts.append(f'<line x1="{pad_l}" y1="{axis_y}" x2="{mx:.1f}" y2="{axis_y}" stroke="var(--water)" stroke-width="4" stroke-linecap="round" />')
    parts.append(f'<circle cx="{mx:.1f}" cy="{axis_y}" r="6" fill="var(--water)" stroke="var(--surface)" stroke-width="2" />')
    anchor = "start" if mx < w * 0.75 else "end"
    label_x = mx + 11 if anchor == "start" else mx - 11
    parts.append(
        f'<text x="{label_x:.1f}" y="{axis_y - 12}" text-anchor="{anchor}" fill="var(--water)" '
        f'font-family="IBM Plex Mono, monospace" font-size="13" font-weight="600">you: {format_volume(ml)}</text>'
    )
    return (
        f'<div class="rail rail-wrap"><svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
        f'preserveAspectRatio="xMinYMid meet" role="img" '
        f'aria-label="Your total, {format_volume(ml)}, on a logarithmic scale of everyday water volumes">'
        f'{"".join(parts)}</svg></div>'
    )


def _daily_chart(totals, model: WaterModel) -> str:
    """Bars of water drawn per day, with the peak day labelled."""
    series = sorted(totals.by_date.items())
    values = [(d, model.water_ml(t)) for d, t in series]
    peak = max(v for _, v in values)
    n = len(values)

    w, h = 900, 210
    pad_l, pad_r, pad_t, pad_b = 62, 12, 16, 34
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    slot = plot_w / n
    bar_w = min(slot * 0.72, 46)

    parts = []
    # Gridlines at 0, half and peak, each labelled with a value the chart reaches.
    for frac in (0, 0.5, 1):
        y = pad_t + plot_h * (1 - frac)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" stroke="var(--rule)" stroke-width="1" />')
        parts.append(
            f'<text x="{pad_l - 9}" y="{y + 4:.1f}" text-anchor="end" fill="var(--ink-faint)" '
            f'font-family="IBM Plex Mono, monospace" font-size="10.5">{format_volume(peak * frac)}</text>'
        )

    step = max(1, n // 8)
    for i, (day, value) in enumerate(values):
        bar_h = (value / peak) * plot_h if peak else 0
        x = pad_l + slot * i + (slot - bar_w) / 2
        y = pad_t + plot_h - bar_h
        is_peak = value == peak
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 1.5):.1f}" '
            f'fill="{"var(--water)" if is_peak else "var(--water-soft)"}" rx="1">'
            f'<title>{html.escape(day)}: {format_volume(value)}</title></rect>'
        )
        if i % step == 0 or i == n - 1:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{h - 12}" text-anchor="middle" fill="var(--ink-faint)" '
                f'font-family="IBM Plex Mono, monospace" font-size="10">{html.escape(day[5:])}</text>'
            )
    return (
        f'<div class="chart-wrap"><svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
        f'preserveAspectRatio="xMinYMid meet" role="img" aria-label="Water drawn per day">'
        f'{"".join(parts)}</svg></div>'
    )


def _composition(tokens, model: WaterModel) -> str:
    """Show which kind of token actually draws the water, not just token counts."""
    kinds = [
        ("Output", tokens.output * model.wh_per_output_token, "var(--water)"),
        ("Cache write", tokens.cache_write * model.wh_per_cache_write_token, "var(--brass)"),
        ("Cache read", tokens.cache_read * model.wh_per_cache_read_token, "var(--water-soft)"),
        ("Input", tokens.input * model.wh_per_input_token, "var(--signal)"),
    ]
    total = sum(wh for _, wh, _ in kinds) or 1
    bar = "".join(
        f'<span style="width:{wh / total * 100:.2f}%;background:{color}" title="{name}"></span>'
        for name, wh, color in kinds if wh > 0
    )
    key = "".join(
        f'<div><i style="background:{color}"></i><span>{name}</span>'
        f'<span class="v">{wh / total * 100:.1f}% &middot; {format_volume(wh * model.ml_per_wh)}</span></div>'
        for name, wh, color in sorted(kinds, key=lambda k: -k[1]) if wh > 0
    )
    return f'<div class="comp">{bar}</div><div class="comp-key">{key}</div>'


def _breakdown(mapping, model: WaterModel, heading: str) -> str:
    rows = sorted(mapping.items(), key=lambda kv: -model.water_ml(kv[1]))[:8]
    body = "".join(
        f'<div class="row"><dt>{html.escape(name)}</dt>'
        f'<dd>{format_volume(model.water_ml(tokens))}</dd></div>'
        for name, tokens in rows
    )
    return f"<div><h2>{heading}</h2><dl class=\"rows\" style=\"margin-top:14px\">{body}</dl></div>"


def build_html(totals, model: WaterModel, fragment: bool = False) -> str:
    water = model.water_ml(totals.tokens)
    energy = model.energy_wh(totals.tokens)

    if totals.calls == 0:
        body = (
            '<div class="sheet"><div class="masthead"><h1>Claude Water Meter</h1></div>'
            "<p>No Claude Code transcripts were found, so there is nothing to read yet. "
            "Run <code>water_meter.py</code> on the machine where you use Claude Code.</p></div>"
        )
        return _wrap(body, fragment)

    capacity_label, capacity = next(
        ((l, s) for l, s in COMPARISONS if water < s), COMPARISONS[-1]
    )
    per_day = water / totals.active_days if totals.active_days else 0
    period = (
        f"{totals.first.date()} &ndash; {totals.last.date()}"
        if totals.first and totals.last else "&mdash;"
    )

    single_day = totals.active_days <= 1
    daily_section = ""
    if not single_day:
        daily_section = f"""
    <section>
      <h2>Water drawn per day</h2>
      <p class="caption">Deepest day highlighted. Hover a bar for its reading.</p>
      {_daily_chart(totals, model)}
    </section>"""

    body = f"""
<div class="sheet">
  <div class="masthead">
    <h1>Claude Water Meter</h1>
    <div class="period">
      <span class="label">Period of record</span>
      <span class="num">{period}</span>
      <span class="num">{totals.calls:,} API calls &middot; {totals.active_days} active day{"" if totals.active_days == 1 else "s"}</span>
    </div>
  </div>

  <div class="meter">
    <div>
      <span class="label">Cumulative reading</span>
      <div style="margin-top:10px">{_register(water)}</div>
      <p class="reading-note">
        Estimated water consumed by the datacenters running your Claude usage:
        <strong>{format_volume(water)}</strong>, or about
        <strong>{water / capacity * 100:.0f}% of {html.escape(capacity_label)}</strong>.
      </p>
    </div>
    {_dial(water, capacity, capacity_label)}
  </div>

  <section>
    <h2>Where that falls</h2>
    <p class="caption">Everyday volumes on a logarithmic scale &mdash; each tick is ten times the last.</p>
    {_rail(water)}
  </section>
{daily_section}
  <div class="split" style="margin-top:40px">
    <div>
      <h2>The reading</h2>
      <dl class="rows" style="margin-top:14px">
        <div class="row"><dt>Tokens processed</dt><dd>{totals.tokens.total:,}</dd></div>
        <div class="row"><dt>API calls</dt><dd>{totals.calls:,}</dd></div>
        <div class="row"><dt>Estimated energy</dt><dd>{energy:,.1f} Wh</dd></div>
        <div class="row"><dt>Per API call</dt><dd>{format_volume(water / totals.calls)}</dd></div>
        <div class="row"><dt>Per active day</dt><dd>{format_volume(per_day)}</dd></div>
        <div class="row"><dt>At this rate, one year</dt><dd>{format_volume(per_day * 365)}</dd></div>
        <div class="row total"><dt>Total water</dt><dd>{format_volume(water)}</dd></div>
      </dl>
    </div>
    <div>
      <h2>What draws it</h2>
      <p class="caption">Share of water by how each token was processed. Generating a
      token costs roughly ten times more than reading one from the prompt cache.</p>
      {_composition(totals.tokens, model)}
    </div>
  </div>

  <div class="split" style="margin-top:40px">
    {_breakdown(totals.by_model, model, "By model")}
    {_breakdown(totals.by_project, model, "By project")}
  </div>

  <div class="note">
    <h2>How this is estimated</h2>
    <p>Anthropic does not publish per-token water figures, so this is a model, not a
    meter reading. Tokens are converted to energy &mdash; about
    <code>{model.wh_per_output_token * 1000:.2f} mWh</code> per generated token, roughly a tenth
    of that per prompt token &mdash; and energy to water at
    <code>{model.ml_per_wh} mL/Wh</code>, a rate implied by Google&rsquo;s 2025 disclosure that a
    median Gemini text prompt uses 0.24&nbsp;Wh and 0.26&nbsp;mL. That rate covers both
    on-site cooling and the water consumed generating the electricity.</p>
    <p>Treat the total as an order of magnitude. Real intensity swings by more than
    tenfold with model size, hardware, batch size, and above all datacenter location:
    an evaporatively cooled site in a hot dry region drinks far more than a cool-climate
    one. Every constant lives in <code>water_model.py</code> and can be overridden.</p>
    <p>Read from Claude Code transcripts on this machine. Because each response is
    written to the log several times as it streams, entries are deduplicated by message
    and request ID before counting &mdash; otherwise every total would be several times
    too high.</p>
  </div>
</div>"""
    return _wrap(body, fragment)


def _wrap(body: str, fragment: bool) -> str:
    head = f"<title>Claude Water Meter</title>\n{FONTS}\n<style>{CSS}</style>"
    if fragment:
        return f"{head}\n{body}\n"
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{head}\n</head>\n<body>{body}</body>\n</html>\n"
    )


def write_report(path: Path, totals, model: WaterModel, fragment: bool = False) -> Path:
    path = Path(path)
    path.write_text(build_html(totals, model, fragment), encoding="utf-8")
    return path
