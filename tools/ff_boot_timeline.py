#!/usr/bin/env python3
"""Boot-to-gameplay timeline (headless, debug server): every STEP frames log
FMV decoding (MDEC), XA audio, CD reading and the screen; drive title ->
2 PLAYER GAME -> select (P1 / P2 / P3 confirm) as soon as each screen accepts
input; stop when the players can move in level 0. Prints the frame of each
milestone (60 frames = 1 s of play).

    python3 tools/ff_boot_timeline.py --exe build-net/FightingForce_Recompiled --out DIR [--env K=V ...]
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

R = 0x800B4318
G_LEVEL = 0x80093D58


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4370)
    ap.add_argument("--mode", default="--headless", help="--headless (unpaced) or --hidden-window (60 fps pacing)")
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--shots", type=int, default=120, help="screenshot every N frames (0 = none)")
    ap.add_argument("--max", type=int, default=20000)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--tap", action="append", default=[],
                    help="FRAME:BUTTON, tap a pad-1 button at that frame (boot phase)")
    ap.add_argument("--tap-after-pick", action="append", default=[],
                    help="OFFSET:BUTTON, tap a pad-1 button OFFSET frames after the fighters are picked")
    ap.add_argument("--title-frame", type=int, default=4300, help="frame at which the title takes input")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    for kv in args.env:
        k, v = kv.split("=", 1)
        env[k] = v
    log = open(out / "run.log", "w")
    t0 = time.time()
    proc = subprocess.Popen([args.exe, args.mode, "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(out / "cards"), "--debug-port", str(args.port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    c = Client(port=args.port).connect(wait_s=90)

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def rec(i):
        b = c.read(R + i * 0xF4, 0xF4)
        return dict(t=struct.unpack_from("<h", b, 0x34)[0], hp=struct.unpack_from("<h", b, 0x42)[0],
                    x=struct.unpack_from("<i", b, 0x28)[0] >> 16)

    taps = [(int(s.split(":")[0]), s.split(":")[1]) for s in args.tap]
    rows = []
    last_mdec = None
    phase = "boot"
    marks = {}
    wall = {}
    next_shot = 0
    p3("seat FFFF")
    c.hold(0xFFFF, port=2)
    try:
        while c.frame() < args.max:
            f = c.frame()
            fs = c.cmd("fmv_state")
            mdec = fs.get("mdec_decode_count", fs.get("mdec_decodes"))
            xa = fs.get("xa_stream_active", fs.get("xa_active"))
            cd = c.cmd("cdrom_state")
            reading = cd.get("reading", cd.get("read_active", cd.get("busy")))
            decoding = last_mdec is not None and mdec != last_mdec
            last_mdec = mdec
            rows.append((f, phase, int(bool(decoding)), xa, reading))
            if args.shots and f >= next_shot:
                c.shot(str(out / f"t_{f:05d}_{phase}.png"))
                next_shot = f + args.shots
            for spec in list(taps):
                at, name = spec
                if f >= at:
                    c.press(pad(name), frames=6)
                    marks[f"tap {name} @{at}"] = f
                    taps.remove(spec)
            if phase == "boot" and f >= args.title_frame:
                marks["title (input)"] = f
                wall["title (input)"] = round(time.time() - t0, 1)
                c.tap("START", frames=6, gap=450)
                c.tap("DOWN", frames=6, gap=40)
                c.tap("CROSS", frames=6, gap=400)
                marks["select screen"] = c.frame()
                p3(f"seat {pad('RIGHT'):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(20)
                c.tap("CROSS", frames=6, gap=60)
                c.tap("CROSS", frames=6, gap=30, port=2)
                p3(f"seat {pad('CROSS'):04X}"); c.wait_frames(4); p3("seat FFFF")
                marks["fighters picked"] = c.frame()
                wall["fighters picked"] = round(time.time() - t0, 1)
                for spec in args.tap_after_pick:
                    off, name = spec.split(":")
                    taps.append((c.frame() + int(off), name))
                phase = "to_level"
                continue
            if phase == "to_level":
                # gameplay = the level loop runs P3's player update (the
                # plugin's "passes" counter moves); then check P1 moves too
                s = p3("show")
                passes = int(s.split("passes=")[1].split()[0]) if "passes=" in s else 0
                if passes > 0 and "level loop running" not in marks:
                    marks["level loop running"] = f
                    wall["level loop running"] = round(time.time() - t0, 1)
                if "level loop running" in marks:
                    r0 = rec(0)
                    x0 = r0["x"]
                    c.press(pad("RIGHT"), frames=10)
                    c.wait_frames(12)
                    if rec(0)["x"] != x0:
                        marks["player can move"] = c.frame()
                        c.shot(str(out / f"t_{c.frame():05d}_playable.png"))
                        break
            c.wait_frames(args.step)
    finally:
        try:
            c.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
        if proc.poll() is None:
            proc.terminate()
        log.close()
    # spans of FMV decoding / CD reading
    def spans(idx):
        res, start = [], None
        for f, _, *vals in rows:
            on = bool(vals[idx])
            if on and start is None:
                start = f
            if not on and start is not None:
                res.append((start, f)); start = None
        if start is not None:
            res.append((start, rows[-1][0]))
        return res
    print("keys fmv_state:", sorted(fs.keys()) if isinstance(fs, dict) else fs)
    print("keys cdrom_state:", sorted(cd.keys()) if isinstance(cd, dict) else cd)
    print("FMV (MDEC decoding) spans:", spans(0))
    print("CD reading spans:", spans(2))
    print("marks:", json.dumps(marks))
    print("wall seconds since start:", json.dumps(wall))
    print(f"wall time {time.time() - t0:.0f} s (headless, unpaced)")
    (out / "timeline.json").write_text(json.dumps({"rows": rows, "marks": marks}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
