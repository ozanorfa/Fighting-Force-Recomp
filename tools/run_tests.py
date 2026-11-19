#!/usr/bin/env python3
"""One command for the game tests (TEST ONLY), in tiers.

    python3 tools/run_tests.py                    # quick tier (~35 min)
    python3 tools/run_tests.py --tier full        # quick + the long ones (hours)
    python3 tools/run_tests.py -k lobby -k net    # tests whose name contains a pattern
    python3 tools/run_tests.py --list

Every test drives the real game through the debug server with one of the
scenario tools in tools/ and passes when the tool exits 0 (and its follow-up
checks, such as the per-frame digest comparison, do too). Each test runs from
a folder of its own (links to the build, copies of what the game writes, its
own memory cards) with the SHIPPED default mod selection -- no state.toml (the
friendly-fire-off tests write the launcher's selection for that) --
and no launcher settings (settings.toml: devices, names) from this PC,
so a test sees what a fresh install sees, and nothing a test or the launcher
writes reaches the build folder (games started from one folder race on
shared files there). Tests run one after another;
on macOS under `caffeinate -d -i -u`: the display must stay on, because
the launcher draws its hidden window only while it is (with the display
asleep the LAN-room test never got past the launcher, 2026-11-17); -u
wakes a display that is already off. A closed lid still sleeps the Mac.

Needs: a built build-net (tools/ff_build.py) and disc/FightingForce.cue.
Results: <out>/<test>/ (tool output in <test>.log) and a summary table.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ff_lanroom  # noqa: E402  (make_peer: a run folder of its own)

PY = sys.executable
LAG = {"RNET_SIM_LATENCY_MS": "30", "RNET_SIM_JITTER_MS": "10", "RNET_SIM_LOSS_PCT": "3"}
DIGESTS = [PY, "tools/ff_net_digests.py", "{out}"]
CARD = ROOT.parent / "screenshots" / "phase7" / "card2" / "cards" / "card1.mcd"  # a save with P3 (local test data)
# The launcher's mod selection with Quick Start's "Friendly fire" off (mods/state.toml, as the launcher writes it).
FF_OFF = """format_version = 2

[[package]]
id = "ff.quick-start"
version = "0.1.0"

[[feature]]
package_id = "ff.quick-start"
id = "quick-start"
enabled = true

[feature.values]
friendly_fire = "false"

[[package]]
id = "ff.third-player"
version = "0.1.0"

