#!/usr/bin/env python3
"""Copy one file out of the disc's ISO 9660 data track (read-only).

Standard library only (macOS / Windows).

    python3 tools/extract_disc_file.py disc/FightingForce.cue GAME.EXE disc/GAME.EXE

The output belongs in the gitignored disc/ folder: it is game data.
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def track1_bin(cue: Path) -> Path:
    text = cue.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'FILE\s+"([^"]+)"', text)
    if not m:
        raise SystemExit(f"no FILE line in {cue}")
    return cue.parent / m.group(1)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    cue, name, out = Path(argv[0]), argv[1].upper(), Path(argv[2])
    data = track1_bin(cue).read_bytes()

    def sector(lba: int) -> bytes:
        off = lba * 2352
        if data[off:off + 12] != SYNC:
            raise SystemExit(f"bad sync at LBA {lba} (expected MODE2/2352)")
        return data[off + 24:off + 24 + 2048]

    def read(lba: int, size: int) -> bytes:
        buf = b"".join(sector(lba + i) for i in range((size + 2047) // 2048))
        return buf[:size]

    pvd = sector(16)
    if pvd[1:6] != b"CD001":
        raise SystemExit("no ISO 9660 volume descriptor at LBA 16")
    ext, size = struct.unpack_from("<I", pvd, 158)[0], struct.unpack_from("<I", pvd, 166)[0]
    parts = name.replace("\\", "/").strip("/").split("/")
    for depth, part in enumerate(parts):
        raw = read(ext, size)
        found = None
        i = 0
        while i < len(raw):
            rl = raw[i]
            if rl == 0:
                i = (i // 2048 + 1) * 2048
                continue
            e, s = struct.unpack_from("<I", raw, i + 2)[0], struct.unpack_from("<I", raw, i + 10)[0]
            nl = raw[i + 32]
            entry = raw[i + 33:i + 33 + nl].decode("ascii", "replace").split(";")[0]
            if entry.upper() == part:
                found = (e, s, raw[i + 25] & 2)
                break
            i += rl
        if not found:
            raise SystemExit(f"{part} not found on the disc")
        ext, size, is_dir = found
        if depth < len(parts) - 1 and not is_dir:
            raise SystemExit(f"{part} is not a directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(read(ext, size))
    print(f"wrote {out} ({size} bytes, LBA {ext})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
