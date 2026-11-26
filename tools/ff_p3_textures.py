#!/usr/bin/env python3
"""TEST ONLY: P3's fighter textures in a level.

The level setup's texture loader loads the fighter texture slots only for
the fighters of players 1 and 2. A P3 fighter that neither of them plays
was drawn with another texture set ("Alana as a robot"). The third-player
mod now has that load include P3's fighter.

Per scenario (a 3-player drop-in game, offline):
1. On the select screen, the five fighter texture bases (s16 at
   0x800AF418 + 2 * slot, slots 0x24..0x28) are set to a marker.
2. P1, P2 and P3 move their cursors as given and confirm.
3. In the level, the bases of P1's, P2's and P3's fighters must have been
   set by this level's load (no marker). The fighter nobody plays and the
   HAWK2 slot must still hold the marker (nothing extra in VRAM).
   tex_loads counts the loads that added P3's slot. A level without room
   for a third fighter (texture entry table, VRAM) keeps the stock load
   instead: tex_skips counts it (P3's base was set by the measuring pass)
   and the game must keep running. The first-level scenarios expect the load.
Also: the level's 4096-entry map at 0x8009F870, which the loader's last
step converts in place (v = (v + base) * 3), must be converted exactly once:
every value is -4 or inside the entry table (0 <= v < 3 * entries). Running
the loader more than once without restoring it scrambled the level's own
textures.
A native screenshot of the level is kept for the eye.

--levels runs the "smasher" scenario (Hawk, Mace, Smasher: the three largest
fighter texture sets), or the --scenarios given, once per level id (e.g.
--levels 5,7-9), started with the game's own
level-select cheat (test-only writes, as tools/ff_p3_waves.py does), and
also reports the free cells of the game's VRAM allocation map (0x800E1140,
0x40 x 0x100 bytes, 1 = used; the allocator FUN_80066acc hangs when no
rectangle fits) and that the game keeps running.

    python3 tools/ff_p3_textures.py --exe build-net/FightingForce_Recompiled --out DIR
        [--scenarios alana,smasher,hawk,mace] [--levels 0-24]
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

R = 0x800B4318            # player records, 0xF4 each; type s16 at +0x34
TEX_BASE = 0x800AF418     # s16 per texture slot
SLOT0 = 0x24              # fighter slots 0x24 + type (4 = HAWK2)
MARK = 0x7EEE
G_CHEATS, G_START_LEVEL, G_LEVEL = 0x80093D68, 0x800927B4, 0x80093D58
LEVEL_MAP, ENTRIES = 0x8009F870, 0x80094838
VRAM_MAP, VRAM_MAP_SIZE = 0x800E1140, 0x4000
NAMES = ["HAWK", "MACE", "SMASHER", "ALANA", "HAWK2"]
# (P1 taps, P2 taps, P3 taps) on the select screen, made after the line-up
# intro (cursors do not move before it). Line-up slots: 0 Alana, 1 Hawk,
# 2 Mace, 3 Smasher; a cursor moves to the next free slot.
SCENARIOS = {
    "alana":   ([], [], []),                       # Hawk, Mace, Alana
    "smasher": ([], [], ["RIGHT"]),                # Hawk, Mace, Smasher
    "hawk":    (["RIGHT"], [], ["RIGHT"]),         # Smasher, Mace, Hawk
    "mace":    (["RIGHT"], ["LEFT"], ["RIGHT"]),   # Smasher, Hawk, Mace
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4423)
    ap.add_argument("--scenarios", default=None,
                    help="default: all four; with --levels: smasher (the largest three fighters)")
    ap.add_argument("--levels", default="", help="e.g. 0-24 or 7,12: the smasher scenario (or --scenarios) per level id")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    results, seen_p3 = [], set()

    runs = [(n, None) for n in (args.scenarios or ",".join(SCENARIOS)).split(",")]
    if args.levels:
        levels = []
        for part in args.levels.split(","):
            lo, _, hi = part.partition("-")
            levels += range(int(lo), int(hi or lo) + 1)
        runs = [(n, lv) for lv in levels for n in (args.scenarios or "smasher").split(",")]
    for name, level in runs:
        p1_taps, p2_taps, p3_taps = SCENARIOS[name]
        tag = name if level is None else f"{name}_lv{level:02d}"
        log = open(out / f"run_{tag}.log", "w")
        proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                                 "--memcard-dir", str(out / f"cards_{tag}"), "--debug-port", str(args.port)],
                                stdout=log, stderr=subprocess.STDOUT, env=env)
        r = {"scenario": tag}
        try:
            c = Client(port=args.port).connect(wait_s=90)

            def p3(s):
                return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

            def show(k):
                return int(re.search(rf"\b{k}=(\d+)", p3("show"))[1])

            def p3_tap(b):
                p3(f"seat {pad(b):04X}"); c.wait_frames(8); p3("seat FFFF"); c.wait_frames(20)

            def p2_tap(b):
                c.cmd("set_input", buttons=pad(b), port=2, connected=1); c.wait_frames(8)
                c.cmd("set_input", buttons=0xFFFF, port=2, connected=1); c.wait_frames(20)

            def bases():
                return list(struct.unpack("<5h", c.read(TEX_BASE + 2 * SLOT0, 10)))

            def rtype(i):
                return struct.unpack("<h", c.read(R + i * 0xF4 + 0x34, 2))[0]

            def stable_types():
                """P1's and P2's types from their records, read while the game runs
                (P3's update pass swaps records for a moment: the most common of 7
                reads), and P3's from the plugin (record 2's type is hidden from
                the enemy code for much of each frame)."""
                seen = []
                for _ in range(7):
                    seen.append(tuple(rtype(i) for i in range(2)))
                    c.wait_frames(5)
                p3_type = show("fighter") if show("spawns") >= 1 else -1
                return list(max(set(seen), key=seen.count)) + [p3_type]

            p3("seat FFFF")
            c.hold(0xFFFF, port=2)
            c.enter_select()
            c.wait_frames(60)
            mem = int(re.search(r"mem=0x([0-9A-Fa-f]+)", p3("show"))[1], 16)
            if not struct.unpack("<I", c.read(mem + 0x2A0, 4))[0]:
                p2_tap("CROSS")                    # drop-in select: P2 and P3 join with X
                p3_tap("CROSS")
            c.wait_frames(400)                     # the line-up intro
            for b in p1_taps:
                c.tap(b, frames=8, gap=20)
            for b in p2_taps:
                p2_tap(b)
            for b in p3_taps:
                p3_tap(b)
            c.wait_frames(20)
            loads0, skips0, app0 = show("tex_loads"), show("tex_skips"), show("tex_appends")
            if level is not None:
                cheats = struct.unpack("<I", c.read(G_CHEATS, 4))[0]
                c.cmd("write_bulk", addr=f"0x{G_CHEATS:08X}", hex=struct.pack("<I", cheats | 2).hex())
                c.cmd("write_bulk", addr=f"0x{G_START_LEVEL:08X}", hex=struct.pack("<I", level).hex())
            c.cmd("write_bulk", addr=f"0x{TEX_BASE + 2 * SLOT0:08X}", hex=struct.pack("<5h", *[MARK] * 5).hex())
            p3_tap("CROSS")
            p2_tap("CROSS")
            c.tap("CROSS", frames=8, gap=20)
            f0 = c.frame()
            while c.frame() - f0 < 8000 and show("passes") < 2:
                c.wait_frames(30)
            c.wait_frames(300)
            types = stable_types()
            b = bases()
            loads1, skips1, app1 = show("tex_loads"), show("tex_skips"), show("tex_appends")
            entries = struct.unpack("<I", c.read(ENTRIES, 4))[0]
            lmap = struct.unpack("<4096h", c.read(LEVEL_MAP, 0x2000))
            map_bad = sum(1 for v in lmap if v != -4 and not 0 <= v < 3 * entries)
            est = {k: show(k) for k in ("tex_entries", "tex_free", "tex_room", "tex_need", "tex_acc_wait",
                                          "tex_tiles")}
            m = re.search(r"\btex_fail=0x([0-9A-Fa-f]+)", p3("show"))
            if m and int(m[1], 16):
                f = int(m[1], 16)  # the first texture the fit check could not place
                est["tex_fail"] = dict(texture=f >> 16 & 0x7FFF, w=(f >> 8 & 0x3F) * 8, h=(f >> 2 & 0x3F) * 8,
                                       depth=f & 3, palette_block=bool(f >> 31))
            c.shot(str(out / f"{tag}_level.png"))
            if level is not None:
                vmap = c.read(VRAM_MAP, VRAM_MAP_SIZE)
                f1 = c.frame()
                c.wait_frames(120, stall_s=30)
                r.update(level=struct.unpack("<I", c.read(G_LEVEL, 4))[0],
                         vram_free_cells=VRAM_MAP_SIZE - sum(1 for x in vmap if x),
                         running=c.frame() >= f1 + 120)
            played = set(types)
            distinct = len(played) == 3 and all(0 <= t <= 3 for t in types)
            loaded = loads1 == loads0 + 1 and skips1 == skips0 and app1 == app0
            appended = app1 == app0 + 1 and loads1 == loads0 and skips1 == skips0
            skipped = skips1 == skips0 + 1 and loads1 == loads0 and app1 == app0
            # the slots this level's loads set: P1's, P2's and (loaded, or measured
            # when there is no room) P3's; never the fourth fighter or HAWK2
            bases_ok = (all(b[t] != MARK for t in types if 0 <= t <= 4)
                        and all(b[k] == MARK for k in range(5) if k not in played))
            r.update(map_bad=map_bad, types=[NAMES[t] if 0 <= t <= 4 else t for t in types], bases=b,
                     result="P3 loaded" if loaded else "P3 in a second load" if appended
                     else "no room, stock load" if skipped else "?",
                     **est)
            counters = {x["name"]: x["count"] for x in c.cmd("mod_counters").get("counters", [])
                        if x["name"].startswith("ff.p3.tex")}
            r["tex_counters"] = counters
            r["ok"] = (distinct and (loaded or appended or (skipped and level is not None)) and bases_ok
                       and map_bad == 0 and r.get("running", True)
                       and not counters.get("ff.p3.tex_sim_mismatch"))
            if distinct:
                seen_p3.add(types[2])
            else:
                r["p3_counters"] = {x["name"]: x["count"] for x in c.cmd("mod_counters").get("counters", [])
                                    if x["name"].startswith("ff.p3.")}
        except Exception as e:  # report and go on with the next scenario
            r["ok"], r["error"] = False, repr(e)
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        print(r, flush=True)
        results.append(r)

    ok = all(x["ok"] for x in results)
    print("P3 fighters covered:", sorted(NAMES[t] for t in seen_p3))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
