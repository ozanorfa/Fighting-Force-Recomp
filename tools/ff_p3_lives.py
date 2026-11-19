#!/usr/bin/env python3
"""Downed-player / game-over test with P3 (debug server; P3 via the ff.p3
seat override; health via the ff.p3 "hp" test command). TEST ONLY: sets
health and lives with debug commands. Usage: python3 tools/ff_p3_lives.py OUTDIR

A. P3 down -> continue prompt -> X on seat 3 -> back with a life used.
B. P1 and P2 down, P3 standing -> no game over, camera on P3, P1/P2 prompts.
C. P3 down as well -> game over starts."""
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

out = sys.argv[1]
c = Client()
R = 0x800B4318
LIVES, GAME_OVER, FOCUS_X, FOCUS_Z = 0x80093D50, 0x80093DD4, 0x80094004, 0x80094008


def w(a):
    return struct.unpack("<i", c.read(a, 4))[0]


def snap():
    b = c.read(R, 3 * 0xF4)
    for _ in range(8):
        b2 = c.read(R, 3 * 0xF4)
        if b2 == b:
            break
        b = b2
    res = []
    for i in range(3):
        g = lambda o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        res.append(dict(hp=g(0x42, "<h"), x=g(0x28, "<i") >> 16, z=g(0x30, "<i") >> 16,
                        st=hex(g(0x0C, "<I")), down=g(0x77, "<B")))
    return res


def p3(cmd):
    return c.cmd("mod_cmd", name="ff.p3", args=cmd).get("reply")


def line(tag):
    s = snap()
    print(f"{c.frame()} {tag}: " + " ".join(
        f"P{i+1}(hp{r['hp']} {r['x']},{r['z']} {r['st']} down{r['down']})" for i, r in enumerate(s)),
        f"| lives {w(LIVES)} game_over {w(GAME_OVER)} focus {w(FOCUS_X) >> 16},{w(FOCUS_Z) >> 16}",
        flush=True)
    return s


def wait_until(pred, frames, step=10):
    f0 = c.frame()
    while c.frame() - f0 < frames:
        if pred():
            return True
        c.wait_frames(step)
    return False


p3("seat FFFF")
c.hold(0xFFFF, port=2)
if w(LIVES) < 2:
    c.cmd("write_bulk", addr=f"0x{LIVES:08X}", hex=struct.pack("<i", 3).hex())  # test only
line("start")

# ---- A
p3("hp 2 0")
ok = wait_until(lambda: snap()[2]["down"] != 0, 900)
s = line(f"A P3 down (down flag set: {ok})")
c.wait_frames(16)
c.shot(f"{out}/A_p3_prompt.png")
lives0 = w(LIVES)
p3(f"seat {pad('CROSS'):04X}"); c.wait_frames(6); p3("seat FFFF")
c.wait_frames(60)
s = line("A after X")
print("A result: lives", lives0, "->", w(LIVES), "| P3 hp", s[2]["hp"], "|", p3("show"))
c.shot(f"{out}/A_p3_back.png")

# ---- B
p3("hp 0 0"); c.wait_frames(4); p3("hp 1 0")
ok = wait_until(lambda: snap()[0]["down"] != 0 and snap()[1]["down"] != 0, 900)
line(f"B P1+P2 down (flags: {ok})")
c.wait_frames(120)
line("B +120 frames")
c.shot(f"{out}/B_p3_alone.png")
for k in range(4):
    p3(f"seat {pad('UP'):04X}"); c.wait_frames(30); p3("seat FFFF"); c.wait_frames(5)
s = line("B after P3 walked")
print("B camera focus vs P3:", (w(FOCUS_X) >> 16, w(FOCUS_Z) >> 16), (s[2]["x"], s[2]["z"]), "|", p3("show"))
c.shot(f"{out}/B_p3_walked.png")

# ---- C
p3("hp 2 0")
ok = wait_until(lambda: w(GAME_OVER) != 0, 900)
line(f"C all down (game over started: {ok})")
c.wait_frames(240)
line("C +240 frames")
c.shot(f"{out}/C_game_over.png")
print(p3("show"))
print([(o["addr"], o["calls"], o["guard_misses"]) for o in c.cmd("func_override")["overrides"]])
