#!/usr/bin/env python3
"""A water meter for your Claude usage.

Reads local Claude Code transcripts, adds up the tokens, and estimates how much
water the datacenters consumed producing them. See water_model.py for how the
estimate is built and how rough it is.

    python3 water_meter.py                  # meter for all recorded usage
    python3 water_meter.py --days 30        # only the last 30 days
    python3 water_meter.py --html out.html  # write a visual report
    python3 water_meter.py --json           # machine-readable totals
"""

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from usage_reader import DEFAULT_ROOT, collect
from water_model import (
    COMPARISONS,
    TokenCounts,
    WaterModel,
    format_volume,
    nearest_comparison,
)

TANK_WIDTH = 22
TANK_HEIGHT = 10


def fill_reference(ml: float):
    """Pick the reference volume to draw the tank against.

    Uses the smallest everyday volume larger than the total, so the tank reads
    as partly full and the label is something you can picture. Once usage
    exceeds the largest reference the tank simply reads full.
    """
    for label, size in COMPARISONS:
        if ml < size:
            return label, size
    return COMPARISONS[-1]


def render_tank(ml: float) -> list:
    """Draw a water tank filled in proportion to the nearest larger reference."""
    label, capacity = fill_reference(ml)
    fraction = min(ml / capacity, 1.0) if capacity else 0.0

    # Each row is an eighth-step, so a partly-filled row can show a meniscus.
    filled_rows = fraction * TANK_HEIGHT
    lines = [" " + "_" * TANK_WIDTH]
    for row in range(TANK_HEIGHT, 0, -1):
        depth = filled_rows - (row - 1)
        if depth >= 1:
            body = "#" * TANK_WIDTH
        elif depth > 0:
            body = "-" * TANK_WIDTH  # the surface, in a partly filled row
        else:
            body = " " * TANK_WIDTH
        lines.append(f"|{body}|")
    lines.append("|" + "_" * TANK_WIDTH + "|")
    lines.append("")
    lines.append(f"  {format_volume(ml)}  =  {fraction * 100:.1f}% of {label}")
    return lines


def sparkline(values: list) -> str:
    """Render a list of numbers as a one-line bar chart."""
    if not values:
        return ""
    blocks = " .:-=+*#%@"
    peak = max(values)
    if peak <= 0:
        return blocks[0] * len(values)
    return "".join(blocks[min(int(v / peak * (len(blocks) - 1) + 0.5), len(blocks) - 1)] for v in values)


def report_text(totals, model: WaterModel, root: Path) -> str:
    water = model.water_ml(totals.tokens)
    energy = model.energy_wh(totals.tokens)
    out = []
    out.append("")
    out.append("  CLAUDE WATER METER")
    out.append("  " + "=" * 46)

    if totals.calls == 0:
        out.append("")
        out.append(f"  No Claude Code transcripts found under {root}.")
        out.append("  Run this on the machine where you use Claude Code.")
        out.append("")
        return "\n".join(out)

    out.append("")
    out.extend("  " + line for line in render_tank(water))
    out.append("")

    label, count = nearest_comparison(water)
    out.append(f"  That is about {count:.1f}x {label}.")
    out.append("")

    out.append("  USAGE")
    out.append(f"    API calls          {totals.calls:,}")
    out.append(f"    Tokens             {totals.tokens.total:,}")
    out.append(f"      output           {totals.tokens.output:,}")
    out.append(f"      input            {totals.tokens.input:,}")
    out.append(f"      cache write      {totals.tokens.cache_write:,}")
    out.append(f"      cache read       {totals.tokens.cache_read:,}")
    out.append(f"    Active days        {totals.active_days:,}")
    if totals.first and totals.last:
        out.append(f"    Range              {totals.first.date()} to {totals.last.date()}")
    out.append("")

    out.append("  ESTIMATE")
    out.append(f"    Energy             {energy:,.1f} Wh")
    out.append(f"    Water              {format_volume(water)}")
    if totals.active_days:
        per_day = water / totals.active_days
        out.append(f"    Per active day     {format_volume(per_day)}")
        out.append(f"    At this rate/year  {format_volume(per_day * 365)}")
    out.append(f"    Per API call       {format_volume(water / totals.calls)}")
    out.append("")

    if len(totals.by_date) > 1:
        recent = sorted(totals.by_date.items())[-60:]
        daily = [model.water_ml(t) for _, t in recent]
        out.append(f"  DAILY  ({recent[0][0]} to {recent[-1][0]}, peak {format_volume(max(daily))})")
        out.append(f"    {sparkline(daily)}")
        out.append("")

    if len(totals.by_model) > 1:
        out.append("  BY MODEL")
        for name, tokens in sorted(
            totals.by_model.items(), key=lambda kv: -model.water_ml(kv[1])
        ):
            out.append(f"    {name:<30} {format_volume(model.water_ml(tokens)):>12}")
        out.append("")

    if len(totals.by_project) > 1:
        out.append("  BY PROJECT")
        top = sorted(totals.by_project.items(), key=lambda kv: -model.water_ml(kv[1]))[:10]
        for name, tokens in top:
            out.append(f"    {name:<30} {format_volume(model.water_ml(tokens)):>12}")
        out.append("")

    out.append("  These are order-of-magnitude estimates, not measurements.")
    out.append("  Anthropic does not publish per-token water figures; see")
    out.append("  water_model.py for the assumptions and how to change them.")
    out.append("")
    return "\n".join(out)


