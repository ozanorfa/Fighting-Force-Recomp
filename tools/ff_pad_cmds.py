#!/usr/bin/env python3
"""TEST ONLY: which pad commands does the game send? Runs the game from boot
(Quick Start straight to the fighter select) and reads the debug server's SIO
trace every few frames, so every transaction is seen. Counts the command byte
after each 0x01 (pad address) and the function that issued it.

Fighting Force reads its pads through the BIOS pad driver (InitPAD/StartPAD),
which polls with 0x42 only. A pad driver that
sends 0x43 (enter config mode, the DualShock path) meets the framework's
config-capable digital pad of v0.3.7 (`live_pad_mode`, short presses lost in
another title: there the driver loops the DualShock detection and polls the
buttons about once per 27 frames). Also measures port 1's button polls per
frame from --rate-from (the select screen) on. Exit 1 if any config-mode
command (0x43..0x4F) is seen, port 1 polls less than 0.95 times per frame,
or part of the trace was missed.

    python3 tools/ff_pad_cmds.py --exe build-net/FightingForce_Recompiled --out DIR
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client  # noqa: E402


def sio_trace(port: int, count: int) -> dict:
    """The reply spans many lines (one per entry): read up to the closing ]}."""
    with socket.create_connection(("127.0.0.1", port), timeout=30) as s:
        s.sendall((json.dumps({"id": 1, "cmd": "sio_trace", "count": count}) + "\n").encode())
        buf = b""
        while not buf.rstrip().endswith(b"]}"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.replace(b"\n", b""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4399)
    ap.add_argument("--until", type=int, default=1520, help="last frame (the select is at ~1,300)")
    ap.add_argument("--step", type=int, default=15, help="frames between trace reads")
    ap.add_argument("--rate-from", type=int, default=1360, help="first frame of the poll-rate window")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    log = open(out / "run.log", "w")
    proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(out / "cards"), "--debug-port", str(args.port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    c = Client(port=args.port).connect(wait_s=90)
    cmds: collections.Counter = collections.Counter()
    funcs: collections.Counter = collections.Counter()
    starts: set[int] = set()
    seen: set[int] = set()
    total = 0
    rate_polls = rate_frames = 0
    prev_frame = 0
    try:
        c.cmd("mod_cmd", name="ff.p3", args="seat off")
        for target in range(args.step, args.until + 1, args.step):
            while c.frame() < target:
                c.wait_frames(5)
            # Ask for the entries since the last read, with room for the ones
            # that arrive between the two requests (bursts while loading).
            now = sio_trace(args.port, 1).get("total", 0)
            r = sio_trace(args.port, max(now - total, 0) + 4000)
            ents = r.get("entries", [])
            total = r.get("total", 0)
            seen.update(e["seq"] for e in ents)
            frame = c.frame()
            in_window = prev_frame >= args.rate_from
            for e, nx in zip(ents, ents[1:]):
                if e["tx"] == "0x01" and e["seq"] not in starts:
                    starts.add(e["seq"])
                    cmds[nx["tx"]] += 1
                    funcs[e["func"]] += 1
                    # SIO_CTRL bit 13 selects the port: clear = port 1.
                    if in_window and nx["tx"] == "0x42" and not int(e["ctrl"], 16) & 0x2000:
                        rate_polls += 1
            if in_window:
                rate_frames += frame - prev_frame
            prev_frame = frame
        print(f"frames 0..{c.frame()}: {len(seen)} of {total} trace entries read "
              f"({100 * len(seen) / max(total, 1):.1f} %)")
        print("pad commands after 0x01:", dict(cmds))
        print("issued from:", dict(funcs))
        print("pad_status slot0:", c.cmd("pad_status").get("slot0"))
        rate = rate_polls / max(rate_frames, 1)
        print(f"port 1 button polls: {rate_polls} in {rate_frames} frames ({rate:.2f} per frame)")
        config = {k: v for k, v in cmds.items() if 0x43 <= int(k, 16) <= 0x4F}
        if config:
            print("CONFIG-MODE COMMANDS SEEN:", config)
            return 1
        if rate_frames == 0 or rate < 0.95:
            print("PORT 1 POLL RATE TOO LOW (or no window: raise --until)")
            return 1
        if len(seen) < total:
            print("part of the trace was missed: use a smaller --step")
            return 1
        print("no config-mode command: the pad driver polls only")
        return 0
    finally:
        try:
            c.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
        if proc.poll() is None:
            proc.terminate()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
