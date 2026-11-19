#!/usr/bin/env python3
"""Compare the peers' state digests after a local netplay test.

Each peer of a rollback match logs "rb live dig local sim=N core=X ... mod=Y"
every 32 simulated frames: a digest of the whole emulated machine (core) and of
the plugin's state (mod) at frame N. A frame that every peer logged must have
the same digests everywhere; this lists any that differ. Rollback episodes
("rb begin" / "rb follow") are counted too.

    python3 tools/ff_net_digests.py OUTDIR [OUTDIR ...]     (OUTDIR holds peer0.log, peer1.log, ...)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DIG = re.compile(r"rb live dig local sim=(\d+) core=([0-9a-f]+).*?mod=([0-9a-f]+)")
SECTIONS = re.compile(r"\b(cpu|clk|tim|ram|dirty|mod|av|cd|spu|mdec|aux)=([0-9a-f]+)")
RB = re.compile(r"rb (?:begin|follow) .*?load=(\d+)")


def digests(log: Path, sections: dict | None = None) -> dict[int, tuple[str, str]]:
    """Digests of frames that stayed final: a frame simulated on predicted
    input and then replayed by a rollback (load <= frame) is dropped, unless
    the replay logged it again. With `sections`, also fills
    {frame: {section: hash}} for the same frames."""
    res: dict[int, tuple[str, str]] = {}
    for line in log.read_text(errors="replace").splitlines():
        m = DIG.search(line)
        if m:
            res[int(m.group(1))] = (m.group(2), m.group(3))
            if sections is not None:
                sections[int(m.group(1))] = dict(SECTIONS.findall(line))
            continue
        r = RB.search(line)
        if r:
            load = int(r.group(1))
            for n in [n for n in res if n >= load]:
                del res[n]
                if sections is not None:
                    sections.pop(n, None)
    return res


def main(argv: list[str]) -> int:
    bad_total = 0
    for d in argv:
        out = Path(d)
        logs = sorted(out.glob("peer*.log"))
        if len(logs) < 2:
            print(f"{out}: fewer than two peer logs")
            continue
        secs = [dict() for _ in logs]
        per = [digests(p, secs[i]) for i, p in enumerate(logs)]
        common = sorted(set.intersection(*(set(x) for x in per)))
        bad = [n for n in common if len({x[n] for x in per}) > 1]
        rb = [sum(1 for l in p.read_text(errors="replace").splitlines() if "rb begin" in l or "rb follow" in l)
              for p in logs]
        span = f"frames {common[0]}..{common[-1]}" if common else "no common frames"
        print(f"{out}: {len(common)} digest points compared ({span}), {len(bad)} differ; rollback lines {rb}")
        for n in bad[:5]:
            print(f"   frame {n}: " + " | ".join(f"core {x[n][0]} mod {x[n][1]}" for x in per))
        if bad:
            # which sections differ, and which peer is the odd one out
            tally: dict[str, int] = {}
            odd: dict[int, int] = {}
            for n in bad:
                rows = [secs[i].get(n, {}) for i in range(len(logs))]
                for sec in sorted(set().union(*rows)):
                    vals = [r.get(sec) for r in rows]
                    if len(set(vals)) > 1:
                        tally[sec] = tally.get(sec, 0) + 1
                        for i, v in enumerate(vals):
                            if vals.count(v) == 1:
                                odd[i] = odd.get(i, 0) + 1
            print(f"   sections that differ (frames): {tally}; odd peer out (section-frames): {odd}")
        bad_total += len(bad)
    return 1 if bad_total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
