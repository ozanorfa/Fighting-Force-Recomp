#!/usr/bin/env python3
"""P3 combat test (seat 3 test override). Usage: python3 tools/ff_p3_combat.py OUTDIR [fight_frames]
1. P3 walks next to P1; P1 punches 15 times -> P3 must not take hits from record 0.
2. P3 fights the nearest enemy for fight_frames -> damage dealt / taken per hit.
Health deltas are read between frames; a read can land inside the P3 pass
(slot 1 then holds P3), so a sample whose slot-1 health equals P3's is not
trusted for P2."""
from pathlib import Path
import math
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

out = sys.argv[1]
fight = int(sys.argv[2]) if len(sys.argv) > 2 else 2400
c = Client()
R = 0x800B4318


def snap():
    # Two equal reads in a row: a read can land inside the P3 view (records
    # 1 and 2 swapped for P3's pass), which would show P2 in record 2.
    b = c.read(R, 8 * 0xF4)
    for _ in range(8):
        b2 = c.read(R, 8 * 0xF4)
        if b2 == b:
            break
        b = b2
    res = []
    for i in range(8):
        g = lambda o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        res.append(dict(i=i, t=g(0x34, "<h"), hp=g(0x42, "<h"), x=g(0x28, "<i") >> 16,
                        z=g(0x30, "<i") >> 16, st=g(0x0C, "<I"), k=g(0xA4, "<i"),
                        tg=g(0xA8, "<i"), atk=g(0x81, "<b"), lvl=g(0xB8, "<i")))
    return res


def seat(w):
    c.cmd("mod_cmd", name="ff.p3", args=f"seat {w:04X}")


def show():
    return c.cmd("mod_cmd", name="ff.p3", args="show")["reply"]


def dist(a, b):
    dx, dz = abs(a["x"] - b["x"]), abs(a["z"] - b["z"])
    return max(dx, dz) + min(dx, dz) // 2


est = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}


def dirto(p, e):
    dx, dz = e["x"] - p["x"], e["z"] - p["z"]
    return max(est, key=lambda d: (est[d][0] * dx + est[d][1] * dz) / max(1e-6, math.hypot(*est[d])))


def walk_to(idx, near=110, steps=80):
    for _ in range(steps):
        s = snap(); p = s[2]; e = s[idx]
        if dist(p, e) < near:
            break
        d = dirto(p, e)
        seat(pad(d)); c.wait_frames(8); q = snap()[2]
        mv = (q["x"] - p["x"], q["z"] - p["z"]); n = math.hypot(*mv)
        if n > 5:
            ex = est[d]; est[d] = (0.5 * ex[0] + 0.5 * mv[0] / n, 0.5 * ex[1] + 0.5 * mv[1] / n)
    seat(0xFFFF)
    return dist(snap()[2], snap()[idx])


# --- 1. P1 punches next to P3 --------------------------------------------
d = walk_to(0, near=120)
s0 = snap()
print("P3 next to P1: dist", d, "P3 hp", s0[2]["hp"])
p3_hits_from = []
last = s0[2]["hp"]
for k in range(15):
    c.press(pad("SQUARE"), frames=4, port=1); c.wait_frames(14)
    s = snap()
    if s[2]["hp"] < last:
        p3_hits_from.append(s[2]["atk"])
    last = s[2]["hp"]
c.shot(out + "/combat_p1_punch.png")
print("P3 hits during P1 punches, attacker bytes:", p3_hits_from, "(0 would be P1)")

# --- 2. P3 fights ----------------------------------------------------------
dealt, taken = [], []
prev = snap(); f0 = c.frame(); kills = 0
while c.frame() - f0 < fight:
    s = snap(); p = s[2]
    for i in range(3, 8):
        if s[i]["t"] != -1 and prev[i]["t"] == s[i]["t"] and s[i]["hp"] < prev[i]["hp"] and s[i]["atk"] == 2:
            dealt.append(prev[i]["hp"] - s[i]["hp"])
        if prev[i]["t"] != -1 and prev[i]["hp"] > 0 and s[i]["hp"] <= 0 and s[i]["atk"] == 2:
            kills += 1
    if s[2]["hp"] < prev[2]["hp"]:
        taken.append((prev[2]["hp"] - s[2]["hp"], s[2]["atk"]))
    prev = s
    if p["hp"] <= 0:
        print("P3 down at", c.frame()); break
    ens = [r for r in s[3:] if r["t"] != -1 and r["hp"] > 0]
    if not ens:
        seat(pad("RIGHT")); c.wait_frames(10); continue
    e = min(ens, key=lambda r: dist(r, p))
    dd = dirto(p, e)
    if dist(e, p) > 140:
        seat(pad(dd)); c.wait_frames(6)
    else:
        seat(pad(dd)); c.wait_frames(2); seat(pad("SQUARE")); c.wait_frames(5); seat(0xFFFF); c.wait_frames(8)
seat(0xFFFF)
print("P3 damage dealt per hit:", dealt, "kills", kills)
print("P3 damage taken (amount, attacker):", taken)
s = snap()
for r in s:
    if r["t"] != -1:
        print(r)
print(show())
print([(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]])
c.shot(out + "/combat_end.png")
