#!/usr/bin/env python3
"""TEST ONLY: do short presses reach the game? On the fighter select screen
(Quick Start's straight-to-select, 1-player drop-in mode), for press lengths
2..12 frames and two debug input paths (`set_input` hold + release, and the
debug `press` command):
  - P2's X must join (the plugin's own pad read, FUN_8005f5ac, every frame);
  - P1's RIGHT must move P1's cursor (the game's own select code).
Prints a table "taken / IGNORED"; exit 1 if any row was ignored. Written for
a framework A/B (v0.3.7 lost presses shorter than ~8-10 frames in another
title); a quick-tier test since.

    python3 tools/ff_press_probe.py --exe build-net/FightingForce_Recompiled --out DIR
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

G_MENU_STATE, FN_SELECT = 0x800927EC, 0x80050798
G_SEL_PLAYERS, G_CURSORS = 0x80092784, 0x8009277C
LENGTHS = [2, 4, 6, 8, 10, 12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4398)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    log = open(out / "run.log", "w")
    proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(out / "cards"), "--debug-port", str(args.port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    c = Client(port=args.port).connect(wait_s=90)

    def w(a):
        return struct.unpack("<I", c.read(a, 4))[0]

    def p2(buttons):
        c.cmd("set_input", buttons=buttons, port=2, connected=1)

    def hold(port, name, n, how):
        if how == "set_input":
            if port == 2:
                p2(pad(name)); c.wait_frames(n); p2(0xFFFF)
            else:
                c.cmd("set_input", buttons=pad(name)); c.wait_frames(n); c.cmd("clear_input", port=1)
        else:
            c.cmd("press", buttons=pad(name), frames=n, port=port)
            c.wait_frames(n)
        c.wait_frames(30)

    rows = []
    try:
        c.cmd("mod_cmd", name="ff.p3", args="seat off")
        p2(0xFFFF)
        f0 = c.frame()
        while c.frame() - f0 < 6000 and w(G_MENU_STATE) != FN_SELECT:
            c.wait_frames(15)
        c.wait_frames(60)
        print("select at frame", c.frame(), "players", w(G_SEL_PLAYERS), flush=True)
        for how in ("set_input", "press"):
            for n in LENGTHS:
                # P2 joins with X
                if w(G_SEL_PLAYERS) != 1:
                    hold(2, "TRIANGLE", 12, "set_input")
                hold(2, "CROSS", n, how)
                p2_in = w(G_SEL_PLAYERS) == 2
                if p2_in:
                    hold(2, "TRIANGLE", 12, "set_input")      # leave again
                # P1 moves its cursor
                before = w(G_CURSORS)
                hold(1, "RIGHT" if before < 3 else "LEFT", n, how)
                moved = w(G_CURSORS) != before
                rows.append((how, n, p2_in, moved))
                print(f"{how:9s} {n:2d} frames: P2 X {'taken' if p2_in else 'IGNORED'}, "
                      f"P1 cursor {'moved' if moved else 'IGNORED'}", flush=True)
        lost = [r for r in rows if not (r[2] and r[3])]
        print(f"{len(rows) - len(lost)} of {len(rows)} rows taken", flush=True)
        return 1 if lost else 0
    finally:
        try:
            c.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
        if proc.poll() is None:
            proc.terminate()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