def to_dict(totals, model: WaterModel) -> dict:
    def block(tokens: TokenCounts) -> dict:
        return {
            "tokens": {
                "input": tokens.input,
                "output": tokens.output,
                "cache_write": tokens.cache_write,
                "cache_read": tokens.cache_read,
                "total": tokens.total,
            },
            "energy_wh": round(model.energy_wh(tokens), 4),
            "water_ml": round(model.water_ml(tokens), 4),
        }

    return {
        "total": block(totals.tokens),
        "api_calls": totals.calls,
        "active_days": totals.active_days,
        "first_seen": totals.first.isoformat() if totals.first else None,
        "last_seen": totals.last.isoformat() if totals.last else None,
        "by_date": {d: block(t) for d, t in sorted(totals.by_date.items())},
        "by_model": {m: block(t) for m, t in totals.by_model.items()},
        "by_project": {p: block(t) for p, t in totals.by_project.items()},
        "model_constants": {
            "wh_per_output_token": model.wh_per_output_token,
            "wh_per_input_token": model.wh_per_input_token,
            "wh_per_cache_write_token": model.wh_per_cache_write_token,
            "wh_per_cache_read_token": model.wh_per_cache_read_token,
            "ml_per_wh": model.ml_per_wh,
        },
    }


def export_payload(totals) -> dict:
    """A compact summary the web app imports, carrying no prompt or file content."""
    return {
        "format": "ai-water-meter-export",
        "version": 1,
        "source": "claude-code",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "calls": totals.calls,
        "tokens": {
            "input": totals.tokens.input,
            "output": totals.tokens.output,
            "cache_write": totals.tokens.cache_write,
            "cache_read": totals.tokens.cache_read,
        },
        "days": {
            day: {
                "input": t.input,
                "output": t.output,
                "cache_write": t.cache_write,
                "cache_read": t.cache_read,
            }
            for day, t in sorted(totals.by_date.items())
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate the water footprint of your Claude usage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT,
        help=f"directory holding Claude Code transcripts (default: {DEFAULT_ROOT})",
    )
    parser.add_argument("--days", type=int, help="only count the last N days")
    parser.add_argument(
        "--conversations", type=Path, nargs="+", metavar="FILE",
        help="also count a claude.ai data export (.zip or .json); tokens there "
             "are estimated from text length, so they are rougher",
    )
    parser.add_argument("--html", type=Path, help="write a visual HTML report to this path")
    parser.add_argument("--json", action="store_true", help="print totals as JSON")
    parser.add_argument(
        "--export", type=Path, metavar="FILE",
        help="write a compact file to import into the web app or phone",
    )
    parser.add_argument(
        "--no-sidechains", action="store_true",
        help="exclude subagent calls, counting only the main conversation",
    )
    parser.add_argument(
        "--ml-per-wh", type=float,
        help="override the water intensity of the datacenter and its power, in mL/Wh",
    )
    args = parser.parse_args(argv)

    model = WaterModel.from_constants()
    if args.ml_per_wh is not None:
        model = replace(model, ml_per_wh=args.ml_per_wh)
    since = date.today() - timedelta(days=args.days) if args.days else None
    totals = collect(args.root, include_sidechains=not args.no_sidechains, since=since)

    if args.conversations:
        from conversations import collect_conversations

        collect_conversations(args.conversations, totals)

    if args.json:
        print(json.dumps(to_dict(totals, model), indent=2))
    else:
        print(report_text(totals, model, args.root))

    if args.export:
        args.export.write_text(json.dumps(export_payload(totals), indent=2), encoding="utf-8")
        if not args.json:
            print(f"  Wrote {args.export} — drop this on the web app to import it.\n")

    if args.html:
        from html_report import write_report

        write_report(args.html, totals, model)
        if not args.json:
            print(f"  Wrote {args.html}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
