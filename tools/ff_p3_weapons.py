#!/usr/bin/env python3
"""TEST ONLY: play levels 0..N with three players on autopilot and count the
enemy weapon / special attacks (FUN_8004930c / FUN_8004db94, wrapped by the
plugin so they also test P3) and P3's hits. Each level: fight for --fight
frames, then end the wave script (test-only debug writes, as
tools/ff_end_waves.py) and walk on until the next level.

    python3 tools/ff_p3_weapons.py --exe build-net/FightingForce_Recompiled --out DIR [--levels 5]
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

R = 0x800B4318
G_LEVEL, G_DONE, G_LIVES, G_GAME_OVER = 0x80093D58, 0x800941B8, 0x80093D50, 0x80093DD4
PAUSE, SCRIPT, WAVE_LEFT, WAVE_X = 0x80093DD9, 0x800946B8, 0x80093DBC, 0x80093DC0
WATCH = {0x0004930C: "enemy weapon", 0x0004DB94: "enemy special"}  # physical, as func_override lists them
EST = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--levels", type=int, default=5)
    ap.add_argument("--fight", type=int, default=4000)
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

    def wr(a, v, f="<I"):
        c.cmd("write_bulk", addr=f"0x{a:08X}", hex=struct.pack(f, v).hex())

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def recs(n=8):
        b = c.read(R, n * 0xF4)
        for _ in range(8):
            b2 = c.read(R, n * 0xF4)
            if b2 == b:
                break
            b = b2
        g = lambda i, o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        return [dict(t=g(i, 0x34, "<h"), hp=g(i, 0x42, "<h"), x=g(i, 0x28, "<i") >> 16, z=g(i, 0x30, "<i") >> 16)
                for i in range(n)]

    def counts():
        res = {}
        for o in c.cmd("func_override").get("overrides", []):
            a = o.get("addr")
            a = int(a, 16) if isinstance(a, str) else int(a)
            a &= 0x1FFFFFFF
            if a in WATCH:
                res[WATCH[a]] = (o.get("calls"), o.get("guard_misses"))
        return res

    def p3_field(name):
        s = p3("show")
        return int(s.split(name + "=")[1].split()[0]) if name + "=" in s else -1

    def send(i, word):
        if i == 2:
            p3(f"seat {word:04X}")
        elif i == 1:
            c.cmd("set_input", buttons=word, port=2, connected=1)
        else:
            c.cmd("set_input", buttons=word)

    tick = [0]

    def autopilot_step(revive=True):
        s = recs()
        ens = [r for r in s[3:] if r["t"] not in (-1, 0x11) and r["hp"] > 0]
        for i in range(3):
            me = s[i]
            if me["t"] == -1 or me["hp"] <= 0:
                # downed: press X so the continue prompt brings it back
                send(i, pad("CROSS") if revive and tick[0] % 8 == 0 else 0xFFFF)
                continue
            if not ens:
                send(i, pad("UP") if tick[0] % 3 else 0xFFFF)
                continue
            e = min(ens, key=lambda r: max(abs(r["x"] - me["x"]), abs(r["z"] - me["z"])))
            d = max(EST, key=lambda k: EST[k][0] * (e["x"] - me["x"]) + EST[k][1] * (e["z"] - me["z"]))
            near = max(abs(e["x"] - me["x"]), abs(e["z"] - me["z"])) <= 140
            k = tick[0] % 3
            send(i, pad(d) if (not near or k == 0) else (pad("SQUARE") if k == 1 else 0xFFFF))
        tick[0] += 1
        c.wait_frames(4)

    def end_waves():
        wr(PAUSE, 1, "<B"); c.wait_frames(2)
        sp = w(SCRIPT) & 0xFFFFFFFF
        term = next(sp + 2 * i for i in range(256) if struct.unpack("<h", c.read(sp + 2 * i, 2))[0] == -1)
        wr(SCRIPT, term)
        wr(WAVE_LEFT, sum(1 for r in recs()[3:] if r["t"] not in (-1, 0x11, 0x33) and r["hp"] > 0))
        wr(WAVE_X, 0)
        wr(PAUSE, 0, "<B")

    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        while c.frame() < 4300:
            c.wait_frames(60)
        c.enter_select()                   # straight-to-select or classic menus
        p3(f"seat {pad('RIGHT'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(20)
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3(f"seat {pad('CROSS'):04X}"); c.wait_frames(4); p3("seat FFFF")
        c.wait_frames(3300)
        for n in range(args.levels):
            lv = w(G_LEVEL)
            if lv < 0 or w(G_GAME_OVER):
                print("stopped: level", lv, "game over", w(G_GAME_OVER), flush=True)
                break
            # keep everyone alive enough to fight a while (test only: lives)
            if w(G_LIVES) < 3:
                wr(G_LIVES, 9)
            f0 = c.frame(); h0 = p3_field("p3_hits")
            while c.frame() - f0 < args.fight and w(G_LEVEL) == lv:
                autopilot_step()
            print(f"level {lv}: fought {c.frame() - f0} frames | {counts()} | P3 hits {h0} -> "
                  f"{p3_field('p3_hits')} | weapon passes for P3 {p3_field('weapon_views')}, hit P3 "
                  f"{p3_field('weapon_p3')} | players {[(r['t'], r['hp']) for r in recs(3)]}", flush=True)
            c.shot(str(out / f"level{lv}_fight.png"))
            if w(G_LEVEL) != lv:
                continue
            end_waves()
            f0 = c.frame()
            while w(G_LEVEL) == lv and c.frame() - f0 < 20000 and not w(G_GAME_OVER):
                autopilot_step()
            print(f"  -> level {w(G_LEVEL)} after {c.frame() - f0} frames | {p3('show')[:140]}", flush=True)
            c.wait_frames(600)
        print("final:", counts(), "|", p3("show"), flush=True)
        print([(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]], flush=True)
        return 0
    finally:
        for i in range(3):
            try:
                send(i, 0xFFFF)
            except Exception:  # noqa: BLE001
                pass
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
