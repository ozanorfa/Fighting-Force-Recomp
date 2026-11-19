#!/usr/bin/env python3
"""TEST ONLY: gun shots, thrown objects and the friendly-fire option.

Starts the game offline (debug server), level 0, P1 Hawk / P2 Mace / P3
Smasher (P3 through the ff.p3 seat test override). The shooter (--shooter 1, 2
or 3) is placed on the pistol that lies in level 0 and picks it up (CROSS
next to an item).
Each try: the shooter's ammo is topped up (test-only write), a friend is
placed in the line of fire close in front, one enemy further along the same
line (test-only writes; the other enemies are emptied and the wave script is
held, as in ff_p3_ff_moves.py), then the shooter fires once (CROSS with a gun
in hand, player update FUN_80024008 -> FUN_80030440 -> shot FUN_8002fd90).

With friendly fire on (the original game) the shot stops at the first record
it reaches: the friend is hit, the enemy behind is not, between any two of
the three players, P3 included. With it off the shot
passes through the friend and hits the enemy. A friend counts as hit when
the plugin's player-to-player damage counter ("pvp" in ff.p3 show) rises.
The plugin's debug trace ("ff.p3 fftrace on") logs each step of the shot
with every record; a try counts only when the shot was fired, one step
passed clearly inside the lookup's reach (< 0x30 of 0x40) of the friend or
the lookup found it (the shooter can turn a little while firing), and the friend before the shot and
the enemy at the shot stood in states the lookup does not skip.

--weapon throw: the shooter picks up the heavy object of level 0 (kind
0x20E; CROSS throws it, state 0xA1) instead and throws it; the try counts
when one of the thrown-object hit tests (FUN_8001bfa0, trace "ffcall")
came near the friend. A thrown object flies on after a hit, so with friendly
fire on the friend is hit (the enemy behind may be too); it hits P3 like the
others (the plugin tests P3 as a player there). The object can be thrown
once (it cannot be picked up again here): one try per run, --friend picks who.

    python3 tools/ff_ff_shots.py --exe RUN/FightingForce_Recompiled --out DIR [--shooter 1] [--tries 4]
        [--weapon gun|throw] [--friend N]
        [--expect on|off]   exit 1 unless every counted try matched that setting
"""
from __future__ import annotations

import argparse
import math
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
ITEMS = 0x8009A3C8                 # objects, 0x24 each: +0 kind, +4 x, +0xC z, +0x16 s16 ammo
SIN, COS = 0x800802D0, 0x800806D0  # the shot steps (sin, -cos) of the angle at record +0x38
GUNS = set(range(499, 504)) | {0x1F8, 0x1F9, 0x1FA, 0x203}
TYPES = {0: 0, 1: 1, 2: 2}         # player -> fighter type (Hawk, Mace, Smasher)
DOWN_STATES = {0x93, 0x94, 0x9A, 0xBF}
# States the shot lookup FUN_80019ba8 skips (FUN_80048cfc(state) != 0, Ghidra).
SKIPPED = ({0x93, 0x9A, 0x95, 0x96, 0x29, 0x84, 0x82, 0x83, 0x4D, 0xA7, 0xA9, 0x21, 0x94, 0xCD, 0xCB, 0xAC, 0x24}
           | set(range(0xB1, 0xBE)))


