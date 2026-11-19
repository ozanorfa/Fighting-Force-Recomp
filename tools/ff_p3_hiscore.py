#!/usr/bin/env python3
"""TEST ONLY: P3's high-score entry and P3's score reset at a new game.
Offline, debug server; P3 via the ff.p3 seat override; scores, lives and
health set with test-only writes / ff.p3 commands.

    python3 tools/ff_p3_hiscore.py --exe build-net/FightingForce_Recompiled --out DIR

Game 1: P1 score 25,000, P3 150,000, everybody down with no lives left ->
game over -> P1's name entry (P1 ends at once: "???") -> table loop -> P3's
entry "CONGRATULATIONS P3!": P3 enters P, 3, X (P1's triangle meanwhile must
not leave the table) -> the table shows P3X / 150000. Then a new game: P3's
score must be 0."""
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

R = 0x800B4318
G_LIVES, G_GAME_OVER = 0x80093D50, 0x80093DD4
SCORE_P1, SCORE_P2 = 0x80094030, 0x80094034
P3_SCORE = 0x9F000000 + 0x80 + 0x10
ROWS = 0x80093E28
CHARSET = "#<ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4370)
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
        return struct.unpack("<i", c.read(a, 4))[0]

    def wr(a, v):
        c.cmd("write_bulk", addr=f"0x{a:08X}", hex=struct.pack("<I", v & 0xFFFFFFFF).hex())

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def p3_tap(name, n=1):
        for _ in range(n):
            p3(f"seat {pad(name):04X}"); c.wait_frames(3); p3("seat FFFF"); c.wait_frames(3)

    def rows():
        b = c.read(ROWS, 120)
        return [(struct.unpack_from("<I", b, 12 * i)[0], b[12 * i + 4:12 * i + 8].split(b"\0")[0].decode("latin1"))
                for i in range(10)]

    def shot(tag):
        c.shot(str(out / f"{c.frame():05d}_{tag}.png"))

    def new_game(title_frame):
        while c.frame() < title_frame:
            c.wait_frames(30)
        c.enter_select()                   # straight-to-select or classic menus
        p3_tap("RIGHT"); c.wait_frames(20)
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3_tap("CROSS")
        f0 = c.frame()
        while c.frame() - f0 < 4000:
            s = p3("show")
            if "passes=" in s and int(s.split("passes=")[1].split()[0]) > 0 and "active=1" in s:
                break
            c.wait_frames(30)

    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        new_game(1400)
        c.wait_frames(120)
        print("game 1 in level | P3 score", w(P3_SCORE), "| table", rows()[:3], "...", rows()[-2:], flush=True)
        wr(SCORE_P1, 25000)
        wr(SCORE_P2, 0)
        p3("score 150000")
        wr(G_LIVES, 0)
        c.wait_frames(10)
        print("scores P1/P2/P3", w(SCORE_P1), w(SCORE_P2), w(P3_SCORE), "| lives", w(G_LIVES), flush=True)
        for r in range(3):
            p3(f"hp {r} 0"); c.wait_frames(4)
        # wait for the high-score routine (P3's state goes PENDING at its start)
        f0 = c.frame()
        seen_entry = False
        while c.frame() - f0 < 6000:
            hs = p3("hs")
            if "state=1" in hs or "state=2" in hs:
                break
            if c.frame() % 300 < 30:
                shot("to_gameover")
            c.wait_frames(30)
        print(c.frame(), "high-score routine started:", p3("hs"), flush=True)
        c.wait_frames(60)
        shot("p1_entry")
        # P1's entry (if any): choose '<' and confirm -> default name
        c.tap("LEFT", frames=4, gap=10)
        c.tap("CROSS", frames=4, gap=60)
        f0 = c.frame()
        while c.frame() - f0 < 600 and "state=2" not in p3("hs"):
            c.wait_frames(10)
        print(c.frame(), "P3 entry:", p3("hs"), flush=True)
        c.wait_frames(40)
        shot("p3_entry_start")
        # P1's triangle must not leave the table while P3 enters
        c.tap("TRIANGLE", frames=4, gap=20)
        print("after P1 triangle:", p3("hs"), flush=True)
        # P3 enters P, 3, X: cursor starts on 'A'
        cur = 2
        for ch in "P3X":
            target = CHARSET.index(ch)
            d = target - cur
            p3_tap("RIGHT" if d > 0 else "LEFT", abs(d))
            p3_tap("CROSS")
            cur = target
            print(f"  entered {ch}:", p3("hs"), flush=True)
        shot("p3_entry_done_letters")
        p3_tap("CROSS")   # cursor jumps to '<' after three letters
        c.wait_frames(30)
        print(c.frame(), "after P3's '<':", p3("hs"), flush=True)
        shot("table")
        print("table:", rows(), flush=True)
        # leave the table (P1 triangle) and start a new game
        c.tap("TRIANGLE", frames=6, gap=200)
        shot("after_table")
        new_game(c.frame() + 1500)
        c.wait_frames(60)
        print("game 2 in level | P3 score", w(P3_SCORE), "| P1/P2", w(SCORE_P1), w(SCORE_P2), flush=True)
        shot("game2")
        return 0
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
