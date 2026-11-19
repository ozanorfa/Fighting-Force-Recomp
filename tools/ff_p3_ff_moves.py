#!/usr/bin/env python3
"""TEST ONLY: friendly fire between the three players, move by move.

Starts the game offline (debug server), level 0, P1 Hawk / P2 Mace / P3
Smasher (P3 through the ff.p3 seat test override). Test-only writes keep the
fight to the players: the wave script stays paused (DAT_80093dd9) and any
enemy record (3..7) is emptied; players' health is topped up through the
plugin's "ff.p3 hp" command. For every attacker -> target pair (P1 <-> P2 is
the original game's own case) and every move, the attacker walks up to the
idle target, faces it and does the move; a try counts as a hit when the
target's health drops. Records are found by fighter type, so a read that
lands inside P3's pass (records swapped) does not mix players up.

    python3 tools/ff_p3_ff_moves.py --exe build-net/FightingForce_Recompiled --out DIR [--tries 6]
"""
from __future__ import annotations

import argparse
import math
import re
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

R = 0x800B4318
TYPES = {0: 0, 1: 1, 2: 2}          # player -> fighter type (Hawk, Mace, Smasher)
EST = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}
DOWN_STATES = {0x93, 0x94, 0x9A, 0xBF}
MOVES = {
    "punch":       [("SQUARE", 5)],
    "kick":        [("TRIANGLE", 5)],
    "circle":      [("CIRCLE", 5)],
    "cross":       [("CROSS", 5)],
    "jump+punch":  [("CROSS", 4), (None, 6), ("SQUARE", 5)],
    "jump+kick":   [("CROSS", 4), (None, 6), ("TRIANGLE", 5)],
    "punch x3":    [("SQUARE", 4), (None, 6), ("SQUARE", 4), (None, 6), ("SQUARE", 4)],
    "punch+kick":  [(("SQUARE", "TRIANGLE"), 6)],
}
PAIRS = [(0, 1), (1, 0), (2, 0), (2, 1), (0, 2), (1, 2)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4390)
    ap.add_argument("--tries", type=int, default=6)
    ap.add_argument("--moves", default=",".join(MOVES))
    ap.add_argument("--pairs", default="")
    ap.add_argument("--trace", action="store_true", help="plugin logs each second hit test (debug)")
    ap.add_argument("--crowd", action="store_true", help="an enemy stands on the target's spot too")
    ap.add_argument("--min-hits", type=int, default=0,
                    help="exit 1 when any pair of any move hit fewer times (0 = report only)")
    ap.add_argument("--max-hits", type=int, default=-1,
                    help="exit 1 when any pair of any move did player-to-player damage more times (friendly fire off: 0)")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    log = open(out / "run.log", "w")
    proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(out / "cards"), "--debug-port", str(args.port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    c = Client(port=args.port).connect(wait_s=90)
    pairs = PAIRS if not args.pairs else [tuple(int(x) - 1 for x in p.split(">")) for p in args.pairs.split(",")]

    def w(a):
        return struct.unpack("<i", c.read(a, 4))[0]

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def raw():
        b = c.read(R, 8 * 0xF4)
        g = lambda i, o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        return [dict(i=i, t=g(i, 0x34, "<h"), hp=g(i, 0x42, "<h"), x=g(i, 0x28, "<i") >> 16,
                     y=g(i, 0x2C, "<i") >> 16, z=g(i, 0x30, "<i") >> 16, st=g(i, 0x0C, "<I"),
                     inv=g(i, 0x50, "<h"), mask=g(i, 0x52, "<H"), atk=g(i, 0x81, "<b")) for i in range(8)]

    def players():
        """{player: record dict} from records 0..2, found by fighter type."""
        for _ in range(20):
            b = raw()
            found = {r["t"]: r for r in b[:3]}
            if all(TYPES[p] in found for p in TYPES):
                return {p: dict(found[TYPES[p]], p=p) for p in TYPES}, b
            c.wait_frames(1)
        raise RuntimeError(f"no stable read of the players: {[(r['i'], r['t'], r['hp']) for r in raw()[:4]]} level {w(0x80093D58)} | {p3('show')[:90]}")

    def send(p, word):
        if p == 2:
            p3(f"seat {word:04X}")
        elif p == 1:
            c.cmd("set_input", buttons=word, port=2, connected=1)
        else:
            c.cmd("set_input", buttons=word)

    def dist(a, b):
        dx, dz = abs(a["x"] - b["x"]), abs(a["z"] - b["z"])
        return max(dx, dz) + min(dx, dz) // 2

    vecs = {}   # player -> {direction(s): unit world vector}, measured in place (the camera turns)

    def calibrate(a):
        v = {}
        for d in ("RIGHT", "LEFT", "UP", "DOWN"):
            p0, _ = players()
            send(a, pad(d)); c.wait_frames(6); send(a, 0xFFFF); c.wait_frames(4)
            p1, _ = players()
            dx, dz = p1[a]["x"] - p0[a]["x"], p1[a]["z"] - p0[a]["z"]
            n = math.hypot(dx, dz)
            v[(d,)] = (dx / n, dz / n) if n > 0 else EST[d]
        for h in ("RIGHT", "LEFT"):
            for u in ("UP", "DOWN"):
                x, z = v[(h,)][0] + v[(u,)][0], v[(h,)][1] + v[(u,)][1]
                n = math.hypot(x, z) or 1.0
                v[(h, u)] = (x / n, z / n)
        vecs[a] = v

    def dirto(a, b):
        dx, dz = b["x"] - a["x"], b["z"] - a["z"]
        v = vecs.get(a["p"]) or {(d,): (EST[d][0] / math.hypot(*EST[d]), EST[d][1] / math.hypot(*EST[d])) for d in EST}
        best = max(v, key=lambda k: v[k][0] * dx + v[k][1] * dz)
        return best

    keep = {}

    def clear_enemies(recs):
        c.cmd("write_bulk", addr="0x80093DD9", hex="01")
        for r in recs[3:]:
            if r["t"] == -1:
                continue
            if args.crowd and keep.get("i") in (None, r["i"]) and r["t"] not in (0x11, 0x33) and r["hp"] > 0:
                keep["i"] = r["i"]
                if r["hp"] < 200:
                    c.cmd("write_bulk", addr=f"0x{R + r['i'] * 0xF4 + 0x42:08X}", hex=struct.pack("<h", 400).hex())
                continue
            c.cmd("write_bulk", addr=f"0x{R + r['i'] * 0xF4 + 0x34:08X}", hex="ffff")

    def enemy_hp():
        if "i" not in keep:
            return None
        return struct.unpack("<h", c.read(R + keep["i"] * 0xF4 + 0x42, 2))[0]

    def place_enemy(x, z):
        if "i" in keep:
            for off, v in ((0x28, x), (0x30, z)):
                c.cmd("write_bulk", addr=f"0x{R + keep['i'] * 0xF4 + off:08X}", hex=struct.pack("<i", int(v) << 16).hex())

    def top_up():
        for p in TYPES:
            pl, _ = players()
            if pl[p]["hp"] < 300:
                rec_index = {0: 0, 1: 1, 2: 2}[p]
                p3(f"hp {rec_index} 380")
        c.wait_frames(2)

    def wait_standing(p, limit=240):
        f0 = c.frame()
        while c.frame() - f0 < limit:
            pl, _ = players()
            if pl[p]["st"] not in DOWN_STATES and pl[p]["y"] >= 0 and pl[p]["inv"] == 0:
                return True
            c.wait_frames(6)
        return False

    def walk_to(a, t, near=70):
        for _ in range(80):
            pl, recs = players()
            clear_enemies(recs)
            if dist(pl[a], pl[t]) < near:
                break
            send(a, pad(*dirto(pl[a], pl[t]))); c.wait_frames(5)
        send(a, 0xFFFF); c.wait_frames(3)

    def clear_bystander(a, t):
        b = 3 - a - t
        for _ in range(12):
            pl, _ = players()
            if min(dist(pl[b], pl[a]), dist(pl[b], pl[t])) > 260:
                break
            mid = dict(x=(pl[a]["x"] + pl[t]["x"]) // 2, z=(pl[a]["z"] + pl[t]["z"]) // 2)
            away = dict(x=2 * pl[b]["x"] - mid["x"], z=2 * pl[b]["z"] - mid["z"])
            send(b, pad(*dirto(pl[b], away))); c.wait_frames(8)
        send(b, 0xFFFF)

    def place(p, x, z):
        p3(f"place {p} {int(x)} {int(z)}")      # record p = player p outside P3's pass

    logf = out / "run.log"
    ATK = {0: (0, 0), 1: (0, 1), 2: (1, 1)}       # player -> (view, slot) in the hit test
    rx = re.compile(r"ffcall view=(\d) atk=(\d) pt=\((-?\d+),(-?\d+)\)")

    def probe(a, seq):
        """Swing at nothing; return the first attack point relative to the attacker."""
        pl, _ = players()
        pos0 = pl[a]
        n0 = logf.stat().st_size
        do_move(a, seq)
        c.wait_frames(24)
        with open(logf, "rb") as f:
            f.seek(n0)
            text = f.read().decode("latin1")
        for m in rx.finditer(text):
            if (int(m.group(1)), int(m.group(2))) == ATK[a]:
                return int(m.group(3)) - pos0["x"], int(m.group(4)) - pos0["z"]
        return None

    def do_move(a, seq):
        for btn, frames in seq:
            if btn is None:
                send(a, 0xFFFF)
            elif isinstance(btn, tuple):
                send(a, pad(*btn))
            else:
                send(a, pad(btn))
            c.wait_frames(frames)
        send(a, 0xFFFF)

    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        # wait for the title instead of a fixed frame (loading speed varies with the host)
        f0 = c.frame()
        while c.frame() < 1400:
            c.wait_frames(30)
        c.enter_select()                   # straight-to-select or classic menus
        p3(f"seat {pad('RIGHT'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(16)  # Alana -> Smasher
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3(f"seat {pad('CROSS'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(16)
        f0 = c.frame()
        while c.frame() - f0 < 6000:
            s = p3("show")
            if "active=1" in s and int(s.split("passes=")[1].split()[0]) > 0:
                break
            c.wait_frames(30)
        c.wait_frames(120)
        if args.crowd:
            f0 = c.frame()
            while c.frame() - f0 < 3000:
                pl, recs = players()
                if any(r["t"] not in (-1, 0x11, 0x33) and r["hp"] > 0 for r in recs[3:]):
                    break
                c.wait_frames(30)
        pl, recs = players()
        clear_enemies(recs)
        print("level", w(0x80093D58), "kept enemy", keep, "friendly-fire flag", w(0x80093D44), "players",
              {p: (r["i"], r["t"], r["hp"]) for p, r in pl.items()}, flush=True)
        c.shot(str(out / "start.png"))
        print(p3("fftrace on"), flush=True)         # the probe swing reads the attack point from it
        table = {}
        pvp_table = {}
        enemy_notes = []
        for name in args.moves.split(","):
            seq = MOVES[name]
            for a, t in pairs:
                hits, notes, pvp_hits = 0, [], 0
                for k in range(args.tries):
                    top_up()
                    wait_standing(t)
                    wait_standing(a)
                    pl, recs = players()
                    clear_enemies(recs)
                    b = 3 - a - t
                    base = pl[a]
                    place(t, base["x"] + 420, base["z"])          # out of reach for the probe
                    place(b, base["x"] - 700, base["z"])          # bystander away
                    c.wait_frames(4)
                    off = probe(a, seq)
                    if off is None:
                        notes.append("?")
                        continue
                    c.wait_frames(30)
                    pl, _ = players()
                    place(t, pl[a]["x"] + off[0], pl[a]["z"] + off[1])   # the target where the swing lands
                    c.wait_frames(4)
                    wait_standing(t)
                    if args.crowd:
                        place_enemy(pl[a]["x"] + off[0], pl[a]["z"] + off[1] + 10)
                        c.wait_frames(1)
                    e0 = enemy_hp()
                    pl, _ = players()
                    before = pl[t]["hp"]
                    show0 = p3("show")
                    ff0 = int(show0.split("ff_hits=")[1].split()[0])
                    pv0 = int(show0.split("pvp=")[1].split()[0]) if "pvp=" in show0 else 0
                    do_move(a, seq)
                    c.wait_frames(30)
                    pl, _ = players()
                    show1 = p3("show")
                    ff1 = int(show1.split("ff_hits=")[1].split()[0])
                    pv1 = int(show1.split("pvp=")[1].split()[0]) if "pvp=" in show1 else 0
                    pvp_hits += pv1 > pv0       # damage by a player to a player (the enemy may hit the target too)
                    hit = pl[t]["hp"] < before
                    hits += hit
                    e1 = enemy_hp()
                    enemy_hit = e0 is not None and e1 is not None and e1 < e0
                    notes.append(("H" if hit else "-") + ("e" if enemy_hit else "") + ("" if ff1 == ff0 else f"+{ff1 - ff0}")
                                 + ("P" if pv1 > pv0 else ""))
                    enemy_notes.append(notes[-1])
                table[(name, a, t)] = (hits, args.tries)
                pvp_table[(name, a, t)] = pvp_hits
                print(f"{name:11s} P{a + 1} -> P{t + 1}: {hits}/{args.tries}  {' '.join(notes)}", flush=True)
        print("SUMMARY (hits / tries)", flush=True)
        names = args.moves.split(",")
        print(f"{'':11s} " + " ".join(f"P{a + 1}>P{t + 1}" for a, t in pairs), flush=True)
        for name in names:
            print(f"{name:11s} " + " ".join(f"{table[(name, a, t)][0]:>2d}/{table[(name, a, t)][1]:<2d}"
                                           for a, t in pairs), flush=True)
        print(p3("show"), flush=True)
        misses = [(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]
                  if o["guard_misses"]]
        print(misses, flush=True)
        low = [(k, v) for k, v in table.items() if v[0] < args.min_hits]
        if args.min_hits and low:
            print("BELOW --min-hits:", low, flush=True)
        # --max-hits counts player-to-player damage (plugin counter), not health drops: in --crowd the
        # enemy on the target's spot may hit the target.
        high = [(k, v) for k, v in pvp_table.items() if args.max_hits >= 0 and v > args.max_hits]
        if high:
            print("ABOVE --max-hits:", high, flush=True)
        if args.crowd and args.max_hits == 0 and not any("e" in n for n in enemy_notes):
            print("the enemy behind was never hit", flush=True)
            high.append("no enemy hit")
        return 1 if (args.min_hits and low) or high or misses else 0
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
