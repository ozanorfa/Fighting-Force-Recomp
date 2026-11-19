#!/usr/bin/env python3
"""Fighter-select test with P3's cursor (debug server; P3 = ff.p3 seat test
override). Run on a cold boot. Usage: python3 tools/ff_p3_select.py OUTDIR [moves]

Title -> 2 PLAYER GAME -> select. P3's cursor moves right `moves` times
(default 1); P1 and P2 confirm first (the front end must wait), then P3
confirms; the level starts with P3 as the fighter it chose."""
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
moves = int(sys.argv[2]) if len(sys.argv) > 2 else 1
c = Client().connect(wait_s=60)
MEM = 0x9F000000
NAMES = {0: "HAWK", 1: "MACE", 2: "SMASHER", 3: "ALANA"}


def w(a):
    return struct.unpack("<I", c.read(a, 4))[0]


def p3(cmd):
    return c.cmd("mod_cmd", name="ff.p3", args=cmd).get("reply")


def p3_tap(name, frames=4, gap=20):
    p3(f"seat {pad(name):04X}"); c.wait_frames(frames); p3("seat FFFF"); c.wait_frames(gap)


def state(tag):
    cur = [w(0x8009277C), w(0x80092784 - 4)]
    lineup = [w(0x800927A4 + 4 * k) for k in range(4)]
    p3c = w(MEM + 0x2A4)
    print(f"{c.frame()} {tag}: P1 slot {cur[0]} ({NAMES.get(lineup[cur[0] & 3])}) P2 slot {cur[1]} "
          f"({NAMES.get(lineup[cur[1] & 3])}) | P3 active {w(MEM + 0x2A0)} slot {p3c} "
          f"({NAMES.get(lineup[p3c & 3])}) confirmed {w(MEM + 0x2A8)} | confirmed mask "
          f"{w(0x8009272C)} done {w(0x80092788)} state {w(0x800927EC):#x}", flush=True)
    c.shot(str(out / f"sel_{tag}.png"))


p3("seat FFFF")
c.hold(0xFFFF, port=2)
while c.frame() < 4300:
    c.wait_frames(60)
c.enter_select()                   # straight-to-select or classic menus
state("enter")
for k in range(moves):
    p3_tap("RIGHT")
state("p3_moved")
c.tap("CROSS", frames=6, gap=60)                 # P1 confirms
c.tap("CROSS", frames=6, gap=30, port=2)         # P2 confirms
c.wait_frames(120)
state("p1p2_confirmed")
p3_tap("CROSS")
state("p3_confirmed")
c.wait_frames(3300)
types = [struct.unpack("<h", c.read(0x800B4318 + i * 0xF4 + 0x34, 2))[0] for i in range(3)]
print("level types", [NAMES.get(t, t) for t in types], "|", p3("show"))
c.shot(str(out / "sel_level.png"))
