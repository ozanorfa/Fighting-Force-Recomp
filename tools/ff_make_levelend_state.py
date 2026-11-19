#!/usr/bin/env python3
"""TEST ONLY: make a savestate at the end of level 0 for the netplay
level-change test (tools/ff_net_local.py --load-slot).

Starts a netplay build OFFLINE with game_3p.toml (the third-player mod must be
enabled in <build>/mods/state.toml), picks Hawk / Mace / Smasher (P3 through
the ff.p3 seat test override), ends level 0's wave script with debug writes
(as tools/ff_end_waves.py), lets the autopilot clear the rest and walk to the
exit, and saves slot SLOT the moment the level-done flag is set. The slot file
lands in OUT/cards0, the host's writable-state directory in ff_net_local.py.

    python3 tools/ff_make_levelend_state.py --exe build-net/FightingForce_Recompiled --out DIR
"""
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
G_LEVEL, G_DONE = 0x80093D58, 0x800941B8
PAUSE, SCRIPT, WAVE_LEFT, WAVE_X = 0x80093DD9, 0x800946B8, 0x80093DBC, 0x80093DC0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--port", type=int, default=4370)
    ap.add_argument("--level", type=int, default=0,
                    help="start at this level id (the game's level-select cheat, test-only writes)")
    ap.add_argument("--title-frame", type=int, default=4300)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    cards = out / "cards0"
    cards.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    log = open(out / "make_state.log", "w")
    proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(cards), "--debug-port", str(args.port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    c = Client(port=args.port).connect(wait_s=90)

    def w(a):
        return struct.unpack("<i", c.read(a, 4))[0]

    def wr(a, v, n=4):
        c.cmd("write_bulk", addr=f"0x{a:08X}",
              hex=struct.pack("<I" if n == 4 else "<B", v & (0xFFFFFFFF if n == 4 else 0xFF)).hex())

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply")

    def p3_tap(name):
        p3(f"seat {pad(name):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(20)

    def recs(n=8):
        b = c.read(R, n * 0xF4)
        for _ in range(8):
            b2 = c.read(R, n * 0xF4)
            if b2 == b:
                break
            b = b2
        return [dict(t=struct.unpack_from("<h", b, i * 0xF4 + 0x34)[0], hp=struct.unpack_from("<h", b, i * 0xF4 + 0x42)[0],
                     x=struct.unpack_from("<i", b, i * 0xF4 + 0x28)[0] >> 16,
                     z=struct.unpack_from("<i", b, i * 0xF4 + 0x30)[0] >> 16) for i in range(n)]

    try:
        if "active=" not in (p3("show") or ""):
            print("third-player mod not active offline: enable it in <build>/mods/state.toml")
            return 1
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        while c.frame() < args.title_frame:
            c.wait_frames(60)
        c.enter_select()                   # straight-to-select or classic menus
        if args.level:
            wr(0x80093D68, w(0x80093D68) | 2)
            wr(0x800927B4, args.level)
        p3_tap("RIGHT")                                  # P3: Alana -> Smasher
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3_tap("CROSS")
        f0 = c.frame()
        while c.frame() - f0 < 6000:
            s = p3("show")
            if "active=1" in s and int(s.split("passes=")[1].split()[0]) > 0:
                break
            c.wait_frames(30)
        c.wait_frames(60)
        print("level", w(G_LEVEL), "players", recs(3), "|", p3("show"), flush=True)
        c.shot(str(out / "state_level0.png"))

        # end the wave script (test-only debug writes; see tools/ff_end_waves.py)
        wr(PAUSE, 1, 1)
        c.wait_frames(2)
        sp = w(SCRIPT) & 0xFFFFFFFF
        term = next(sp + 2 * i for i in range(64) if struct.unpack("<h", c.read(sp + 2 * i, 2))[0] == -1)
        wr(SCRIPT, term)
        live = sum(1 for r in recs()[3:] if r["t"] not in (-1, 0x11, 0x33) and r["hp"] > 0)
        wr(WAVE_LEFT, live)
        wr(WAVE_X, 0)
        wr(PAUSE, 0, 1)
        print("waves ended; enemies alive", live, flush=True)

        # autopilot (P1 port 1, P2 port 2, P3 seat override) until level done
        est = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}

        def dirto(p, e):
            dx, dz = e["x"] - p["x"], e["z"] - p["z"]
            return max(est, key=lambda d: (est[d][0] * dx + est[d][1] * dz) / math.hypot(*est[d]))

        def send(i, word):
            if i == 2:
                p3(f"seat {word:04X}")
            elif i == 1:
                c.cmd("set_input", buttons=word, port=2, connected=1)
            else:
                c.cmd("set_input", buttons=word)

        f0 = c.frame(); tick = 0
        while w(G_DONE) <= 0:
            if c.frame() - f0 > 20000:
                print("no level end after 20000 frames", recs(3), flush=True)
                return 1
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
                near = max(abs(e["x"] - me["x"]), abs(e["z"] - me["z"])) <= 140
                send(i, pad(dirto(me, e)) if (not near or tick % 3 == 0) else (pad("SQUARE") if tick % 3 == 1 else 0xFFFF))
            tick += 1
            c.wait_frames(4)
        for i in range(3):
            send(i, 0xFFFF)
        p3("seat off")
        c.cmd("clear_input", port=1)
        print("level done flag", w(G_DONE), "at frame", c.frame(), "players", recs(3), flush=True)
        r = c.cmd("savestate", op="save", slot=args.slot)
        print("save:", r, flush=True)
        c.wait_frames(30)
        print("status:", c.cmd("savestate_status"), flush=True)
        c.shot(str(out / "state_saved.png"))
        print("slot files:", sorted(p.name for p in cards.iterdir()), flush=True)
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
