#!/usr/bin/env python3
"""Assemble the Chrome extension from the web app.

The extension is the same app in a popup, so its files are copied from site/
rather than kept as a second copy that can drift. Only manifest.json is authored
here; everything else is generated and gitignored.

    python3 build_extension.py     # writes extension/, ready to load unpacked
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
EXT = ROOT / "extension"

# The service worker and web manifest are for the PWA, not the extension; MV3
# rejects a page that registers one, so they are deliberately left out.
COPY = [
    "index.html", "style.css", "app.js", "water-model.js",
    "constants.json", "icon-192.png", "icon-512.png",
]


def build() -> None:
    EXT.mkdir(exist_ok=True)
    for name in COPY:
        shutil.copy2(SITE / name, EXT / name)

    page = (EXT / "index.html").read_text(encoding="utf-8")
    # A popup has no manifest to link and cannot reach the PWA icon by that name.
    page = re.sub(r'\s*<link rel="manifest"[^>]*>', "", page)
    page = re.sub(r'\s*<link rel="icon"[^>]*>', "", page)
    page = re.sub(r'\s*<link rel="apple-touch-icon"[^>]*>', "", page)
    # Popups size to their content, so give it a workable width and height cap.
    page = page.replace(
        "</head>",
        "<style>body{width:760px;max-height:600px;overflow-y:auto}"
        ".sheet{padding:22px 18px 32px}</style>\n</head>",
    )
    (EXT / "index.html").write_text(page, encoding="utf-8")

    print(f"Built {EXT} — load it via chrome://extensions → Developer mode → Load unpacked")


if __name__ == "__main__":
    build()