[[feature]]
package_id = "ff.third-player"
id = "p3"
enabled = true
"""

TESTS = [
    # ---- quick ----------------------------------------------------------
    dict(name="lobby", tier="quick",
         what="straight to fighter select + drop-in: solo, drop3, p2only, leave, menu, seatlost, p2hold",
         cmd=[PY, "tools/ff_p3_lobby.py", "--exe", "{exe}", "--out", "{out}"]),
    dict(name="pad_polls", tier="quick",
         what="SIO trace boot -> select: no 0x43..0x4F, port 1 polled >= 0.95 per frame",
         cmd=[PY, "tools/ff_pad_cmds.py", "--exe", "{exe}", "--out", "{out}", "--until", "1700"]),
    dict(name="short_press", tier="quick",
         what="presses of 2..12 frames reach the select (P2 joins, P1's cursor moves)",
         cmd=[PY, "tools/ff_press_probe.py", "--exe", "{exe}", "--out", "{out}"]),
    dict(name="ff_punch", tier="quick",
         what="friendly fire, all six pairs, target placed on the swing point",
         cmd=[PY, "tools/ff_p3_ff_moves.py", "--exe", "{exe}", "--out", "{out}", "--moves", "punch",
              "--tries", "3", "--min-hits", "3"]),
    dict(name="ff_crowd", tier="quick",
         what="friendly fire with an enemy on the target's spot (players hit first)",
         cmd=[PY, "tools/ff_p3_ff_moves.py", "--exe", "{exe}", "--out", "{out}", "--moves", "punch",
              "--tries", "4", "--crowd", "--min-hits", "1"]),
    dict(name="ff_off_punch", tier="quick", state=FF_OFF,
         what="friendly fire off: no pair hits; with an enemy on the spot the enemy is hit",
         cmd=[PY, "tools/ff_p3_ff_moves.py", "--exe", "{exe}", "--out", "{out}", "--moves", "punch",
              "--tries", "3", "--crowd", "--max-hits", "0"]),
    dict(name="ff_shots", tier="quick",
         what="gun shots, friendly fire on: P1's and P3's shots hit the friend in front of the enemy",
         cmd=[PY, "tools/ff_ff_shots.py", "--exe", "{exe}", "--out", "{out}/p1", "--shooter", "1",
              "--tries", "3", "--expect", "on"],
         then=[[PY, "tools/ff_ff_shots.py", "--exe", "{exe}", "--out", "{out}/p3", "--shooter", "3",
                "--tries", "3", "--expect", "on"]]),
    dict(name="ff_off_shots", tier="quick", state=FF_OFF,
         what="gun shots, friendly fire off: P1's and P3's shots fly through friends into the enemy",
         cmd=[PY, "tools/ff_ff_shots.py", "--exe", "{exe}", "--out", "{out}/p1", "--shooter", "1",
              "--tries", "3", "--expect", "off"],
         then=[[PY, "tools/ff_ff_shots.py", "--exe", "{exe}", "--out", "{out}/p3", "--shooter", "3",
                "--tries", "3", "--expect", "off"]]),
    dict(name="waves_lv17", tier="quick",
         what="level 0x11 waves on autopilot (it once crashed spawning a wave)",
         cmd=[PY, "tools/ff_p3_waves.py", "--exe", "{exe}", "--out", "{out}", "--levels", "17",
              "--fight", "6000"]),
    dict(name="downcarry", tier="quick",
         what="P3's down state across level starts: not frozen after a downed P2, no free revive, "
              "a continue during the level end",
         cmd=[PY, "tools/ff_p3_downcarry.py", "--exe", "{exe}", "--out", "{out}"]),
    dict(name="p3_textures", tier="quick",
         what="P3's fighter textures in a level: all four fighters as P3, only the three played fighters loaded",
         cmd=[PY, "tools/ff_p3_textures.py", "--exe", "{exe}", "--out", "{out}"]),
    dict(name="p3_textures_levels", tier="full",
         what="P3's fighter textures in every level (Hawk, Mace, Smasher): loads, VRAM room, keeps running",
         cmd=[PY, "tools/ff_p3_textures.py", "--exe", "{exe}", "--out", "{out}", "--levels", "0-24"]),
    dict(name="gift_pause", tier="quick",
         what="P3's next-level weapon gift (not when down) and its START pause: its own menu, "
              "P1 cannot end it, P3 alone, QUIT",
         cmd=[PY, "tools/ff_p3_gift_pause.py", "--exe", "{exe}", "--out", "{out}"]),
    dict(name="net3_lag", tier="quick",
         what="3 peers, 30 ms +-10 ms, 3 % loss, rollback, autopilot, then P3's START pause; records + digests",
         cmd=[PY, "tools/ff_net_local.py", "--exe", "{exe}", "--out", "{out}", "--autopilot", "--fight", "3000",
              "--stagger", "3", "--net-mode", "rollback", "--port", "7810", "--p3-pause"],
         env=LAG, then=[DIGESTS]),
    dict(name="lanroom", tier="quick", own_folders=True,
         what="the launcher's LAN room, scripted: 3 seated, PLAY, select, level; digests",
         cmd=[PY, "tools/ff_lanroom.py", "--build", "{build}", "--out", "{out}", "--room-port", "7792", "--fresh"],
         then=[DIGESTS]),
    # ---- full -------------------------------------------------------------
    dict(name="waves_all", tier="full",
         what="every level's waves (bosses included), 15,000 frames each",
         cmd=[PY, "tools/ff_p3_waves.py", "--exe", "{exe}", "--out", "{out}", "--levels", "0-24",
              "--fight", "15000"]),
    dict(name="net_soak", tier="full",
         what="3-peer soak: 18,000 frames with lag, jitter, loss; digests",
         cmd=[PY, "tools/ff_net_local.py", "--exe", "{exe}", "--out", "{out}", "--autopilot", "--fight", "18000",
              "--stagger", "3", "--net-mode", "rollback", "--port", "7810"],
         env=LAG, then=[DIGESTS]),
    dict(name="net_card", tier="full", needs=CARD,
         what="continue a card save in a match (select -> TRIANGLE -> OPTIONS -> LOAD)",
         cmd=[PY, "tools/ff_net_local.py", "--exe", "{exe}", "--out", "{out}", "--load-card", "--host-card",
              str(CARD), "--fight", "600", "--stagger", "3", "--net-mode", "rollback", "--port", "7810"],
         then=[DIGESTS]),
    dict(name="ending", tier="full",
         what="the last level, the ending (P3 out of record 2 in the cutscenes), P3's high-score entry",
         cmd=[PY, "tools/ff_p3_ending.py", "--exe", "{exe}", "--out", "{out}"]),
    dict(name="ff_off_throw", tier="full", state=FF_OFF,
         what="thrown object, friendly fire off: P1 -> P3 and P3 -> P1 fly through into the enemy",
         cmd=[PY, "tools/ff_ff_shots.py", "--exe", "{exe}", "--out", "{out}/p1", "--weapon", "throw",
              "--shooter", "1", "--friend", "3", "--tries", "1", "--expect", "off"],
         then=[[PY, "tools/ff_ff_shots.py", "--exe", "{exe}", "--out", "{out}/p3", "--weapon", "throw",
                "--shooter", "3", "--friend", "1", "--tries", "1", "--expect", "off"]]),
    dict(name="ff_moves", tier="full",
         what="friendly fire, every move (report; some moves vary for the stock pairs too)",
         cmd=[PY, "tools/ff_p3_ff_moves.py", "--exe", "{exe}", "--out", "{out}", "--tries", "4"]),
]


def run(cmd: list[str], log, env: dict) -> int:
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        cmd = ["caffeinate", "-d", "-i", "-u", *cmd]
    log.write("\n$ " + " ".join(cmd) + "\n")
    log.flush()
    return subprocess.call(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", choices=["quick", "full"], default="quick")
    ap.add_argument("-k", action="append", default=[], help="run tests whose name contains this (repeatable)")
    ap.add_argument("--build", default="build-net")
    ap.add_argument("--out", default=str(ROOT.parent / "test_runs" / time.strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    tests = [t for t in TESTS if args.tier == "full" or t["tier"] == "quick"]
    if args.k:
        tests = [t for t in TESTS if any(k in t["name"] for k in args.k)]
    if args.list:
        for t in TESTS:
            print(f"  {t['name']:11s} {t['tier']:5s}  {t['what']}")
        return 0
    build = (ROOT / args.build).resolve()
    if not (build / "FightingForce_Recompiled").exists() and not (build / "FightingForce_Recompiled.exe").exists():
        print(f"no game in {build}: build it first (tools/ff_build.py)")
        return 2
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for t in tests:
        tdir = out / t["name"]
        if tdir.exists():
            shutil.rmtree(tdir)
        tdir.mkdir(parents=True)
        if t.get("needs") is not None and not Path(t["needs"]).exists():
            results.append((t["name"], "SKIP", 0.0, f"needs {t['needs']}"))
            print(f"  {t['name']:11s} SKIP  (needs {t['needs']})", flush=True)
            continue
        exe = ""
        if not t.get("own_folders"):
            run_dir = tdir / "_run"
            ff_lanroom.make_peer(build, run_dir)
            (run_dir / "mods" / "state.toml").unlink()       # the shipped defaults ...
            if t.get("state"):                                 # ... or a launcher selection of the test's
                (run_dir / "mods" / "state.toml").write_text(t["state"])
            (run_dir / "settings.toml").unlink(missing_ok=True)  # no launcher settings of this PC
            exe = str(run_dir / "FightingForce_Recompiled")
        fill = lambda a: a.format(exe=exe, out=str(tdir), build=str(build))  # noqa: E731
        env = dict(os.environ, **t.get("env", {}))
        print(f"  {t['name']:11s} ...   {t['what']}", flush=True)
        t0 = time.time()
        with open(out / f"{t['name']}.log", "w") as log:
            rc = run([fill(a) for a in t["cmd"]], log, env)
            for follow in t.get("then", []):
                if rc == 0:
                    rc = run([fill(a) for a in follow], log, os.environ.copy())
        dt = time.time() - t0
        results.append((t["name"], "PASS" if rc == 0 else "FAIL", dt, "" if rc == 0 else f"exit {rc}"))
        print(f"  {t['name']:11s} {'PASS' if rc == 0 else 'FAIL'}  {dt / 60:.1f} min", flush=True)
        time.sleep(3)
    print(f"\nSUMMARY ({args.tier} tier, results in {out})")
    for name, res, dt, note in results:
        print(f"  {res:4s}  {name:11s} {dt / 60:5.1f} min  {note}")
    return 0 if all(r != "FAIL" for _, r, _, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
