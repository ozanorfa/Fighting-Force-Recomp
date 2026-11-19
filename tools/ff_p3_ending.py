#!/usr/bin/env python3
"""TEST ONLY: the end of the game with three players. Start at the last level
(id 0x18) through the game's level-select cheat (test-only writes), give P3 a
top-ten score (test-only ff.p3 score), end the wave script (test-only writes)
and let the autopilot finish the level; then follow the ending (cutscenes 6
and 7 -- Quick Start skips them), P3's high-score entry and the return to
the title. Exit 0 when the cutscenes ran with P3 out of record 2 (its
in-level flag clear), P3's entry (END, >= 250,000) is in the table, and the
flag is still clear after the table.

    python3 tools/ff_p3_ending.py --exe build-net/FightingForce_Recompiled --out DIR [--port 4369]
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
EST = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4369)
    ap.add_argument("--level", type=int, default=0x18)
    ap.add_argument("--walk", type=int, default=3000, help="autopilot frames before forcing the level end")
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

    def recs(n=8):
        b = c.read(R, n * 0xF4)
        g = lambda i, o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        return [dict(t=g(i, 0x34, "<h"), hp=g(i, 0x42, "<h"), x=g(i, 0x28, "<i") >> 16, z=g(i, 0x30, "<i") >> 16)
                for i in range(n)]

    def shot(tag):
        c.shot(str(out / f"{c.frame():05d}_{tag}.png"))

    def send(i, word):
        if i == 2:
            p3(f"seat {word:04X}")
        elif i == 1:
            c.cmd("set_input", buttons=word, port=2, connected=1)
        else:
            c.cmd("set_input", buttons=word)

    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        while c.frame() < 1400:
            c.wait_frames(60)
        c.enter_select()                   # straight-to-select or classic menus
        wr(0x80093D68, w(0x80093D68) | 2)
        wr(0x800927B4, args.level)
        p3_tap("RIGHT")
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3_tap("CROSS")
        f0 = c.frame()
        while c.frame() - f0 < 6000:
            s = p3("show")
            if "active=1" in s and int(s.split("passes=")[1].split()[0]) > 0:
                break
            c.wait_frames(30)
        print(c.frame(), "level", w(G_LEVEL), "players", [(r["t"], r["hp"]) for r in recs(3)], flush=True)
        shot("last_level")
        p3("score 250000")
        wr(G_LIVES, 9)
        c.cmd("write_bulk", addr="0x80093DD9", hex="01"); c.wait_frames(2)
        sp = w(0x800946B8) & 0xFFFFFFFF
        term = next(sp + 2 * i for i in range(512) if struct.unpack("<h", c.read(sp + 2 * i, 2))[0] == -1)
        wr(0x800946B8, term)
        live = sum(1 for r in recs()[3:] if r["t"] not in (-1, 0x11, 0x33) and r["hp"] > 0)
        wr(0x80093DBC, live); wr(0x80093DC0, 0)
        c.cmd("write_bulk", addr="0x80093DD9", hex="00")
        f0 = c.frame(); tick = 0
        while w(G_DONE) <= 0 and c.frame() - f0 < args.walk:
            s = recs()
            ens = [r for r in s[3:] if r["t"] not in (-1, 0x11) and r["hp"] > 0]
            for i in range(3):
                me = s[i]
                if me["t"] == -1 or me["hp"] <= 0:
                    send(i, pad("CROSS") if tick % 8 == 0 else 0xFFFF)
                    continue
                if not ens:
                    send(i, pad("UP") if tick % 3 else 0xFFFF)
                    continue
                e = min(ens, key=lambda r: max(abs(r["x"] - me["x"]), abs(r["z"] - me["z"])))
                d = max(EST, key=lambda k: EST[k][0] * (e["x"] - me["x"]) + EST[k][1] * (e["z"] - me["z"]))
                near = max(abs(e["x"] - me["x"]), abs(e["z"] - me["z"])) <= 140
                k = tick % 3
                send(i, pad(d) if (not near or k == 0) else (pad("SQUARE") if k == 1 else 0xFFFF))
            tick += 1
            c.wait_frames(4)
            if c.read(0x80093DD9, 1)[0]:
                c.cmd("write_bulk", addr="0x80093DD9", hex="00")
        for i in range(3):
            send(i, 0xFFFF)
        # P3 stays connected with no buttons (a seat without a device skips its high-score entry)
        p3("seat FFFF"); c.cmd("clear_input", port=1); c.hold(0xFFFF, port=2)
        mem = int(re.search(r"mem=0x([0-9A-Fa-f]+)", p3("show"))[1], 16)

        def active():                    # P3 in record 2 (mod memory M_ACTIVE, read from the guest)
            return w(mem + 0x14)
        print(c.frame(), "level done by play:", w(G_DONE) > 0, "| level", w(G_LEVEL), flush=True)
        if w(G_DONE) <= 0:
            # test only: start the level-end sequence the exit line would start
            wr(G_DONE, 1)
            print(c.frame(), "level-end counter started by a test write", flush=True)
        shot("level_done")
        # follow the ending until the high-score routine (P3's entry state) or a timeout
        f0 = c.frame(); seen = set(); live_in_scene = []
        while c.frame() - f0 < 12000:
            lv = w(G_LEVEL)
            hs = p3("hs")
            act = active()
            if lv >= 0x1B and act:             # a cutscene (level id 0x1B + n): P3 is out of record 2
                live_in_scene.append((c.frame(), lv))
            key = (lv, hs.split()[0])
            if key not in seen:
                seen.add(key)
                print(c.frame(), "level", lv, hs, "active", act, flush=True)
                shot(f"end_lv{lv}_{hs.split()[0]}")
            if "state=2" in hs:
                break
            if lv == 0x22 and (c.frame() - f0) % 240 < 30:
                c.tap("CROSS", frames=6, gap=10)   # "THE END ?? -- X CONTINUE" (P1 / P2 pads)
            c.wait_frames(30)
        c.wait_frames(60)
        shot("p3_entry")
        for ch in "END":
            cur = int(p3("hs").split("cur=")[1].split()[0])
            target = "#<ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?".index(ch)
            d = target - cur
            p3_tap("RIGHT" if d > 0 else "LEFT", abs(d)); p3_tap("CROSS")
        p3_tap("CROSS")
        c.wait_frames(60)
        shot("table")
        b = c.read(0x80093E28, 120)
        table = [(struct.unpack_from("<I", b, 12 * i)[0], b[12 * i + 4:12 * i + 8].split(b"\0")[0].decode("latin1"))
                 for i in range(10)]
        print("table:", table, flush=True)
        c.tap("TRIANGLE", frames=6, gap=300)
        f0 = c.frame()
        c.wait_frames(1200)
        shot("after")
        act = active()
        print(c.frame(), "after the table: level", w(G_LEVEL), "P3 active", act, flush=True)
        print([(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]
               if o["guard_misses"]], flush=True)
        scenes = sorted({lv for lv, _ in seen if lv >= 0x1B})
        print("cutscene levels seen", [hex(v) for v in scenes], "| P3 active in a cutscene:", live_in_scene[:5],
              flush=True)
        entry = any(n == "END" and v >= 250000 for v, n in table)
        print("P3's entry in the table:", entry, flush=True)
        return 0 if scenes and not live_in_scene and act == 0 and entry else 1
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
