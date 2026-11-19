#!/usr/bin/env python3
"""Make FightingForce.ico from the player's own game: the title screen's logo.

Run once by tools/ff_setup.py after a build (Setup.bat then points the
desktop shortcut at it). Nothing from the game ships in the ZIP: the icon is
made on each PC from that player's disc.

1. Starts the built game headless with the debug server on 127.0.0.1 (a free
   port; a temporary memory-card folder), no window.
2. Asks Quick Start to stay on the title for this run (debug command
   "ff.qs hold-title on"; the player's mod settings are not touched) and
   waits until the title has polled the pad 150 times (the logo has faded in
   after ~60).
3. Takes a screenshot, crops the logo, pads it to a square (transparent),
   box-scales it to 256 / 48 / 32 / 16 and writes an .ico (PNG at 256, 32-bit
   DIB below), then quits the game.

    python tools/ff_icon.py [--exe build-net/FightingForce_Recompiled(.exe)] [--out FightingForce.ico]

Exit 0 on success; anything else leaves the default icon. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ffdbg import Client  # noqa: E402

EXE = "FightingForce_Recompiled.exe" if os.name == "nt" else "FightingForce_Recompiled"
# The logo on the 320x240 title screen (FIGHTING / FORCE and the emblem).
CROP = (34, 28, 282, 156)          # x0, y0, x1, y1 (exclusive); the logo is x 41-274, y 43-143
SIZES = (256, 48, 32, 16)
TITLE_POLLS = 150


# ---- PNG in / out -----------------------------------------------------------
def png_decode(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError(f"unsupported PNG (depth {depth}, type {ctype}, interlace {interlace})")
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        f, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x:x + bpp]) + ((255,) if bpp == 3 else ()) for x in range(0, stride, bpp)])
        prev = line
    return w, h, rows


def png_encode(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    h, w = len(px), len(px[0])
    raw = b"".join(b"\0" + bytes(v for p in row for v in p) for row in px)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# ---- picture ----------------------------------------------------------------
def square_logo(rows: list[list[tuple[int, int, int, int]]]) -> list[list[tuple[int, int, int, int]]]:
    x0, y0, x1, y1 = CROP
    crop = [row[x0:x1] for row in rows[y0:y1]]
    w, h = x1 - x0, y1 - y0
    side = max(w, h)
    clear = (0, 0, 0, 0)
    top = (side - h) // 2
    left = (side - w) // 2
    out = [[clear] * side for _ in range(side)]
    for y in range(h):
        out[top + y][left:left + w] = crop[y]
    return out


def box_scale(px: list[list[tuple[int, int, int, int]]], n: int) -> list[list[tuple[int, int, int, int]]]:
    """Area average with premultiplied alpha (no dark fringes at the edges)."""
    side = len(px)
    out = []
    for oy in range(n):
        sy0, sy1 = oy * side / n, (oy + 1) * side / n
        row = []
        for ox in range(n):
            sx0, sx1 = ox * side / n, (ox + 1) * side / n
            acc = [0.0, 0.0, 0.0, 0.0]
            area = 0.0
            for y in range(int(sy0), min(side, int(sy1 + 0.999))):
                wy = min(sy1, y + 1) - max(sy0, y)
                for x in range(int(sx0), min(side, int(sx1 + 0.999))):
                    wgt = wy * (min(sx1, x + 1) - max(sx0, x))
                    r, g, b, a = px[y][x]
                    al = a / 255.0
                    acc[0] += r * al * wgt
                    acc[1] += g * al * wgt
                    acc[2] += b * al * wgt
                    acc[3] += al * wgt
                    area += wgt
            al = acc[3] / area if area else 0.0
            if al > 0:
                row.append((round(acc[0] / acc[3]), round(acc[1] / acc[3]), round(acc[2] / acc[3]), round(al * 255)))
            else:
                row.append((0, 0, 0, 0))
        out.append(row)
    return out


def dib_entry(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    n = len(px)
    header = struct.pack("<IiiHHIIiiII", 40, n, 2 * n, 1, 32, 0, n * n * 4, 0, 0, 0, 0)
    colour = b"".join(bytes((b, g, r, a)) for row in reversed(px) for (r, g, b, a) in row)
    mask_stride = ((n + 31) // 32) * 4
    mask = bytearray()
    for row in reversed(px):
        bits = bytearray(mask_stride)
        for x, (_, _, _, a) in enumerate(row):
            if a == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return header + colour + bytes(mask)


def write_ico(path: Path, square: list[list[tuple[int, int, int, int]]]) -> None:
    images = []
    for n in SIZES:
        px = box_scale(square, n)
        images.append((n, png_encode(px) if n >= 256 else dib_entry(px)))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for n, blob in images:
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    tmp = path.with_suffix(".ico.tmp")
    tmp.write_bytes(head + entries + blobs)
    os.replace(tmp, path)


# ---- the game ---------------------------------------------------------------
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def qs(c: Client, args: str) -> str:
    return str(c.cmd("mod_cmd", name="ff.qs", args=args).get("reply", ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default=str(ROOT / "build-net" / EXE))
    ap.add_argument("--game", default=str(ROOT / "game_3p.toml"))
    ap.add_argument("--disc", default=str(ROOT / "disc" / "FightingForce.cue"))
    ap.add_argument("--out", default=str(ROOT / "FightingForce.ico"))
    ap.add_argument("--keep-png", default="", help="also keep the title screenshot here (test)")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="seconds to reach the title after connecting (ff_setup.py waits 360 in all)")
    args = ap.parse_args()
    exe = Path(args.exe)
    if not exe.is_file():
        print(f"icon: no game at {exe}")
        return 2
    work = Path(tempfile.mkdtemp(prefix="ff-icon-"))
    port = free_port()
    env = dict(os.environ, PSX_DEBUG_SERVER="1", PSX_OVERLAY_AUTOCOMPILE_OFF="1")
    flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
    log = open(work / "game.log", "w")
    proc = subprocess.Popen([str(exe), "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                             "--memcard-dir", str(work / "cards"), "--debug-port", str(port)],
                            cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT, env=env,
                            creationflags=flags)
    c = None
    try:
        c = Client(port=port).connect(wait_s=60)
        hold = qs(c, "hold-title on")
        if "hold_title=1" not in hold:
            print("icon: this build cannot stay on the title (old Quick Start plugin)")
            return 3
        deadline = time.monotonic() + args.timeout
        while True:
            reply = qs(c, "show")
            polls = int((re.search(r"title_polls=(\d+)", reply) or [0, 0])[1])
            skips = int((re.search(r"title_skips=(\d+)", reply) or [0, 0])[1])
            if skips:
                print("icon: the title was skipped before the hold took effect")
                return 4
            if polls >= TITLE_POLLS:
                break
            if time.monotonic() > deadline or proc.poll() is not None:
                print(f"icon: no title screen (title polls {polls})")
                return 5
            c.wait_frames(10)
        shot = work / "title.png"
        c.shot(str(shot))
        for _ in range(50):
            if shot.is_file() and shot.stat().st_size > 0:
                break
            time.sleep(0.1)
        w, h, rows = png_decode(shot.read_bytes())
        if (w, h) != (320, 240):
            print(f"icon: unexpected screen size {w}x{h}")
            return 6
        if args.keep_png:
            shutil.copy2(shot, args.keep_png)
        write_ico(Path(args.out), square_logo(rows))
        print(f"icon: wrote {args.out}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"icon: {type(e).__name__}: {e}")
        return 1
    finally:
        if c is not None:
            try:
                c.cmd("quit")
            except Exception:  # noqa: BLE001
                pass
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
