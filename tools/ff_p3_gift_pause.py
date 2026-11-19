#!/usr/bin/env python3
"""TEST ONLY: P3's next-level weapon gift and its START pause.

Starts a 3-player game at level 1 through the game's level-select cheat
(test-only writes), with 5 continues (test-only write). The gift flag
DAT_80093d64 is what the game sets when a level is left over its exit line;
the test writes it with the level-end counter (both test only, as
ff_p3_downcarry.py writes the counter).

  G1  Gift flag, everyone standing: the next level starts with a weapon in
      P3's hand (one of the game's four gift kinds, its own object), as P1 and
      P2 get theirs; seat 3's CROSS then fires it (ammo - 1).
  G2  Gift flag, P3 down at the level end: P3 starts down with no new weapon
      (the game skips downed players), P1 and P2 still get theirs.
  P1  START on seat 3 pauses the game: "PAUSED 3P" in place of "PAUSED 1P",
      the level's frame counter stops; P1's START does nothing in P3's menu;
      seat 3's START resumes and "PAUSED 1P" is back.
  P2  P1's own pause still works and seat 3 cannot end it.
  P3  P3 alone (P1 and P2 down) can pause and resume.
  Q   P3's menu QUIT -> YES leaves the level as the game's QUIT does, and the
      pause state is cleared.

Exit 0 when all pass and no override guard missed.

    python3 tools/ff_p3_gift_pause.py --exe build-net/FightingForce_Recompiled --out DIR [--port 4418]
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
ITEMS = 0x8009A3C8                      # objects, 0x24 each: +0 kind, +0x16 s16 ammo
G_LEVEL, G_DONE, G_LIVES = 0x80093D58, 0x800941B8, 0x80093D50
G_GIFT, G_FRAME, G_PAUSE = 0x80093D64, 0x80093DD0, 0x80093DD8
G_TXT_1P, G_TXT_2P = 0x800924E4, 0x800924E8
GIFT_KINDS = (499, 504, 500, 501)       # DAT_8007a114


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4418)
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

    def show(key):
        return int(re.search(rf"\b{key}=(\d+)", p3("show"))[1])

    def p3_tap(name, hold=4, gap=8):
        p3(f"seat {pad(name):04X}"); c.wait_frames(hold); p3("seat FFFF"); c.wait_frames(gap)

    def rec(i):
        b = c.read(R + i * 0xF4, 0xF4)
        g = lambda o, f: struct.unpack_from(f, b, o)[0]
        return dict(t=g(0x34, "<h"), hp=g(0x42, "<h"), down=g(0x77, "<B"), hb=g(0x7D, "<B"), h=g(0x7E, "<B"))

    def held(r):
        """(object, kind, ammo) of the weapon record r holds, or None."""
        for idx in (r["hb"], r["h"]):
            if idx != 0xFF and idx < 0x40:
                kind = w(ITEMS + idx * 0x24)
                ammo = struct.unpack("<h", c.read(ITEMS + idx * 0x24 + 0x16, 2))[0]
                return idx, kind, ammo
        return None

    def cstr(ptr):
        ptr &= 0xFFFFFFFF
        mem = int(re.search(r"mem=0x([0-9A-Fa-f]+)", p3("show"))[1], 16)
        if not (0x80000000 <= ptr < 0x80200000 or mem <= ptr < mem + 0x440):  # main RAM or the P3 block
            print(f"   text pointer 0x{ptr:08X} outside RAM and the P3 block 0x{mem:08X}", flush=True)
            return None
        return c.read(ptr, 32).split(b"\0")[0].decode("latin-1")

    def pause_text():
        return cstr(w(G_TXT_1P))

    def paused():
        return c.read(G_PAUSE, 1)[0]

    def frozen(n=30):
        f0 = w(G_FRAME)
        c.wait_frames(n)
        return w(G_FRAME) == f0

    def next_level(spawns_before, t=8000):
        """Wait for P3's next spawn and two of its update passes in that level."""
        f0 = c.frame()
        while c.frame() - f0 < t:
            if show("spawns") > spawns_before:
                p0 = show("passes")
                while c.frame() - f0 < t:
                    c.wait_frames(2)
                    if show("passes") >= p0 + 2:
                        return True
            c.wait_frames(10)
        return False

    def shot(tag):
        c.shot(str(out / f"{c.frame():05d}_{tag}.png"))

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
        if not next_level(0):
            print("no level with P3", p3("show")[:300], flush=True)
            return 1
        c.wait_frames(60)
        wr(G_LIVES, 5)
        print(c.frame(), "level", w(G_LEVEL), [rec(i) for i in range(3)], flush=True)

        # --- G1: gift flag, everyone standing
        g0, sp = show("gifts"), show("spawns")
        wr(G_GIFT, 1)
        wr(G_DONE, 1)
        ok = next_level(sp)
        r = [rec(i) for i in range(3)]
        h = [held(x) for x in r]
        g1 = show("gifts")
        print(c.frame(), "G1: level", w(G_LEVEL), "reached", ok, "gifts", g0, "->", g1, flush=True)
        for i in range(3):
            print(f"   P{i + 1}", r[i], "holds", h[i], flush=True)
        shot("G1_start")
        own = h[2] is not None and h[2][0] not in [x[0] for x in h[:2] if x]
        g1_ok = (ok and g1 == g0 + 1 and h[2] is not None and h[2][1] in GIFT_KINDS and own
                 and all(x is not None and x[1] in GIFT_KINDS for x in h[:2]))
        fired = False
        if h[2] is not None:
            idx, kind, ammo0 = h[2]
            c.wait_frames(120)                  # the level fades in first
            for _ in range(3):                  # CROSS fires a held gun (ff_ff_shots.py)
                if held(rec(2)) is None or held(rec(2))[0] != idx:
                    print(c.frame(), "G1: P3 no longer holds", idx, rec(2), flush=True)
                    break
                p3_tap("CROSS", hold=6, gap=40)
                ammo1 = struct.unpack("<h", c.read(ITEMS + idx * 0x24 + 0x16, 2))[0]
                print(c.frame(), f"G1: P3 fires kind {kind}: ammo {ammo0} -> {ammo1}", flush=True)
                if ammo1 == ammo0 - 1:
                    fired = True
                    break
            shot("G1_fired")
        res["G1"] = g1_ok and fired
        print("G1:", "PASS" if res["G1"] else "FAIL", f"(gift {g1_ok}, fired {fired})", flush=True)

        # --- G2: gift flag, P3 down at the level end
        p3("hp 2 0")
        c.wait_frames(150)
        lives = w(G_LIVES)
        g0, sp = show("gifts"), show("spawns")
        print(c.frame(), "G2: P3", rec(2), "continues", lives, flush=True)
        wr(G_GIFT, 1)
        wr(G_DONE, 1)
        ok = next_level(sp)
        r = [rec(i) for i in range(3)]
        h = [held(x) for x in r]
        g2 = show("gifts")
        print(c.frame(), "G2: level", w(G_LEVEL), "reached", ok, "gifts", g0, "->", g2, flush=True)
        for i in range(3):
            print(f"   P{i + 1}", r[i], "holds", h[i], flush=True)
        res["G2"] = (ok and g2 == g0 and r[2]["hp"] < 1 and r[2]["down"] != 0
                     and all(x is not None and x[1] in GIFT_KINDS for x in h[:2]))
        print("G2:", "PASS" if res["G2"] else "FAIL", flush=True)
        c.wait_frames(60)
        p3_tap("CROSS")                       # P3 continues (the game's prompt)
        c.wait_frames(60)
        print(c.frame(), "P3 continued:", rec(2), "continues", lives, "->", w(G_LIVES), flush=True)

        # --- P1: P3 pauses and resumes
        text_1p = pause_text()
        text_2p = cstr(w(G_TXT_2P))
        n0 = show("pauses")
        p3_tap("START", hold=6, gap=30)
        t3, pb, pp3, n1 = pause_text(), paused(), show("pause_p3"), show("pauses")
        fr = frozen()
        shot("P1_p3_paused")
        print(c.frame(), f"P1: texts 1P {text_1p!r} 2P {text_2p!r}; after seat 3 START: byte {pb} "
              f"pause_p3 {pp3} pauses {n0}->{n1} text {t3!r} frozen {fr}", flush=True)
        c.tap("START", frames=6, gap=30)      # P1's START: not P1's menu
        fr_p1 = frozen()
        print(c.frame(), "P1: after P1's START still frozen", fr_p1, flush=True)
        p3_tap("START", hold=6, gap=40)
        moving = not frozen(20)
        back = (paused(), show("pause_p3"), pause_text())
        print(c.frame(), "P1: after seat 3 START: moving", moving, "byte/pause_p3/text", back, flush=True)
        res["P1"] = (pb & 1 and pp3 == 1 and n1 == n0 + 1 and t3 == "PAUSED 3P" and fr and fr_p1
                     and moving and back == (0, 0, text_1p))
        print("P1:", "PASS" if res["P1"] else "FAIL", flush=True)

        # --- P2: P1's own pause, seat 3 cannot end it
        c.tap("START", frames=6, gap=30)
        t1, pb, pp3 = pause_text(), paused(), show("pause_p3")
        fr = frozen()
        shot("P2_p1_paused")
        p3_tap("START", hold=6, gap=30)
        fr3 = frozen()
        c.tap("START", frames=6, gap=40)
        moving = not frozen(20)
        print(c.frame(), f"P2: P1 paused byte {pb} pause_p3 {pp3} text {t1!r} frozen {fr}; "
              f"seat 3 START frozen {fr3}; P1 START moving {moving}", flush=True)
        res["P2"] = pb == 1 and pp3 == 0 and t1 == text_1p and fr and fr3 and moving and paused() == 0
        print("P2:", "PASS" if res["P2"] else "FAIL", flush=True)

        # --- P3: P3 alone
        p3("hp 0 0"); c.wait_frames(2); p3("hp 1 0")
        c.wait_frames(150)
        print(c.frame(), "P3: records", [rec(i) for i in range(3)], "game over", w(0x80093DD4), flush=True)
        p3_tap("START", hold=6, gap=30)
        pb, fr = paused(), frozen()
        shot("P3_alone_paused")
        p3_tap("START", hold=6, gap=40)
        moving = not frozen(20)
        print(c.frame(), "P3: alone paused byte", pb, "frozen", fr, "then moving", moving, flush=True)
        res["P3"] = pb & 1 and fr and moving and paused() == 0
        print("P3:", "PASS" if res["P3"] else "FAIL", flush=True)

        # --- Q: QUIT -> YES from P3's menu
        p3_tap("START", hold=6, gap=30)
        p3_tap("SELECT", hold=6, gap=20)
        shot("Q_menu")
        p3_tap("RIGHT", hold=6, gap=20)
        p3_tap("CROSS", hold=6, gap=20)
        shot("Q_quit_menu")
        p3_tap("LEFT", hold=6, gap=20)
        p3_tap("CROSS", hold=6, gap=20)
        c.wait_frames(300)
        shot("Q_after")
        q = (show("active"), show("pause_p3"), pause_text())
        print(c.frame(), "Q: active/pause_p3/text", q, "byte", paused(), flush=True)
        res["Q"] = q == (0, 0, text_1p)
        print("Q:", "PASS" if res["Q"] else "FAIL", flush=True)

        misses = [(o["addr"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"] if o["guard_misses"]]
        print("guard misses", misses, flush=True)
        print("RESULT", res, flush=True)
        return 0 if all(res.get(k) for k in ("G1", "G2", "P1", "P2", "P3", "Q")) and not misses else 1
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
