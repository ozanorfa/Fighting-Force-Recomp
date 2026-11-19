#!/usr/bin/env python3
"""Fighting Force campaign saves after a netplay match.

A match runs on the host's memory card: the host's card holds the game's
checkpoint saves. Guests play from a copy (`<saves>/netplay/card1.mcd`),
so their own card is never written. To host the next session yourself,
copy the match's card over your own with `import`.

    python tools/ff_match_save.py status            # what each card holds
    python tools/ff_match_save.py import            # match copy -> your card 1 (backup kept)

Options: --saves DIR (default: <repo>/saves, the folder tools/ff_netplay.py
uses; build-net/saves is also listed by `status`).
Standard library only (macOS / Windows).
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE_NAME = b"BASLUS-00433"
FIGHTERS = {0: "Hawk", 1: "Mace", 2: "Smasher", 3: "Alana"}
BLOCK = 0x2000


def find_save(card: bytes) -> bytes | None:
    """The 8 KB save block of Fighting Force on a PS1 card image, or None."""
    if len(card) != 0x20000 or card[:2] != b"MC":
        return None
    for slot in range(1, 16):
        entry = card[slot * 0x80:(slot + 1) * 0x80]
        if (entry[0] & 0xF0) == 0x50 and entry[0x0A:0x0A + len(FILE_NAME)] == FILE_NAME:
            return card[slot * BLOCK:(slot + 1) * BLOCK]
    return None


def describe(block: bytes) -> str:
    d = block[0x200:]
    level, players, t1, t2 = d[0], d[1], d[2], d[3]
    s1, s2 = struct.unpack_from("<II", d, 8)
    lives = struct.unpack_from("<i", d, 0x18)[0]
    names = [FIGHTERS.get(t1, "?")]
    if players == 2:
        names.append(FIGHTERS.get(t2, "?"))
    text = f"level id {level}, {' + '.join(names)}, scores {s1}" + (f" / {s2}" if players == 2 else "")
    text += f", lives {lives}"
    p3 = d[0xA4:0xAC]
    if p3[0] == 0x50 and p3[1] <= 3:
        hp, score = struct.unpack_from("<hI", p3, 2)
        text += f", P3 {FIGHTERS[p3[1]]} (score {score}, health {hp})"
    return text


def card_status(path: Path) -> str:
    if not path.exists():
        return "no file"
    block = find_save(path.read_bytes())
    if block is None:
        return "no Fighting Force save"
    return describe(block)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "import"])
    ap.add_argument("--saves", default=str(ROOT / "saves"))
    args = ap.parse_args()
    saves = Path(args.saves)
    if args.action == "status":
        dirs = [saves]
        extra = ROOT / "build-net" / "saves"
        if extra != saves:
            dirs.append(extra)
        for d in dirs:
            print(f"{d}:")
            for rel in ("card1.mcd", "netplay/card1.mcd"):
                label = "your card 1   " if rel == "card1.mcd" else "last match    "
                print(f"  {label} {card_status(d / rel)}")
        return 0
    src, dst = saves / "netplay" / "card1.mcd", saves / "card1.mcd"
    if not src.exists() or find_save(src.read_bytes()) is None:
        print(f"no match save at {src}")
        return 1
    if dst.exists():
        backup = dst.with_name(f"card1.mcd.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copyfile(dst, backup)
        print(f"backup of your card: {backup}")
    shutil.copyfile(src, dst)
    print(f"your card 1 now holds the match's save: {card_status(dst)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
