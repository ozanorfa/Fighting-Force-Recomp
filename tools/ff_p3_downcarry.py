#!/usr/bin/env python3
"""TEST ONLY: P3's down state across a level start.

Starts a 3-player game at level 1 through the game's level-select cheat
(test-only writes), with 5 continues (test-only write), then plays three
level ends by writing the level-end counter (test only, as ff_p3_ending.py):

  A  P2 down at a level end, P3 standing. The next level must start P3
     standing (down flag +0x77 = 0, health > 0) and moving on seat 3's UP.
     (Before the fix P3 took P2's down flag and stood frozen.) Then P2
     continues with X through the game's own prompt.
  B  P3 down at a level end, P2 standing. The next level must start P3 down
     (health 0, down flag set) with the continues unchanged; seat 3's X then
     continues it (continues - 1, health 0x17C). (Before the fix P3 came
     back for free with P2's health.)
  C  P3 down, then X on seat 3 during the level-end sequence. The next level
     must start P3 the way the level ended: standing (with at most that
     health: enemies may hit it before the read) if the continue was taken,
     down if the prompt was not offered there.

"Down" here comes from a test-only health write of 0; the game's own player
update then sets the down flag, as after a knock-out. Exit 0 when A, B and C
pass and no override guard missed.

    python3 tools/ff_p3_downcarry.py --exe build-net/FightingForce_Recompiled --out DIR [--port 4417]
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

R = 0x800B4318
G_LEVEL, G_DONE, G_LIVES = 0x80093D58, 0x800941B8, 0x80093D50
RESPAWN = 0x17C


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4417)
    ap.add_argument("--level", type=int, default=1)
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

    def wr(a, v, f="<I"):
        c.cmd("write_bulk", addr=f"0x{a:08X}", hex=struct.pack(f, v).hex())

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def p3_tap(name, n=1):
        for _ in range(n):
            p3(f"seat {pad(name):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(8)

    def rec(i):
        b = c.read(R + i * 0xF4, 0xF4)
        g = lambda o, f: struct.unpack_from(f, b, o)[0]
        return dict(t=g(0x34, "<h"), hp=g(0x42, "<h"), down=g(0x77, "<B"),
                    x=g(0x28, "<i") >> 16, z=g(0x30, "<i") >> 16)

    def counts():
        s = p3("show")
        return int(re.search(r"spawns=(\d+)", s)[1]), int(re.search(r"passes=(\d+)", s)[1])

    def in_level_after(spawns_before, t=8000):
        """Wait for P3's next spawn and 30 of its update passes in that level."""
        f0 = c.frame()
        while c.frame() - f0 < t:
            sp, ps = counts()
            if sp > spawns_before:
                p0 = ps
                while c.frame() - f0 < t:
                    c.wait_frames(10)
                    if counts()[1] >= p0 + 30:
                        return True
            c.wait_frames(20)
        return False

    def shot(tag):
        c.shot(str(out / f"{c.frame():05d}_{tag}.png"))

    def down_p(r, frames=150):
        p3(f"hp {r} 0")
        c.wait_frames(frames)     # the player update sets +0x77 and counts it to 0x3E

    res = {}
    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        while c.frame() < 1400:
            c.wait_frames(60)
        c.enter_select()
        wr(0x80093D68, w(0x80093D68) | 2)
        wr(0x800927B4, args.level)
        p3_tap("RIGHT")
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3_tap("CROSS")
        if not in_level_after(0):
            print("no level with P3", p3("show")[:300], flush=True)
            return 1
        wr(G_LIVES, 5)
        print(c.frame(), "level", w(G_LEVEL), "start", [rec(i) for i in range(3)], flush=True)

        # --- A: P2 down at the level end, P3 standing
        down_p(1)
        hp3 = rec(2)["hp"]
        print(c.frame(), "A: P2", rec(1), "P3", rec(2), flush=True)
        sp, _ = counts()
        wr(G_DONE, 1)
        ok = in_level_after(sp)
        a = [rec(i) for i in range(3)]
        print(c.frame(), "A: next level", w(G_LEVEL), "reached", ok, a, flush=True)
        shot("A_start")
        x0, z0 = a[2]["x"], a[2]["z"]
        p3(f"seat {pad('UP'):04X}"); c.wait_frames(60); p3("seat FFFF"); c.wait_frames(4)
        b = rec(2)
        moved = (b["x"], b["z"]) != (x0, z0)
        print(c.frame(), "A: P3 after 60 frames of UP", b, "moved", moved, flush=True)
        res["A"] = ok and a[1]["hp"] < 1 and a[2]["down"] == 0 and a[2]["hp"] > 0 and moved
        print("A:", "PASS" if res["A"] else "FAIL", f"(P3 hp at the end {hp3}, now {a[2]['hp']})", flush=True)
        # P2 back through the game's own continue prompt (X on port 2)
        lives = w(G_LIVES)
        c.tap("CROSS", frames=6, gap=30, port=2)
        c.wait_frames(30)
        print(c.frame(), "P2 continue:", rec(1), "continues", lives, "->", w(G_LIVES), flush=True)

        # --- B: P3 down at the level end, P2 standing
        down_p(2)
        lives_b = w(G_LIVES)
        print(c.frame(), "B: P3", rec(2), "continues", lives_b, flush=True)
        sp, _ = counts()
        wr(G_DONE, 1)
        ok = in_level_after(sp)
        d = [rec(i) for i in range(3)]
        lives_start = w(G_LIVES)
        print(c.frame(), "B: next level", w(G_LEVEL), "reached", ok, d, "continues", lives_start, flush=True)
        shot("B_start")
        start_ok = ok and d[1]["hp"] > 0 and d[2]["hp"] < 1 and d[2]["down"] != 0 and lives_start == lives_b
        c.wait_frames(60)
        p3_tap("CROSS")
        c.wait_frames(30)
        e = rec(2)
        lives_after = w(G_LIVES)
        cont_ok = e["hp"] == RESPAWN and e["down"] == 0 and lives_after == lives_start - 1
        print(c.frame(), "B: after X on seat 3", e, "continues", lives_after, flush=True)
        shot("B_continued")
        res["B"] = start_ok and cont_ok
        print("B:", "PASS" if res["B"] else "FAIL", f"(start {start_ok}, continue {cont_ok})", flush=True)

        # --- C: P3 down, X during the level-end sequence
        down_p(2)
        sp, _ = counts()
        wr(G_DONE, 1)
        c.wait_frames(40)
        lives_c = w(G_LIVES)
        p3_tap("CROSS")
        c.wait_frames(20)
        end = rec(2)
        took = w(G_LIVES) == lives_c - 1 and end["hp"] > 0
        print(c.frame(), "C: during the level end", end, "continue taken:", took, flush=True)
        ok = in_level_after(sp)
        f = rec(2)
        print(c.frame(), "C: next level", w(G_LEVEL), "reached", ok, f, flush=True)
        shot("C_start")
        if took:
            # standing with that health; enemies may already have hit P3 in
            # the 30 passes before this read, so not more than it, but > 0
            res["C"] = ok and f["down"] == 0 and 0 < f["hp"] <= end["hp"]
        else:
            res["C"] = ok and f["hp"] < 1 and f["down"] != 0
        print("C:", "PASS" if res["C"] else "FAIL", "(continued at the level end)" if took else
              "(no continue at the level end: P3 stays down)", flush=True)

        misses = [(o["addr"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"] if o["guard_misses"]]
        print("guard misses", misses, flush=True)
        print("RESULT", res, flush=True)
        return 0 if all(res.get(k) for k in "ABC") and not misses else 1
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
