#!/usr/bin/env python3
"""Camera / leash test for three players (debug server; P3 = ff.p3 seat
override). Usage: python3 tools/ff_p3_camera.py OUTDIR

P3 walks one way for a while, then P1 the other way; prints positions, the
camera focus and pull-back, and the leash counter; screenshots."""
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
c = Client()
R = 0x800B4318


def snap():
    b = c.read(R, 3 * 0xF4)
    for _ in range(8):
        b2 = c.read(R, 3 * 0xF4)
        if b2 == b:
            break
        b = b2
    return [((struct.unpack_from("<i", b, i * 0xF4 + 0x28)[0] >> 16),
             (struct.unpack_from("<i", b, i * 0xF4 + 0x30)[0] >> 16)) for i in range(3)]


def report(tag):
    s = snap()
    fx, fz = (struct.unpack("<i", c.read(a, 4))[0] >> 16 for a in (0x80094004, 0x80094008))
    spread = struct.unpack("<i", c.read(0x80094840, 4))[0]
    cx = sum(p[0] for p in s) // 3
    cz = sum(p[1] for p in s) // 3
    box = (max(p[0] for p in s) - min(p[0] for p in s), max(p[1] for p in s) - min(p[1] for p in s))
    show = c.cmd("mod_cmd", name="ff.p3", args="show").get("reply")
    print(f"{c.frame()} {tag}: P1 {s[0]} P2 {s[1]} P3 {s[2]} | centre ({cx},{cz}) focus ({fx},{fz}) "
          f"spread {spread} box {box} | leash={show.split('leash=')[1].split()[0]}", flush=True)
    c.shot(str(out / f"cam_{tag}.png"))


c.cmd("mod_cmd", name="ff.p3", args="seat FFFF")
c.hold(0xFFFF, port=2)
report("start")
c.cmd("mod_cmd", name="ff.p3", args=f"seat {pad('UP'):04X}")
c.wait_frames(240)
report("p3_up_240")
c.wait_frames(240)
report("p3_up_480")
c.cmd("mod_cmd", name="ff.p3", args="seat FFFF")
c.cmd("set_input", buttons=pad("DOWN"))
c.wait_frames(240)
report("p1_down_240")
c.cmd("clear_input", port=1)
c.cmd("mod_cmd", name="ff.p3", args=f"seat {pad('RIGHT'):04X}")
c.wait_frames(240)
report("p3_right_240")
c.cmd("mod_cmd", name="ff.p3", args="seat FFFF")
c.wait_frames(30)
report("end")
