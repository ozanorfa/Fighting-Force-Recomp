#!/usr/bin/env python3
"""Start a Direct IP netplay match (LAN, or a virtual LAN such as Radmin VPN).

Standard library only (macOS / Windows). Every player runs the SAME build
(same commit, built with -DFF_NETPLAY=ON) and the same disc dump.

    # the host (player 1); ADDRESS is the host's LAN / Radmin VPN address
    python3 tools/ff_netplay.py host --listen ADDRESS:7777

    # player 2 and player 3
    python3 tools/ff_netplay.py join --slot 1 --host ADDRESS:7777
    python3 tools/ff_netplay.py join --slot 2 --host ADDRESS:7777

Options: --players 2|3 (default 3; every peer must use the same number),
--exe (default build-net/FightingForce_Recompiled[.exe]), --disc,
--delay N (starting input delay in frames), --mode rollback|delay,
--fixed-delay N (rollback, 5..20: keep the input delay at N frames instead of
letting the runtime raise it from how late inputs arrive; less input lag,
more corrections on screen; every player must use the same N),
--log FILE (the game's messages go to FILE instead of this window; the
Windows start files use logs\\last_run.txt).

A command-line session takes its seat count from PSX_NET_SLOTS (the runtime
does not take it from game.toml there), so this sets it for you. The third
player's fighter needs the third-player mod; a netplay build forces it on
every peer through the plugin's netplay profile.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_exe() -> Path:
    name = "FightingForce_Recompiled.exe" if os.name == "nt" else "FightingForce_Recompiled"
    return ROOT / "build-net" / name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("role", choices=["host", "join"])
    ap.add_argument("--listen", default="0.0.0.0:7777", help="host: address:port to listen on")
    ap.add_argument("--host", help="join: the host's address:port")
    ap.add_argument("--slot", type=int, default=1, help="join: your seat (1 = P2, 2 = P3)")
    ap.add_argument("--bind", default="", help="join: local address:port (default: any address, port 7777+slot)")
    ap.add_argument("--players", type=int, default=3, choices=[2, 3])
    ap.add_argument("--exe", default=str(default_exe()))
    ap.add_argument("--game", default=str(ROOT / "game_3p.toml"))
    ap.add_argument("--disc", default=str(ROOT / "disc" / "FightingForce.cue"))
    ap.add_argument("--saves", default=str(ROOT / "saves"),
                    help="memory cards and savestates (the host's card holds the campaign; "
                         "guests' match copy is <saves>/netplay; see tools/ff_match_save.py)")
    ap.add_argument("--delay", type=int, default=-1, help="input delay in frames (default: runtime's)")
    ap.add_argument("--mode", choices=["rollback", "delay"], default="rollback")
    ap.add_argument("--log", default="", help="write the game's messages (stdout and stderr) to this file")
    ap.add_argument("--fixed-delay", type=int, default=-1,
                    help="rollback: fixed input delay, 5..20 frames (turns the automatic delay off)")
    args = ap.parse_args()

    if args.role == "join" and not args.host:
        ap.error("join needs --host ADDRESS:PORT")
    if args.fixed_delay >= 0 and (args.mode != "rollback" or not 5 <= args.fixed_delay <= 20):
        ap.error("--fixed-delay needs --mode rollback and a value from 5 to 20 (rollback's minimum is 5)")
    if args.role == "join" and not 1 <= args.slot < args.players:
        ap.error(f"--slot must be 1..{args.players - 1}")
    exe = Path(args.exe)
    if not exe.exists():
        ap.error(f"no netplay build at {exe} (configure with -DFF_NETPLAY=ON)")

    Path(args.saves).mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "--no-launcher", "--game", args.game, "--disc", args.disc, "--memcard-dir", args.saves]
    if args.role == "host":
        cmd += ["--net-slot", "0", "--net-bind", args.listen]
    else:
        bind = args.bind or f"0.0.0.0:{7777 + args.slot}"
        cmd += ["--net-slot", str(args.slot), "--net-bind", bind, "--net-peer", args.host]
    env = dict(os.environ, PSX_NET_SLOTS=str(args.players), PSX_NET_MODE=args.mode)
    env.setdefault("PSX_OVERLAY_AUTOCOMPILE_OFF", "1")  # as in every test: no runtime compiler
    if args.fixed_delay >= 0:
        # The rollback engine's own switch for its automatic delay
        # (log line "rbe: auto delay ON ... RBE_RB_AUTO_DELAY=0 off").
        args.delay = args.fixed_delay
        env["RBE_RB_AUTO_DELAY"] = "0"
    if args.delay >= 0:
        cmd += ["--net-delay", str(args.delay)]
    print("starting:", " ".join(cmd), f"(PSX_NET_SLOTS={args.players}, PSX_NET_MODE={args.mode}"
          + (", automatic delay off" if args.fixed_delay >= 0 else "") + ")")
    if not args.log:
        return subprocess.call(cmd, env=env)
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    print("the game's messages go to", log.resolve(), flush=True)
    with open(log, "wb") as f:
        return subprocess.call(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    sys.exit(main())
