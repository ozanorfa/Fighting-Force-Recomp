#!/usr/bin/env python3
"""All three players fight on autopilot (P1 port 1, P2 port 2, P3 seat 3 test
override). Logs kills, deaths, level number and P3 counters; screenshots.
Usage: python3 tools/ff_p3_autoplay.py OUTDIR FRAMES [2|3 players]"""
from pathlib import Path
import math
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

out = sys.argv[1]
total = int(sys.argv[2])
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 3  # 2 = stock game (record 2 is an enemy)
c = Client()
R = 0x800B4318
G_LEVEL = 0x80093D58


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
                        z=g(0x30, "<i") >> 16, atk=g(0x81, "<b"), lvl=g(0xB8, "<i")))
    return res


def dist(a, b):
    dx, dz = abs(a["x"] - b["x"]), abs(a["z"] - b["z"])
    return max(dx, dz) + min(dx, dz) // 2


est = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}


def dirto(p, e):
    dx, dz = e["x"] - p["x"], e["z"] - p["z"]
    return max(est, key=lambda d: (est[d][0] * dx + est[d][1] * dz) / max(1e-6, math.hypot(*est[d])))


def send(player, word):
    if player == 2:
        c.cmd("mod_cmd", name="ff.p3", args=f"seat {word:04X}")
    elif player == 1:
        c.cmd("set_input", buttons=word, port=2, connected=1)
    else:
        c.cmd("set_input", buttons=word)


def level():
    return struct.unpack("<i", c.read(G_LEVEL, 4))[0]


f0 = c.frame(); prev = snap(); tick = 0
kills = {}; deaths = []; lv = level(); next_shot = f0
print("start frame", f0, "level", lv, flush=True)
try:
    while c.frame() - f0 < total:
        s = snap()
        for i in range(NP, 8):
            if prev[i]["t"] != -1 and prev[i]["hp"] > 0 and s[i]["hp"] <= 0 and s[i]["t"] == prev[i]["t"]:
                kills[s[i]["atk"]] = kills.get(s[i]["atk"], 0) + 1
        for p in range(NP):
            if prev[p]["hp"] > 0 and s[p]["hp"] <= 0 and s[p]["t"] != -1:
                deaths.append((c.frame(), p + 1))
                print("death", c.frame(), "P%d" % (p + 1), flush=True)
        prev = s
        ens = [r for r in s[NP:] if r["t"] not in (-1, 0x11) and r["hp"] > 0]
        phase = tick % 3
        for p in range(NP):
            me = s[p]
            if me["t"] == -1 or me["hp"] <= 0:
                continue
            if not ens:
                send(p, pad("UP") if phase else 0xFFFF)  # level 0 runs toward +z
                continue
            e = min(ens, key=lambda r: dist(r, me))
            d = dirto(me, e)
            if dist(e, me) > 140:
                send(p, pad(d))
            else:
                send(p, pad(d) if phase == 0 else (pad("SQUARE") if phase == 1 else 0xFFFF))
        tick += 1
        c.wait_frames(4)
        wst = (c.read(0x80093DD9, 1)[0], struct.unpack("<i", c.read(0x80093D60, 4))[0])
        if wst != globals().get("last_wst"):
            print("wave pause/area", c.frame(), wst, "z", [r["z"] for r in s[:3]], flush=True)
            globals()["last_wst"] = wst
        nl = level()
        if nl != lv:
            print("level change", c.frame(), lv, "->", nl, c.cmd("mod_cmd", name="ff.p3", args="show").get("reply"), flush=True)
            lv = nl
        if c.frame() >= next_shot:
            f = c.frame()
            c.shot(f"{out}/auto_f{f}.png")
            hp = [r["hp"] for r in s[:3]]
            print("t", f - f0, "level", lv, "hp", hp, "z", [r["z"] for r in s[:NP]], "enemies", len(ens), "kills", kills,
                  c.cmd("mod_cmd", name="ff.p3", args="show").get("reply"), flush=True)
            next_shot = f + 1500
except Exception as ex:  # noqa: BLE001
    print("EXCEPTION", repr(ex), flush=True)
for p in range(3):
    try:
        send(p, 0xFFFF)
    except Exception:  # noqa: BLE001
        pass
print("kills by attacker", kills, "deaths", deaths)
print(c.cmd("mod_cmd", name="ff.p3", args="show").get("reply"))
print([(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]])
