#!/usr/bin/env python3
"""Frame-time summary from the always-on frame_wall_ring.

    python3 tools/ff_perf.py [--last 1800]

Prints frame-time percentiles (ms) and interpreter instructions per frame
over the newest --last frame stamps. In a headless (unthrottled) run the
frame time is the real cost per frame; in a window it includes the wait for
the 60 Hz pace, so there the mean frame rate is the useful number.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=1800)
    args = ap.parse_args()
    c = Client()
    meta = c.cmd("frame_wall_ring", n=1)
    total = int(meta["total"])
    start = max(int(meta.get("oldest", 0)), total - args.last)
    rows: list[list[int]] = []
    seq = start
    while seq < total:
        r = c.cmd("frame_wall_ring", **{"from": seq, "n": 512})
        f = r.get("f", [])
        stride = int(r.get("stride", 6))
        got = [f[i:i + stride] for i in range(0, len(f), stride)]
        if not got:
            break
        rows.extend(got)
        seq += len(got)
    if len(rows) < 3:
        print("not enough frame stamps")
        return 1
    dts = []
    insns = []
    for a, b in zip(rows, rows[1:]):
        df = b[0] - a[0]
        if df <= 0:
            continue
        dts.append((b[1] - a[1]) / 1000.0 / df)
        insns.append(((b[2] - a[2]) & 0xFFFFFFFF) / df)
    dts.sort()
    n = len(dts)

    def pct(p: float) -> float:
        return dts[min(n - 1, int(p * n))]

    span_s = (rows[-1][1] - rows[0][1]) / 1e6
    frames = rows[-1][0] - rows[0][0]
    print(f"frames {rows[0][0]}..{rows[-1][0]}  ({frames} frames in {span_s:.1f} s "
          f"= {frames / span_s:.1f} fps)")
    print(f"frame ms: p50 {pct(0.5):.2f}  p90 {pct(0.9):.2f}  p99 {pct(0.99):.2f}  "
          f"max {dts[-1]:.2f}")
    print(f"interpreted insns/frame: mean {sum(insns) / len(insns):,.0f}  "
          f"max {max(insns):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
