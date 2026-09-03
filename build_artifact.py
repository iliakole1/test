#!/usr/bin/env python3
"""Inline the web app into one self-contained HTML file.

Used for publishing the app somewhere that serves a single file rather than a
directory. `--fragment` drops the document skeleton for hosts that supply their
own; the default writes a standalone page.

    python3 build_artifact.py out.html [--fragment]
"""

import json
import re
import sys
from pathlib import Path

SITE = Path(__file__).parent / "site"


def build(fragment: bool = False) -> str:
    page = (SITE / "index.html").read_text(encoding="utf-8")
    constants = json.loads((SITE / "constants.json").read_text(encoding="utf-8"))

    inline = (
        "<style>\n" + (SITE / "style.css").read_text(encoding="utf-8") + "\n</style>\n"
        "<script>window.WATER_CONSTANTS = " + json.dumps(constants) + ";</script>\n"
        "<script>\n" + (SITE / "water-model.js").read_text(encoding="utf-8") + "\n</script>\n"
        "<script>\n" + (SITE / "app.js").read_text(encoding="utf-8") + "\n</script>"
    )

    # Drop the tags that only make sense when the app is served as a directory.
    for pattern in (
        r'\s*<link rel="stylesheet" href="style\.css">',
        r'\s*<link rel="manifest"[^>]*>',
        r'\s*<link rel="icon"[^>]*>',
        r'\s*<link rel="apple-touch-icon"[^>]*>',
        r'\s*<script src="water-model\.js"></script>',
        r'\s*<script src="app\.js"></script>',
    ):
        page = re.sub(pattern, "", page)

    if not fragment:
        return page.replace("</body>", inline + "\n</body>")

    # Split before inlining, or the assets land in the output twice.
    head = re.search(r"<head>(.*?)</head>", page, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", page, re.S).group(1)
    keep = "\n".join(
        line for line in head.splitlines()
        if "<title>" in line or "fonts.googleapis" in line or "fonts.gstatic" in line
    )
    return keep + "\n" + body + "\n" + inline


if __name__ == "__main__":
    args = sys.argv[1:]
    fragment = "--fragment" in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        sys.exit("usage: build_artifact.py OUT.html [--fragment]")
    out = Path(paths[0])
    out.write_text(build(fragment), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")