def game_dist(ax: int, az: int, bx: int, bz: int) -> int:
    dx, dz = abs(ax - bx), abs(az - bz)
    return max(dx, dz) + min(dx, dz) // 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4391)
    ap.add_argument("--shooter", type=int, default=1, choices=(1, 2, 3))
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--expect", choices=("on", "off"), default="")
    ap.add_argument("--weapon", choices=("gun", "throw"), default="gun")
    ap.add_argument("--friend", type=int, default=0, choices=(0, 1, 2, 3), help="only this friend (0 = both)")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    log = open(out / "run.log", "w")
    proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(out / "cards"), "--debug-port", str(args.port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    c = Client(port=args.port).connect(wait_s=90)
    s = args.shooter - 1

    def wr(a, data: bytes):
        c.cmd("write_bulk", addr=f"0x{a:08X}", hex=data.hex())

    def p3(cmd):
        return c.cmd("mod_cmd", name="ff.p3", args=cmd).get("reply") or ""

    def qs(cmd):
        return c.cmd("mod_cmd", name="ff.qs", args=cmd).get("reply") or ""

    def counter(name):
        m = re.search(rf"\b{name}=(\d+)", p3("show"))
        return int(m.group(1)) if m else -1

    def raw():
        b = c.read(R, 8 * 0xF4)
        g = lambda i, o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        return [dict(i=i, t=g(i, 0x34, "<h"), hp=g(i, 0x42, "<h"), x=g(i, 0x28, "<i") >> 16,
                     y=g(i, 0x2C, "<i") >> 16, z=g(i, 0x30, "<i") >> 16, st=g(i, 0x0C, "<I"),
                     inv=g(i, 0x50, "<h"), ang=g(i, 0x38, "<h"), hand=g(i, 0x7E, "<b")) for i in range(8)]

    def players():
        for _ in range(20):
            b = raw()
            found = {r["t"]: r for r in b[:3]}
            if all(TYPES[p] in found for p in TYPES):
                return {p: dict(found[TYPES[p]], p=p) for p in TYPES}, b
            c.wait_frames(1)
        raise RuntimeError("no stable read of the players")

    def send(p, word):
        if p == 2:
            p3(f"seat {word:04X}")
        elif p == 1:
            c.cmd("set_input", buttons=word, port=2, connected=1)
        else:
            c.cmd("set_input", buttons=word)

    def press(p, name, frames=5, after=40):
        send(p, pad(name)); c.wait_frames(frames); send(p, 0xFFFF); c.wait_frames(after)

    def place(p, x, z):
        """The plugin keeps one place request and applies it in the next frame: wait for it."""
        p3(f"place {p} {int(x)} {int(z)}")
        c.wait_frames(2)

    keep = {}

    def clear_enemies(recs):
        wr(0x80093DD9, b"\x01")
        for r in recs[3:]:
            if r["t"] == -1:
                continue
            if keep.get("i") in (None, r["i"]) and r["t"] not in (0x11, 0x33) and r["hp"] > 0:
                keep["i"] = r["i"]
                if r["hp"] < 300:
                    wr(R + r["i"] * 0xF4 + 0x42, struct.pack("<h", 400))
                continue
            wr(R + r["i"] * 0xF4 + 0x34, b"\xff\xff")

    def enemy():
        return raw()[keep["i"]] if "i" in keep else None

    def place_enemy(x, z):
        for off, v in ((0x28, x), (0x30, z)):
            wr(R + keep["i"] * 0xF4 + off, struct.pack("<i", int(v) << 16))

    def wait_standing(p, limit=240):
        f0 = c.frame()
        while c.frame() - f0 < limit:
            pl, _ = players()
            if pl[p]["st"] not in DOWN_STATES and pl[p]["y"] >= 0 and pl[p]["inv"] == 0:
                return True
            c.wait_frames(6)
        return False

    def hittable(r):
        return r["st"] not in SKIPPED and r["inv"] == 0 and r["y"] >= 0 and r["hp"] > 0

    def wait_hittable(limit=300):
        """The friend and the kept enemy in states the shot lookup does not skip."""
        f0 = c.frame()
        while c.frame() - f0 < limit:
            pl, recs = players()
            if hittable(pl[f]) and hittable(recs[keep["i"]]):
                return True
            c.wait_frames(6)
        return False

    def top_up():
        for p in TYPES:
            pl, _ = players()
            if pl[p]["hp"] < 300:
                p3(f"hp {p} 380")
        c.wait_frames(2)

    def direction(ang):
        i = ang & 0x7FF
        sn = struct.unpack("<h", c.read(SIN + 2 * i, 2))[0]
        cs = struct.unpack("<h", c.read(COS + 2 * i, 2))[0]
        n = math.hypot(sn, cs) or 1.0
        return sn / n, -cs / n

    try:
        p3("seat FFFF")
        c.hold(0xFFFF, port=2)
        while c.frame() < 1400:
            c.wait_frames(30)
        c.enter_select()
        p3(f"seat {pad('RIGHT'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(16)  # Alana -> Smasher
        c.tap("CROSS", frames=6, gap=60)
        c.tap("CROSS", frames=6, gap=30, port=2)
        p3(f"seat {pad('CROSS'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(16)
        f0 = c.frame()
        while c.frame() - f0 < 6000:
            st = p3("show")
            if "active=1" in st and int(st.split("passes=")[1].split()[0]) > 0:
                break
            c.wait_frames(30)
        c.wait_frames(120)
        ff = re.search(r"friendly_fire=(\d)", qs("show"))
        print("friendly_fire", ff.group(1) if ff else "?", flush=True)
        f0 = c.frame()
        while c.frame() - f0 < 3000:
            _, recs = players()
            if any(r["t"] not in (-1, 0x11, 0x33) and r["hp"] > 0 for r in recs[3:]):
                break
            c.wait_frames(30)
        _, recs = players()
        clear_enemies(recs)
        if "i" not in keep:
            print("no enemy came", flush=True)
            return 2
        gun_mode = args.weapon == "gun"
        kinds = GUNS if gun_mode else {0x20E}
        items = c.read(ITEMS, 64 * 0x24)
        gun = next((k for k in range(64) if struct.unpack_from("<i", items, k * 0x24)[0] in kinds), None)
        if gun is None:
            print(f"no {args.weapon} item in this level", flush=True)
            return 2
        print(f"{args.weapon} item {gun} kind {struct.unpack_from('<i', items, gun * 0x24)[0]}; "
              f"shooter P{s + 1}, enemy record {keep['i']}", flush=True)
        friends = [p for p in TYPES if p != s]
        tested = [p for p in friends if not args.friend or p == args.friend - 1]

        def pick_up():
            """Shooter on the item, CROSS (picks up an item in reach); True when it holds it."""
            for _ in range(4):
                pl, _ = players()
                if pl[s]["hand"] == gun:
                    return True
                it = c.read(ITEMS + gun * 0x24, 0x10)
                gx, gz = struct.unpack_from("<i", it, 4)[0] >> 16, struct.unpack_from("<i", it, 0xC)[0] >> 16
                for k, p in enumerate(friends):
                    place(p, gx - 400, gz + 300 * (k * 2 - 1))
                place_enemy(gx + 500, gz + 500)
                place(s, gx, gz); c.wait_frames(20)
                wait_standing(s)
                press(s, "CROSS", 5, 50)
            return players()[0][s]["hand"] == gun

        if not pick_up():
            print("the shooter did not pick the item up", flush=True)
            c.shot(str(out / "no_pickup.png"))
            return 2
        hand = gun
        print(f"P{s + 1} holds item {hand}", flush=True)
        p3("fftrace on")
        logf = out / "run.log"
        step_rx = re.compile(r"shotfind owner=-?\d+ pt=\((-?\d+),(-?\d+)\)" if gun_mode
                             else r"ffcall view=\d atk=\d pt=\((-?\d+),(-?\d+)\)")
        near, far = (110, 260) if gun_mode else (90, 190)
        results = []
        for f in tested:
            other = [p for p in friends if p != f][0]
            for k in range(args.tries):
                if gun_mode:
                    wr(ITEMS + hand * 0x24 + 0x16, struct.pack("<h", 5))
                elif not pick_up():
                    print("the shooter could not pick the item up again", flush=True)
                    break
                top_up()
                wait_standing(s)
                pl, recs = players()
                clear_enemies(recs)
                dx, dz = direction(pl[s]["ang"])
                sx, sz = pl[s]["x"], pl[s]["z"]
                place(f, sx + near * dx, sz + near * dz)
                place(other, sx - 300 * dz, sz + 300 * dx)      # beside the shooter, out of the line
                place_enemy(sx + far * dx, sz + far * dz)
                c.wait_frames(3)
                ready = wait_hittable()
                e0 = enemy()["hp"]
                pvp0, shot0 = counter("pvp"), counter("pshot")
                pl, _ = players()
                fpos, holds = pl[f], pl[s]["hand"] == hand
                n0 = logf.stat().st_size
                press(s, "CROSS", 5, 45 if gun_mode else 70)
                with open(logf, "rb") as lf:
                    lf.seek(n0)
                    text = lf.read().decode("latin1")
                steps = [(int(a), int(b)) for a, b in step_rx.findall(text)]
                if gun_mode:
                    # The friend's record in the lookup (P3 shooting: its own pass, P1 in 0, P2 in 2), its
                    # position at each step from the trace; clearly inside the lookup's reach (< 0x40).
                    fr = f if s != 2 else (0 if f == 0 else 2)
                    rx = re.compile(r"shotfind owner=-?\d+ pt=\((-?\d+),(-?\d+)\).*? r" + str(fr)
                                    + r"\[t-?\d+ hp-?\d+ x(-?\d+) z(-?\d+) ")
                    in_line = any(game_dist(int(a), int(b), int(x), int(z)) < 0x30
                                  for a, b, x, z in rx.findall(text)) \
                        or str(fr) in re.findall(r"shotfind owner=-?\d+ pt=\(-?\d+,-?\d+\) ->(-?\d+)", text)
                else:                                  # a thrown object hit P2 at 80
                    in_line = any(game_dist(x, z, fpos["x"], fpos["z"]) < 100 for x, z in steps)
                if gun_mode and ready:                 # the enemy's state at the shot, from the trace
                    last = [m for m in re.findall(rf"r{keep['i']}\[t-?\d+ hp-?\d+ x-?\d+ z-?\d+ st([0-9A-F]+)\]",
                                                  text)]
                    ready = not last or int(last[-1], 16) not in SKIPPED
                e1 = enemy()["hp"]
                pvp1, shot1 = counter("pvp"), counter("pshot")
                ammo = struct.unpack("<h", c.read(ITEMS + hand * 0x24 + 0x16, 2))[0]
                gone = players()[0][s]["hand"] != hand
                friend_hit, enemy_hit = pvp1 > pvp0, e1 < e0
                fired_now = holds and (ammo < 5 if gun_mode else gone)
                why = ("" if fired_now else ", no shot") + ("" if in_line else ", friend not in the line") \
                    + ("" if ready else ", friend or enemy not hittable")
                results.append((f, friend_hit, enemy_hit, not why))
                detail = f"ammo 5 -> {ammo}, shot lookups +{shot1 - shot0}" if gun_mode else f"hit tests {len(steps)}"
                print(f"P{s + 1} -> friend P{f + 1}, enemy behind: friend {'HIT' if friend_hit else '-'}  "
                      f"enemy {'HIT' if enemy_hit else '-'}  ({detail}{why + ': not counted' if why else ''})",
                      flush=True)
                if k == 0:
                    c.shot(str(out / f"shot_P{s + 1}_P{f + 1}.png"))
        print(p3("show"), flush=True)
        misses = [(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]
                  if o["guard_misses"]]
        print("guard misses", misses, flush=True)
        fired = [r for r in results if r[3]]
        print(f"shots fired with the friend in the line {len(fired)}/{len(results)}", flush=True)
        if args.expect:
            # On: the shot stops at the friend (every pair of players, as P1 <-> P2 in the
            # original game); off: it flies through the friend into the enemy.
            want = (True, False) if args.expect == "on" else (False, True)
            checked = fired
            if gun_mode or args.expect == "off":
                bad = [r for r in checked if (r[1], r[2]) != want]
            else:
                bad = [r for r in checked if not r[1]]          # thrown, on: the friend is hit
            ok = checked and not bad and not misses
            print("PASS" if ok else f"FAIL {bad}", flush=True)
            return 0 if ok else 1
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
