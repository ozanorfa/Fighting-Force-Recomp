#!/usr/bin/env python3
"""TEST ONLY: Quick Start "straight to fighter select" and drop-in joining,
offline. One process per scenario (debug server; P2 = port 2, P3 = the ff.p3
seat override). Each scenario boots without pressing START and checks that
the front end goes to the select screen on its own, then:

  solo    P1 confirms alone             -> a 1-player game, no P3
  drop3   P2 and P3 join with X, all confirm -> a 3-player game (P3 spawned)
  p2only  P2 joins, P3's seat is connected but never joins -> 2 players, no P3
  leave   P2 and P3 join, then leave with TRIANGLE -> back to 1 player
  menu    P1's TRIANGLE -> the original main menu; "2 PLAYER GAME" there
          works as on the PS1 (P3 then takes its cursor at once)
  seatlost  classic select (as "menu"), P3's controller goes: its cursor
          goes (it comes back with the controller); P1 and P2 start a
          2-player game (the select used to wait for P3)
  p2hold  P2 joins, holds SELECT+START 10 frames: the game's soft-reset hold
          counter for P2's port counts once per frame, as without the
          plugin (the drop-in's own read of P2's pad made it 1.5)

    python3 tools/ff_p3_lobby.py --exe build-net/FightingForce_Recompiled --out DIR [--scenarios solo,drop3,...]
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

R = 0x800B4318
G_MENU_STATE, FN_SELECT, FN_MAIN_MENU = 0x800927EC, 0x80050798, 0x80050270
G_SEL_PLAYERS, G_SEL_MASK, G_PLAYERS, G_LEVEL = 0x80092784, 0x8009272C, 0x800940B0, 0x80093D58
SCENARIOS = ["solo", "drop3", "p2only", "leave", "menu", "seatlost", "p2hold"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--port", type=int, default=4391)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    results = []

    for name in args.scenarios.split(","):
        log = open(out / f"run_{name}.log", "w")
        proc = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game, "--disc", args.disc,
                                 "--memcard-dir", str(out / "cards"), "--debug-port", str(args.port)],
                                stdout=log, stderr=subprocess.STDOUT, env=env)
        c = Client(port=args.port).connect(wait_s=90)

        def w(a):
            return struct.unpack("<I", c.read(a, 4))[0]

        def p3(s):
            return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

        def p3_tap(btn, n=1):
            for _ in range(n):
                p3(f"seat {pad(btn):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(10)

        def p2_tap(btn, n=1):
            for _ in range(n):
                c.cmd("set_input", buttons=pad(btn), port=2, connected=1); c.wait_frames(4)
                c.cmd("set_input", buttons=0xFFFF, port=2, connected=1); c.wait_frames(10)

        def p1_tap(btn, n=1):
            for _ in range(n):
                c.tap(btn, frames=4, gap=10)

        def sel():
            return dict(state=w(G_MENU_STATE), n=w(G_SEL_PLAYERS), mask=w(G_SEL_MASK),
                        p3=p3("show").split("sel_active=")[1].split()[0])

        def wait_level(limit=6000):
            f0 = c.frame()
            while c.frame() - f0 < limit:
                if w(G_MENU_STATE) not in (FN_SELECT, FN_MAIN_MENU) and w(G_LEVEL) <= 0x19:
                    s = p3("show")
                    if "passes=" in s and (int(s.split("passes=")[1].split()[0]) > 0 or c.frame() - f0 > 2500):
                        break
                c.wait_frames(30)
            c.wait_frames(120)
            b = c.read(R, 3 * 0xF4)
            types = [struct.unpack_from("<h", b, i * 0xF4 + 0x34)[0] for i in range(3)]
            return dict(level=w(G_LEVEL), players=w(G_PLAYERS), types=types,
                        p3_active=re.search(r"(?<![a-z_])active=1", p3("show")) is not None)

        r = dict(scenario=name, ok=False, notes=[])
        try:
            p3("seat FFFF")                      # P3's seat connected, no buttons
            c.hold(0xFFFF, port=2)              # P2's pad connected
            f0 = c.frame()
            while c.frame() - f0 < 6000 and w(G_MENU_STATE) != FN_SELECT:
                c.wait_frames(15)
            r["select_at_frame"] = c.frame()
            r["qs"] = c.cmd("mod_cmd", name="ff.qs", args="show").get("reply")
            c.wait_frames(60)
            s = sel()
            r["select"] = s
            c.shot(str(out / f"{name}_1_select.png"))
            if s["state"] != FN_SELECT or s["n"] != 1:
                r["notes"].append("did not open on a 1-player select")
                raise RuntimeError("no select")
            if name == "solo":
                p1_tap("CROSS")
                lv = wait_level()
                r["level"] = lv
                r["ok"] = lv["players"] == 1 and not lv["p3_active"]
            elif name in ("drop3", "p2only"):
                p2_tap("CROSS")
                s2 = sel(); r["after_p2"] = s2
                c.shot(str(out / f"{name}_2_p2_in.png"))
                if name == "drop3":
                    p3_tap("CROSS")
                    s3 = sel(); r["after_p3"] = s3
                    p3_tap("RIGHT")
                    c.shot(str(out / f"{name}_3_p3_in.png"))
                    p3_tap("CROSS")
                p2_tap("CROSS")
                p1_tap("CROSS")
                c.wait_frames(20)
                c.shot(str(out / f"{name}_4_all_in.png"))
                lv = wait_level()
                r["level"] = lv
                if name == "drop3":
                    r["ok"] = s2["n"] == 2 and lv["players"] == 2 and 0 <= lv["types"][2] <= 3 and lv["p3_active"]
                else:
                    r["ok"] = s2["n"] == 2 and lv["players"] == 2 and not lv["p3_active"]
            elif name == "leave":
                p2_tap("CROSS"); p3_tap("CROSS")
                s2 = sel(); r["both_in"] = s2
                c.shot(str(out / f"{name}_2_both_in.png"))
                p3_tap("TRIANGLE")
                s3 = sel(); r["p3_left"] = s3
                p3_tap("CROSS")                  # P3 back in
                p2_tap("TRIANGLE")               # P2 leaves: P3 goes too (P3 needs P2)
                s4 = sel(); r["p2_left"] = s4
                c.shot(str(out / f"{name}_3_p2_left.png"))
                p1_tap("CROSS")
                lv = wait_level()
                r["level"] = lv
                r["ok"] = (s2["n"] == 2 and s2["p3"] == "1" and s3["p3"] == "0" and s4["n"] == 1
                           and s4["p3"] == "0" and lv["players"] == 1 and not lv["p3_active"])
            elif name == "menu":
                p1_tap("TRIANGLE")
                c.wait_frames(30)
                m = sel(); r["menu"] = m
                c.shot(str(out / f"{name}_2_menu.png"))
                p1_tap("DOWN")                   # 1 PLAYER -> 2 PLAYER GAME
                p1_tap("CROSS")
                c.wait_frames(20)
                s2 = sel(); r["classic_select"] = s2
                c.shot(str(out / f"{name}_3_classic.png"))
                p3_tap("CROSS"); p2_tap("CROSS"); p1_tap("CROSS")
                lv = wait_level()
                r["level"] = lv
                r["ok"] = (m["state"] == FN_MAIN_MENU and s2["state"] == FN_SELECT and s2["n"] == 2
                           and s2["p3"] == "1" and lv["players"] == 2 and lv["p3_active"])
            elif name == "seatlost":
                p1_tap("TRIANGLE"); c.wait_frames(30)
                p1_tap("DOWN"); p1_tap("CROSS"); c.wait_frames(20)     # classic 2 PLAYER GAME select
                s2 = sel(); r["classic_select"] = s2
                p3("seat off"); c.wait_frames(10)                     # P3's controller gone
                s3 = sel(); r["seat_off"] = s3
                p3("seat FFFF"); c.wait_frames(10)                    # back: a cursor again
                s4 = sel(); r["seat_back"] = s4
                p3("seat off"); c.wait_frames(10)
                c.shot(str(out / f"{name}_3_seat_off.png"))
                p2_tap("CROSS"); p1_tap("CROSS")
                lv = wait_level()
                r["level"] = lv
                r["ok"] = (s2["state"] == FN_SELECT and s2["p3"] == "1" and s3["p3"] == "0" and s4["p3"] == "1"
                           and lv["players"] == 2 and not lv["p3_active"])
            elif name == "p2hold":
                # P2's Select+Start soft-reset hold (FUN_8005f5ac, s16 0x80093A18[port]): the select
                # runs every second frame and reads P2's pad twice per run (4 FUN_8005f5ac calls per
                # run with P1's), so the hold counts once per frame; the drop-in's own extra read made
                # it 1.5 per frame (A/B 2026-11-14: 18 counts in 12 frames, 5 calls per run).
                p2_tap("CROSS")
                s2 = sel(); r["after_p2"] = s2
                port = w(0x80094288 + 4)
                c.cmd("set_input", buttons=pad("SELECT") & pad("START"), port=2, connected=1)
                c.wait_frames(2)
                a0, g0, f0 = struct.unpack("<h", c.read(0x80093A18 + 2 * port, 2))[0], w(0x80093DD0), c.frame()
                c.wait_frames(10)
                a1, g1, f1 = struct.unpack("<h", c.read(0x80093A18 + 2 * port, 2))[0], w(0x80093DD0), c.frame()
                c.cmd("set_input", buttons=0xFFFF, port=2, connected=1)
                c.wait_frames(10)
                r["hold"] = dict(port=port, counts=a1 - a0, ticks=g1 - g0, frames=f1 - f0)
                s3 = sel(); r["after_hold"] = s3
                p2_tap("CROSS"); p1_tap("CROSS")
                lv = wait_level()
                r["level"] = lv
                r["ok"] = (s2["n"] == 2 and f1 - f0 >= 8 and abs((a1 - a0) - (f1 - f0)) <= 1
                           and s3["state"] == FN_SELECT and lv["players"] == 2)
            c.shot(str(out / f"{name}_5_level.png"))
            r["counters"] = {k: v for k, v in c.cmd("counters").items() if isinstance(k, str) and "ff.p3" in k} \
                if isinstance(c.cmd("counters"), dict) else {}
        except Exception as e:  # noqa: BLE001
            r["notes"].append(f"{type(e).__name__}: {e}")
        finally:
            try:
                c.cmd("quit")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
            if proc.poll() is None:
                proc.terminate()
            log.close()
        print(r, flush=True)
        results.append(r)
        time.sleep(3)
    print("SUMMARY", flush=True)
    for r in results:
        print(f"  {r['scenario']:7s} {'OK  ' if r['ok'] else 'FAIL'} select at frame {r.get('select_at_frame')} "
              f"level {r.get('level')} {r['notes']}", flush=True)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
