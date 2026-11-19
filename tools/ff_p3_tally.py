#!/usr/bin/env python3
"""TEST ONLY: P3 breaks a cash object, then the level-end tally turns P3's
bonus units into points (and an extra life). Offline, debug server; P3 via the
ff.p3 seat test override; P3 is moved next to the object with a debug write.

    python3 tools/ff_p3_tally.py --exe build-net/FightingForce_Recompiled --out DIR

Object table (the level-end bonus): 0x8009EC70, 0x18-byte entries,
count DAT_8009479c; +0 x min, +2 z min, +4 x max, +6 z max (s16), +0xA shape,
+0xC handler type (5 = cash object, FUN_8001f518), +0x10 alive (>= 0),
+0x14 object instance."""
from __future__ import annotations

import argparse
import math
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

R = 0x800B4318
OBJ, OBJ_N = 0x8009EC70, 0x8009479C
G_LEVEL, G_DONE, G_LIVES = 0x80093D58, 0x800941B8, 0x80093D50
P_SCORE, P_LIFE_AT = 0x80094030, 0x800941B0          # [p] for p = 0, 1
MEM = 0x9F000000
M_LIFE_AT, M_P3_SCORE = MEM + 0x74, MEM + 0x80 + 0x10
PAUSE, SCRIPT, WAVE_LEFT, WAVE_X = 0x80093DD9, 0x800946B8, 0x80093DBC, 0x80093DC0


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

    def w(a, f="<i"):
        return struct.unpack(f, c.read(a, struct.calcsize(f)))[0]

    def wr(a, v, f="<I"):
        c.cmd("write_bulk", addr=f"0x{a:08X}", hex=struct.pack(f, v).hex())

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def stable(a, n):
        b = c.read(a, n)
        for _ in range(8):
            b2 = c.read(a, n)
            if b2 == b:
                break
            b = b2
        return b

    def recs(n=8):
        b = stable(R, n * 0xF4)
        g = lambda i, o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        return [dict(t=g(i, 0x34, "<h"), hp=g(i, 0x42, "<h"), x=g(i, 0x28, "<i") >> 16, z=g(i, 0x30, "<i") >> 16,
                     bonus=g(i, 0x8A, "<h")) for i in range(n)]

    def objects():
        n = w(OBJ_N)
        b = c.read(OBJ, max(0, n) * 0x18)
        res = []
        for k in range(n):
            x0, z0, x1, z1 = struct.unpack_from("<4h", b, k * 0x18)
            shape, typ = struct.unpack_from("<hh", b, k * 0x18 + 0xA)
            alive = struct.unpack_from("<h", b, k * 0x18 + 0x10)[0]
            inst = struct.unpack_from("<I", b, k * 0x18 + 0x14)[0]
            model = w(inst) if 0x80000000 <= inst < 0x80200000 else None
            res.append(dict(k=k, box=(x0, z0, x1, z1), shape=shape, type=typ, alive=alive, model=model))
        return res

    def state(tag):
        s = recs(3)
        print(f"{c.frame()} {tag}: bonus P1/P2/P3 {[r['bonus'] for r in s]} | score P1 {w(P_SCORE)} "
              f"P2 {w(P_SCORE + 4)} P3 {w(M_P3_SCORE)} | lives {w(G_LIVES)} | P3 next life {w(M_LIFE_AT)} "
              f"| level {w(G_LEVEL)} done {w(G_DONE)}", flush=True)

    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        while c.frame() < 4300:
            c.wait_frames(60)
        c.enter_select()                   # straight-to-select or classic menus
        p3("seat " + f"{pad('RIGHT'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(20)
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3("seat " + f"{pad('CROSS'):04X}"); c.wait_frames(4); p3("seat FFFF")
        c.wait_frames(3300)
        state("level start")
        objs = objects()
        for o in objs:
            print("  object", o, flush=True)
        # breakables that pay a bonus unit to the attacker's record:
        # handler 5 (FUN_8001f518, cash) and 10 (FUN_8001ff08)
        cash = [o for o in objs if o["type"] in (5, 10) and o["alive"] >= 0]
        if not cash:
            print("no bonus object (handler 5 / 10) in this level", flush=True)
            return 1
        est = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}

        def toward(dx, dz):
            return max(est, key=lambda k: (est[k][0] * dx + est[k][1] * dz) / math.hypot(*est[k]))

        def centre(o):
            if o["shape"] == 1:          # rotated box: centre from the object instance
                inst = struct.unpack("<I", c.read(OBJ + o["k"] * 0x18 + 0x14, 4))[0]
                h = sorted((abs(o["box"][0]), abs(o["box"][1])))
                return w(inst + 4) >> 16, w(inst + 0xC) >> 16, h
            x0, z0, x1, z1 = o["box"]
            return (x0 + x1) // 2, (z0 + z1) // 2, sorted((abs(x1 - x0) // 2, abs(z1 - z0) // 2))

        got = False
        for o in cash:
            cx, cz, half = centre(o)
            tries = [(ux, uz, dist) for dist in (half[0] + 40, half[0] + 90, half[1] + 40)
                     for ux, uz in ((-1, 0), (1, 0), (0, -1), (0, 1))]
            for ux, uz, dist in tries:
                tx, tz = cx + ux * dist, cz + uz * dist
                # P1 / P2 near P3 so the leash lets P3 move (test-only writes)
                for i, (px, pz) in enumerate(((tx + ux * 150 - 120 * uz, tz + uz * 150 - 120 * ux),
                                              (tx + ux * 150 + 120 * uz, tz + uz * 150 + 120 * ux), (tx, tz))):
                    wr(R + i * 0xF4 + 0x28, (px << 16) & 0xFFFFFFFF)
                    wr(R + i * 0xF4 + 0x30, (pz << 16) & 0xFFFFFFFF)
                c.wait_frames(4)
                d = toward(cx - tx, cz - tz)
                b0 = recs(3)[2]["bonus"]
                print(f"object {o['k']} (type {o['type']}, model {o['model']}, alive {o['alive']}) centre "
                      f"({cx},{cz}) half {half}: P3 at ({tx},{tz}) hits {d}", flush=True)
                for k in range(60):
                    word = pad(d) if k % 4 == 0 else (pad("SQUARE") if k % 4 == 1 else 0xFFFF)
                    p3(f"seat {word:04X}"); c.wait_frames(6)
                    if recs(3)[2]["bonus"] > b0:
                        break
                p3("seat FFFF")
                now = objects()[o["k"]]
                print("   object alive now", now["alive"], "| P3 bonus", recs(3)[2]["bonus"], flush=True)
                if recs(3)[2]["bonus"] > b0:
                    got = True
                    c.wait_frames(20)
                    c.shot(str(out / "p3_broke_object.png"))
                    break
            if got:
                break
        state("after P3's hits")
        if not got:
            print("P3 did not get a bonus unit", flush=True)
            return 1

        # extra-life check: P3's next life just above its score (test-only)
        sc = w(M_P3_SCORE)
        print("life:", p3(f"life {sc + 100}"), flush=True)
        c.wait_frames(4)
        state("P3 next life set to score + 100")

        # end the level (test-only, as tools/ff_end_waves.py) + autopilot
        wr(PAUSE, 1, "<B"); c.wait_frames(2)
        sp = w(SCRIPT) & 0xFFFFFFFF
        term = next(sp + 2 * i for i in range(64) if struct.unpack("<h", c.read(sp + 2 * i, 2))[0] == -1)
        wr(SCRIPT, term)
        wr(WAVE_LEFT, sum(1 for r in recs()[3:] if r["t"] not in (-1, 0x11, 0x33) and r["hp"] > 0))
        wr(WAVE_X, 0)
        wr(PAUSE, 0, "<B")
        est = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}

        def send(i, word):
            if i == 2:
                p3(f"seat {word:04X}")
            elif i == 1:
                c.cmd("set_input", buttons=word, port=2, connected=1)
            else:
                c.cmd("set_input", buttons=word)

        f0 = c.frame(); tick = 0
        while w(G_DONE) <= 0 and c.frame() - f0 < 20000:
            s = recs()
            ens = [r for r in s[3:] if r["t"] not in (-1, 0x11) and r["hp"] > 0]
            for i in range(3):
                me = s[i]
                if me["t"] == -1 or me["hp"] <= 0:
                    continue
                if not ens:
                    send(i, pad("UP") if tick % 3 else 0xFFFF)
                    continue
                e = min(ens, key=lambda r: max(abs(r["x"] - me["x"]), abs(r["z"] - me["z"])))
                d = max(est, key=lambda k: (est[k][0] * (e["x"] - me["x"]) + est[k][1] * (e["z"] - me["z"])))
                near = max(abs(e["x"] - me["x"]), abs(e["z"] - me["z"])) <= 140
                send(i, pad(d) if (not near or tick % 3 == 0) else (pad("SQUARE") if tick % 3 == 1 else 0xFFFF))
            tick += 1
            c.wait_frames(4)
        for i in range(3):
            send(i, 0xFFFF)
        p3("seat off")
        state("level done")
        # follow the tally (it runs while the done counter is past 0x5A)
        shots = 0
        while 0 < w(G_DONE) and w(G_LEVEL) == 0:
            c.wait_frames(20)
            d = w(G_DONE)
            if d > 0x5A and shots < 3 and d % 40 < 20:
                c.shot(str(out / f"tally_{shots}.png")); shots += 1
                state("tally")
        state("after tally")
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
