"""Estimate the water footprint of Claude usage.

Nobody publishes a per-token water number for Claude, so this converts tokens to
energy and energy to water using two public anchors:

  1. Energy per token. A decode step runs one forward pass to emit a single
     token, so it is bound by memory bandwidth and uses the hardware poorly.
     Prefill pushes the whole prompt through in parallel and lands roughly an
     order of magnitude cheaper per token. The prefill variants below are scaled
     against each other by their billing ratios (cache write 1.25x base input,
     cache read 0.1x), which track how much work each actually skips.

  2. Water per unit of energy. Google's 2025 disclosure for a median Gemini text
     prompt -- 0.24 Wh and 0.26 mL -- implies ~1.08 mL/Wh. That figure covers
     both on-site cooling and the water consumed generating the electricity.

Treat the result as an order-of-magnitude estimate, not a meter reading. The
constants are the honest weak point: energy per token varies with model size,
hardware, batch size, and datacenter, and water intensity varies enormously by
region -- a hydro-cooled datacenter in a cold climate and a evaporatively cooled
one in a hot dry one differ by more than 10x. Every constant is overridable so
you can substitute better numbers when you have them.
"""

import json
from dataclasses import dataclass
from pathlib import Path

# The web app and the CLI share one constants file so the two implementations
# cannot drift on the numbers. Built-in defaults below keep the CLI working if
# the file is missing or unreadable.
CONSTANTS_PATH = Path(__file__).parent / "site" / "constants.json"


def load_constants() -> dict:
    try:
        return json.loads(CONSTANTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@dataclass(frozen=True)
class WaterModel:
    """Token -> energy -> water conversion factors."""

    # Watt-hours per token, by how the token was processed.
    wh_per_output_token: float = 0.0006  # ~2.2 J; one forward pass per token
    wh_per_input_token: float = 0.00006  # prefill, ~10x cheaper per token
    wh_per_cache_write_token: float = 0.000075  # prefill + store (1.25x input)
    wh_per_cache_read_token: float = 0.000006  # KV load only (0.1x input)

    # Millilitres of water consumed per watt-hour, on-site cooling plus the
    # water used to generate the power.
    ml_per_wh: float = 1.08

    @classmethod
    def from_constants(cls) -> "WaterModel":
        """Build a model from site/constants.json, falling back per-field."""
        data = load_constants()
        tokens = data.get("claude_tokens_wh") or {}
        water = data.get("water") or {}
        defaults = cls()
        return cls(
            wh_per_output_token=tokens.get("output", defaults.wh_per_output_token),
            wh_per_input_token=tokens.get("input", defaults.wh_per_input_token),
            wh_per_cache_write_token=tokens.get("cache_write", defaults.wh_per_cache_write_token),
            wh_per_cache_read_token=tokens.get("cache_read", defaults.wh_per_cache_read_token),
            ml_per_wh=water.get("ml_per_wh", defaults.ml_per_wh),
        )

    def energy_wh(self, tokens: "TokenCounts") -> float:
        return (
            tokens.output * self.wh_per_output_token
            + tokens.input * self.wh_per_input_token
            + tokens.cache_write * self.wh_per_cache_write_token
            + tokens.cache_read * self.wh_per_cache_read_token
        )

    def water_ml(self, tokens: "TokenCounts") -> float:
        return self.energy_wh(tokens) * self.ml_per_wh


@dataclass
class TokenCounts:
    """Tokens split by how they were billed, which is also how they were computed."""

    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def __add__(self, other: "TokenCounts") -> "TokenCounts":
        return TokenCounts(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_write=self.cache_write + other.cache_write,
            cache_read=self.cache_read + other.cache_read,
        )


# Everyday water uses, in millilitres, for putting a total in perspective.
# Household figures are typical US/EU values; the food and textile figures are
# water footprints covering the whole supply chain, which is why they dwarf the
# rest.
_SHARED_COMPARISONS = [
    (c["label"], c["ml"]) for c in load_constants().get("comparisons", [])
]

COMPARISONS = _SHARED_COMPARISONS or [
    ("a teaspoon", 5),
    ("a shot glass", 44),
    ("a cup of coffee", 240),
    ("a water bottle", 500),
    ("a kettle boiled full", 1_700),
    ("a toilet flush", 6_000),
    ("a dishwasher cycle", 15_000),
    ("a washing machine load", 50_000),
    ("a 10-minute shower", 65_000),
    ("a full bathtub", 150_000),
    ("a day of average household water use", 1_135_000),
    ("a pair of jeans (supply chain)", 8_000_000),
]


def nearest_comparison(ml: float):
    """Return (label, count) describing a volume in terms of a familiar object.

    Picks the largest reference the volume covers at least once, so the count
    stays small and readable, and falls back to the smallest reference for
    volumes below a teaspoon.
    """
    if ml <= 0:
        return ("a teaspoon", 0.0)
    for label, size in reversed(COMPARISONS):
        if ml >= size:
            return (label, ml / size)
    label, size = COMPARISONS[0]
    return (label, ml / size)


def format_volume(ml: float) -> str:
    """Render millilitres with a unit that keeps the number human-sized."""
    if ml < 1:
        return f"{ml:.2f} mL"
    if ml < 1_000:
        return f"{ml:.1f} mL"
    if ml < 1_000_000:
        return f"{ml / 1_000:.2f} L"
    return f"{ml / 1_000_000:.2f} m³"
