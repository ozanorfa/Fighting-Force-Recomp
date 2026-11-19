#!/usr/bin/env python3
"""Start the recompiled game headless with the debug server and drive it.

Standard library only (macOS / Windows).

    python3 tools/ff_run.py --exe build-release/FightingForce_Recompiled \
        --out ../screenshots/phase1/boot1 --frames 3000 --every 60

Starts the runtime with PSX_DEBUG_SERVER=1, --no-launcher and a fresh
memory-card folder inside --out (so the player's saves are never touched),
takes a screenshot every --every frames until --frames, then quits.
A --script file can add timed inputs: lines "<frame> <port> <BUTTONS> [hold]"
(e.g. "900 1 START 4"). The runtime's stdout/stderr go to <out>/runtime.log.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--game", default="game.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--every", type=int, default=60)
    ap.add_argument("--script", default="")
    ap.add_argument("--mode", default="--headless",
                    help="--headless or --hidden-window")
    ap.add_argument("--extra", action="append", default=[],
                    help="extra runtime argument (repeatable)")
    ap.add_argument("--keep", action="store_true",
                    help="leave the runtime running at the end")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cards = out / "memcards"
    cards.mkdir(exist_ok=True)

    events: list[tuple[int, int, int, int]] = []
    if args.script:
        for line in Path(args.script).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            hold = int(parts[3]) if len(parts) > 3 else 4
            events.append((int(parts[0]), int(parts[1]), pad(parts[2]), hold))
        events.sort()

    env = dict(os.environ, PSX_DEBUG_SERVER="1")
    cmd = [args.exe, args.mode, "--no-launcher", "--game", args.game,
           "--disc", args.disc, "--memcard-dir", str(cards), *args.extra]
    log = open(out / "runtime.log", "wb")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
    t0 = time.time()
    shots = []
    try:
        c = Client().connect(wait_s=60)
        next_shot = 0
        ei = 0
        while True:
            if proc.poll() is not None:
                print(f"runtime exited with code {proc.returncode}")
                break
            f = c.frame()
            while ei < len(events) and events[ei][0] <= f:
                _, port, word, hold = events[ei]
                c.press(word, frames=hold, port=port)
                ei += 1
            if f >= next_shot:
                p = out / f"f{f:06d}.png"
                r = c.shot(str(p))
                shots.append((f, round(time.time() - t0, 1), r.get("ok")))
                next_shot = f + args.every
            if f >= args.frames:
                break
            time.sleep(0.02)
        if proc.poll() is None:
            info = {k: c.cmd(k) for k in ("frame", "bios_info")}
            (out / "final_state.json").write_text(json.dumps(info, indent=1))
            if not args.keep:
                try:
                    c.cmd("quit")
                except (OSError, ConnectionError):
                    pass
    finally:
        if not args.keep:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
    (out / "shots.json").write_text(json.dumps(shots))
    print(f"{len(shots)} screenshots in {out} ({round(time.time() - t0, 1)} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
