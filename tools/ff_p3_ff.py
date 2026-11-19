#!/usr/bin/env python3
"""Friendly-fire test between the three players (debug server, P3 via the
ff.p3 seat test override). Usage: python3 tools/ff_p3_ff.py OUTDIR

For each pair (attacker -> target): walk the attacker next to the target,
face it and punch 12 times; report the target's health and attacker byte.
Players: 0 = P1 (port 1), 1 = P2 (port 2), 2 = P3 (seat 3 override).
Health reads can land inside the P3 view (slot 1 then holds P3); a P2/P3
reading is only trusted when two reads agree."""
from pathlib import Path
import math
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

out = sys.argv[1]
c = Client()
R = 0x800B4318


def rec(i):
    b = c.read(R + i * 0xF4, 0xF4)
    g = lambda o, f: struct.unpack_from(f, b, o)[0]
    return dict(i=i, t=g(0x34, "<h"), hp=g(0x42, "<h"), x=g(0x28, "<i") >> 16,
                z=g(0x30, "<i") >> 16, atk=g(0x81, "<b"), st=g(0x0C, "<I"))


def stable(i):
    while True:
        a, b = rec(i), rec(i)
        if (a["hp"], a["x"], a["z"]) == (b["hp"], b["x"], b["z"]):
            return b


def send(p, word):
    if p == 2:
        c.cmd("mod_cmd", name="ff.p3", args=f"seat {word:04X}")
    elif p == 1:
        c.cmd("set_input", buttons=word, port=2, connected=1)
    else:
        c.cmd("set_input", buttons=word)


def dist(a, b):
    dx, dz = abs(a["x"] - b["x"]), abs(a["z"] - b["z"])
    return max(dx, dz) + min(dx, dz) // 2


est = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}


def dirto(p, e):
    dx, dz = e["x"] - p["x"], e["z"] - p["z"]
    return max(est, key=lambda d: (est[d][0] * dx + est[d][1] * dz) / max(1e-6, math.hypot(*est[d])))


def walk_to(a, t, near=110):
    for _ in range(60):
        pa, pt = stable(a), stable(t)
        if dist(pa, pt) < near:
            break
        send(a, pad(dirto(pa, pt))); c.wait_frames(6)
    send(a, 0xFFFF); c.wait_frames(4)


def show():
    return c.cmd("mod_cmd", name="ff.p3", args="show").get("reply")


c.cmd("mod_cmd", name="ff.p3", args="seat FFFF")
c.hold(0xFFFF, port=2)
results = []
for a, t in ((0, 2), (2, 0), (1, 2), (2, 1), (0, 1)):
    walk_to(a, t)
    before = stable(t)
    hits = []
    last = before["hp"]
    for k in range(12):
        pa, pt = stable(a), stable(t)
        send(a, pad(dirto(pa, pt))); c.wait_frames(2)
        send(a, pad("SQUARE")); c.wait_frames(5)
        send(a, 0xFFFF); c.wait_frames(12)
        now = stable(t)
        if now["hp"] < last:
            hits.append((last - now["hp"], now["atk"]))
        last = now["hp"]
    after = stable(t)
    line = f"P{a+1} -> P{t+1}: dist {dist(stable(a), after)} hp {before['hp']} -> {after['hp']} hits {hits}"
    print(line, flush=True)
    results.append(line)
    c.shot(f"{out}/ff_P{a+1}_to_P{t+1}.png")
    c.wait_frames(60)
print(show())
print([(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]])
