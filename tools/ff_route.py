#!/usr/bin/env python3
"""Drive a running game (debug server) from the title screen into a level.

    python3 tools/ff_route.py 2p --out ../screenshots/<dir>   # HAWK + MACE
    python3 tools/ff_route.py 1p --out ../screenshots/<dir>   # HAWK

Waits for the title (frame >= --title-frame on a cold boot), then:
START -> main menu -> (2p: DOWN) CROSS -> fighter select -> CROSS on pad 1
(2p: CROSS on pad 2) -> waits through the helicopter intro and the level
load. For 2p, pad 2 is plugged in (held neutral) before the menu so the
"2 PLAYER GAME" entry exists. Takes a screenshot after every step.
Frame counts were measured on cold headless boots.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["1p", "2p"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--title-frame", type=int, default=4300)
    ap.add_argument("--level-wait", type=int, default=3300,
                    help="frames from the last pick to gameplay")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    c = Client().connect(wait_s=60)

    def shot(name: str) -> None:
        f = c.frame()
        c.shot(str(out / f"{name}_f{f}.png"))
        print(name, f, flush=True)

    if args.mode == "2p":
        c.hold(0xFFFF, port=2)
    while c.frame() < args.title_frame:
        c.wait_frames(60)
    shot("r0_title")
    c.tap("START", frames=6, gap=450); shot("r1_menu")
    if args.mode == "2p":
        c.tap("DOWN", frames=6, gap=40); shot("r2_2p")
    c.tap("CROSS", frames=6, gap=400); shot("r3_select")
    c.tap("CROSS", frames=6, gap=60); shot("r4_p1_pick")
    if args.mode == "2p":
        c.tap("CROSS", frames=6, gap=30, port=2)
        shot("r5_p2_pick")
        # With the third-player mod and a device on seat 3 the select screen
        # also waits for P3's cursor: confirm it (no-op without the mod).
        r = c.cmd("mod_cmd", name="ff.p3", args="show")
        if "active=" in (r.get("reply") or ""):
            c.cmd("mod_cmd", name="ff.p3", args=f"seat {pad('CROSS'):04X}")
            c.wait_frames(6)
            c.cmd("mod_cmd", name="ff.p3", args="seat FFFF")
            c.wait_frames(20)
            shot("r5b_p3_pick")
    c.wait_frames(args.level_wait)
    shot("r6_level")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
