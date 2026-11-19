#!/usr/bin/env python3
"""Tile screenshots into one labelled contact sheet (needs Pillow).

    python3 tools/contact_sheet.py <dir> <out.png> [--cols 6] [--width 256]
        [--pick f000265.png,f000628.png]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("out")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--pick", default="")
    args = ap.parse_args()
    d = Path(args.dir)
    files = ([d / n for n in args.pick.split(",")] if args.pick
             else sorted(d.glob("*.png")))
    if not files:
        raise SystemExit("no screenshots")
    tiles = []
    for f in files:
        im = Image.open(f).convert("RGB")
        h = max(1, round(im.height * args.width / im.width))
        tiles.append((f.stem, im.resize((args.width, h))))
    th = max(t[1].height for t in tiles) + 14
    rows = (len(tiles) + args.cols - 1) // args.cols
    sheet = Image.new("RGB", (args.cols * args.width, rows * th), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(tiles):
        x, y = (i % args.cols) * args.width, (i // args.cols) * th
        sheet.paste(im, (x, y + 14))
        draw.text((x + 3, y + 1), name, fill=(255, 255, 0))
    sheet.save(args.out)
    print(f"{len(tiles)} tiles -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
