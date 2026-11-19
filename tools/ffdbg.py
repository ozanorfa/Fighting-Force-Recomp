#!/usr/bin/env python3
"""Small client for the PSXRecomp TCP debug server (JSON over newline).

Standard library only, so it runs the same on macOS and Windows.

Library use:
    from ffdbg import Client, pad
    c = Client()                      # 127.0.0.1:4370
    c.cmd("ping")
    c.press(pad("START"))             # pad 1, 2 frames
    c.hold(pad("CROSS"), port=2)      # pad 2 held until c.release(2)
    c.shot("/abs/path.png")

CLI:
    python3 tools/ffdbg.py ping
    python3 tools/ffdbg.py cmd <name> key=value ...
    python3 tools/ffdbg.py press START [frames] [port]
    python3 tools/ffdbg.py shot /abs/path.png
"""
from __future__ import annotations

import json
import socket
import sys
import time

# PS1 pad word bits (active low: a 0 bit means pressed).
BITS = {
    "SELECT": 0, "L3": 1, "R3": 2, "START": 3, "UP": 4, "RIGHT": 5,
    "DOWN": 6, "LEFT": 7, "L2": 8, "R2": 9, "L1": 10, "R1": 11,
    "TRIANGLE": 12, "CIRCLE": 13, "CROSS": 14, "SQUARE": 15,
}


def pad(*names: str) -> int:
    """Active-low pad word with the named buttons pressed."""
    word = 0xFFFF
    for n in names:
        for part in n.upper().split("+"):
            if part:
                word &= ~(1 << BITS[part])
    return word


class Stalled(RuntimeError):
    """The guest stopped running (frame counter frozen)."""


class Client:
    def __init__(self, host: str = "127.0.0.1", port: int = 4370,
                 timeout: float = 20.0):
        self.addr = (host, port)
        self.timeout = timeout
        self.next_id = 1

    def connect(self, wait_s: float = 60.0) -> "Client":
        """Wait until the server accepts connections."""
        deadline = time.time() + wait_s
        while True:
            try:
                socket.create_connection(self.addr, timeout=2.0).close()
                return self
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)

    def cmd(self, name: str, /, **params) -> dict:
        """One request per connection: the server closes after each reply."""
        rid = self.next_id
        self.next_id += 1
        req = {"id": rid, "cmd": name}
        req.update(params)
        with socket.create_connection(self.addr, timeout=self.timeout) as s:
            s.sendall((json.dumps(req, separators=(",", ":")) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(1 << 20)
                if not chunk:
                    raise ConnectionError("debug server closed the connection")
                buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])

    # --- helpers -----------------------------------------------------------
    def frame(self) -> int:
        # `ping` is answered by the I/O thread without a frame number.
        r = self.cmd("frame")
        return int(r.get("frame", -1))

    def wait_frames(self, n: int, poll: float = 0.05, stall_s: float = 60.0) -> int:
        """Wait until n frames have run. Raises Stalled when the frame
        counter stops for stall_s seconds (the emulation halted, e.g. on a
        FAIL-FAST; the debug server stays up)."""
        start = last = self.frame()
        t_last = time.monotonic()  # does not run while the host sleeps (macOS)
        while True:
            f = self.frame()
            if f - start >= n:
                return f
            if f != last:
                last, t_last = f, time.monotonic()
            elif time.monotonic() - t_last > stall_s:
                raise Stalled(f"emulation stopped at frame {f}")
            time.sleep(poll)

    def enter_select(self, timeout: int = 4000) -> str:
        """Get to the 2-player fighter select with P3's cursor up, either way:
        Quick Start's straight-to-select (the select comes by itself in
        1-player drop-in mode: P2 and P3 join with X) or the classic menus
        (START, 2 PLAYER GAME). Expects port 2 connected and P3's test seat
        set ("ff.p3 seat FFFF") beforehand. Returns "drop-in" or "classic"."""
        f0 = self.frame()
        while self.frame() - f0 < timeout:
            if int.from_bytes(self.read(0x800927EC, 4), "little") == 0x80050798:
                break
            self.wait_frames(15)
        else:
            self.tap("START", frames=6, gap=450)
            self.tap("DOWN", frames=6, gap=40)
            self.tap("CROSS", frames=6, gap=400)
            return "classic"
        self.wait_frames(30)
        if int.from_bytes(self.read(0x80092784, 4), "little") == 1:
            self.cmd("set_input", buttons=pad("CROSS"), port=2, connected=1)
            self.wait_frames(4)
            self.cmd("set_input", buttons=0xFFFF, port=2, connected=1)
            self.wait_frames(12)
            self.cmd("mod_cmd", name="ff.p3", args=f"seat {pad('CROSS'):04X}")
            self.wait_frames(4)
            self.cmd("mod_cmd", name="ff.p3", args="seat FFFF")
            self.wait_frames(12)
        return "drop-in"

    def press(self, buttons: int, frames: int = 4, port: int = 1) -> dict:
        params = {"buttons": buttons, "frames": frames}
        if port == 2:
            params.update(port=2, connected=1)
        return self.cmd("press", **params)

    def tap(self, name: str, frames: int = 4, gap: int = 20,
            port: int = 1) -> None:
        """Press a button, then wait until it is released plus a gap.

        Port 2 uses set_input hold + neutral hold: a timed `press` on port 2
        unplugs the virtual pad when it expires (docs/TCP_COMMANDS.md), and
        the game then reports pad 2 as disconnected."""
        if port == 2:
            self.hold(pad(name), port=2)
            self.wait_frames(frames)
            self.hold(0xFFFF, port=2)
            self.wait_frames(gap)
            return
        self.press(pad(name), frames=frames, port=port)
        self.wait_frames(frames + gap)

    def hold(self, buttons: int, port: int = 1) -> dict:
        params = {"buttons": buttons}
        if port == 2:
            params.update(port=2, connected=1)
        return self.cmd("set_input", **params)

    def release(self, port: int | None = None) -> dict:
        return self.cmd("clear_input", **({"port": port} if port else {}))

    def shot(self, path: str) -> dict:
        return self.cmd("screenshot", path=path)

    def read(self, addr: int, length: int) -> bytes:
        r = self.cmd("read_ram", addr=f"0x{addr:08X}", len=length)
        return bytes.fromhex(r.get("hex", r.get("data", "")))


def _parse_value(v: str):
    try:
        return int(v, 0)
    except ValueError:
        return v


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    c = Client().connect(wait_s=5)
    op = argv[0]
    if op == "ping":
        print(json.dumps(c.cmd("ping")))
    elif op == "cmd":
        params = dict(a.split("=", 1) for a in argv[2:])
        print(json.dumps(c.cmd(argv[1], **{k: _parse_value(v) for k, v in params.items()})))
    elif op == "press":
        frames = int(argv[2]) if len(argv) > 2 else 4
        port = int(argv[3]) if len(argv) > 3 else 1
        print(json.dumps(c.press(pad(argv[1]), frames=frames, port=port)))
    elif op == "shot":
        print(json.dumps(c.shot(argv[1])))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
