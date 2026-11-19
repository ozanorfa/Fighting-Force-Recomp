#!/usr/bin/env python3
"""TEST ONLY: end the running level's wave script (debug RAM writes) and
watch the level-done path. Usage: python3 tools/ff_end_waves.py OUTDIR [frames]"""
from pathlib import Path
import struct, sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client
out = sys.argv[1]; frames = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
c = Client()
def w(a): return struct.unpack("<i", c.read(a, 4))[0]
def wr(a, v, n=4): c.cmd("write_bulk", addr=f"0x{a:08X}", hex=struct.pack("<I" if n == 4 else "<B", v & (0xFFFFFFFF if n == 4 else 0xFF)).hex())
wr(0x80093DD9, 1, 1)       # pause first: the script returns at its top while paused
c.wait_frames(2)
sp = w(0x800946B8) & 0xFFFFFFFF
# first halfword -1 at or after the script pointer
term = next(sp + 2 * i for i in range(64) if struct.unpack("<h", c.read(sp + 2 * i, 2))[0] == -1)
print("script ptr", hex(sp), "-> terminator", hex(term))
wr(0x800946B8, term)       # script at its end
# DAT_80093dbc = enemies of the wave not yet defeated (each enemy KO takes
# one off): set it to the enemies alive now, not 0, or it goes negative.
p3_active = "active=1" in (c.cmd("mod_cmd", name="ff.p3", args="show").get("reply") or "")
live = 0
for i in range(2, 8):
    if i == 2 and p3_active:
        continue
    t, = struct.unpack("<h", c.read(0x800B4318 + i * 0xF4 + 0x34, 2))
    hp, = struct.unpack("<h", c.read(0x800B4318 + i * 0xF4 + 0x42, 2))
    if t not in (-1, 0x11, 0x33) and hp > 0:
        live += 1
print("enemies alive", live)
wr(0x80093DBC, live)
wr(0x80093DC0, 0)
wr(0x80093DD9, 0, 1)       # unpause
f0 = c.frame(); last = None
while c.frame() - f0 < frames:
    st = (w(0x80093D58), w(0x800941B8), struct.unpack("<h", c.read(0x800B4318 + 2 * 0xF4 + 0x34, 2))[0])
    if st != last:
        print(c.frame(), "level", st[0], "done", st[1], "rec2 type", st[2], flush=True); last = st
        c.shot(f"{out}/endw_f{c.frame()}.png")
    c.wait_frames(10)
r = c.cmd("mod_cmd", name="ff.p3", args="show")
print(r.get("reply"))
