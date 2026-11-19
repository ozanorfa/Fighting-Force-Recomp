#!/usr/bin/env python3
"""TEST ONLY: three players through the launcher's LAN room, scripted.

Each peer runs from a folder of its own (links to the build's exe and data,
copies of what the launcher or the runtime writes: mods/, the --game toml,
keybinds, settings, overlay captures; its own memory cards and log). The
launcher is driven with recomp-ui's script hook (LNG_SCRIPT, hidden window,
logical 1100x750 layout): the host opens NETPLAY -> LAN -> Host Lobby ->
Create Lobby and presses PLAY once the guests sat down; the guests use Join
Direct (127.0.0.1:7777). Every launcher step leaves a screenshot. Then the
games run hidden, each with its own debug server: the test waits for the
match (netplay_status), for every peer on the fighter select screen
(Quick Start's straight-to-select), lets each peer pick with its own pad,
waits for the level, lets everyone walk a while, quits and compares the
peers' per-frame state digests ("rb live dig").

The approach (recomp-ui's LNG_SCRIPT hook, one folder per peer) was first
worked out in a sibling PSXRecomp project.

    python3 tools/ff_lanroom.py --build build-net --out DIR [--disc disc/FightingForce.cue]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, pad  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXE = "FightingForce_Recompiled"
LINKS = [EXE, f"{EXE}.execution.json", "assets", "bios", "game_options.toml", "overlay_toolchain",
         "psx_execution", "native-cpu-spans", "native-idle-wait"]
COPIES = ["game_3p.toml", "keybinds.ini", "input.ini", "settings.toml", "overlay_captures.json"]
STATE = """format_version = 2

[[package]]
id = "ff.third-player"
version = "0.1.0"

[[feature]]
package_id = "ff.third-player"
id = "p3"
enabled = true
"""
# clicks in the 1100x750 logical layout (recomp-ui 839a6f9)
LAN, NAME, SAVE = "234,282", "547,435", "583,485"
HOST_LOBBY, CREATE_LOBBY, PLAY = "118,689", "546,545", "971,689"
JOIN_DIRECT, JOIN_DIRECT_OK = "981,689", "542,354"
HOST_PORT_FIELD, JOIN_PORT_FIELD = "701,416", "415,262"


def set_field(at: str, value: str) -> str:
    """Click a text field, clear its (up to 5) characters, type value."""
    return f"click:{at};wait:5;key:End;" + "key:Backspace;" * 5 + f"text:{value};wait:5;"
G_MENU_STATE, FN_SELECT, G_LEVEL, G_PLAYERS = 0x800927EC, 0x80050798, 0x80093D58, 0x800940B0
R = 0x800B4318


def make_peer(build: Path, folder: Path) -> None:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name in LINKS:
        src = build / name
        if src.exists():
            (folder / name).symlink_to(src.resolve())
    for name in COPIES:
        src = build / name
        if src.exists():
            shutil.copy2(src, folder / name)
    (folder / "mods").mkdir()
    shutil.copytree(build / "mods" / "bundled", folder / "mods" / "bundled", symlinks=True)
    (folder / "mods" / "state.toml").write_text(STATE)
    (folder / "cards").mkdir()


def script(role: str, name: str, shots: Path, port: int) -> str:
    s = (f"size:1100x750;wait:30;view:netplay_mode;wait:20;shot:{shots}/{role}_1_mode.png;"
         f"click:{LAN};wait:40;shot:{shots}/{role}_2_lan.png;"
         f"click:{NAME};wait:5;text:{name};wait:5;click:{SAVE};wait:20;shot:{shots}/{role}_3_named.png;")
    if role == "host":
        s += (f"click:{HOST_LOBBY};wait:40;" + set_field(HOST_PORT_FIELD, str(port)) +
              f"shot:{shots}/{role}_4_host.png;"
              f"click:{CREATE_LOBBY};wait:600;shot:{shots}/{role}_5_room.png;wait:900;"
              f"shot:{shots}/{role}_6_seated.png;click:{PLAY};wait:20000")
    else:
        s += (f"click:{JOIN_DIRECT};wait:30;" + set_field(JOIN_PORT_FIELD, str(port)) +
              f"shot:{shots}/{role}_4_join.png;"
              f"click:{JOIN_DIRECT_OK};wait:300;shot:{shots}/{role}_5_joined.png;"
              f"click:{JOIN_DIRECT_OK};wait:400;shot:{shots}/{role}_6_room.png;wait:20000")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build-net")
    ap.add_argument("--out", required=True)
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--walk", type=int, default=1500)
    ap.add_argument("--base-port", type=int, default=4400, help="debug servers: base, base+1, base+2")
    ap.add_argument("--room-port", type=int, default=7790, help="the LAN room's UDP port (7777 may be taken)")
    ap.add_argument("--fresh", action="store_true",
                    help="no mods/state.toml and no settings.toml in the peer folders: a first install's defaults")
    args = ap.parse_args()
    build = (ROOT / args.build).resolve()
    out = Path(args.out).resolve()
    shots = out / "launcher"
    shots.mkdir(parents=True, exist_ok=True)
    disc = (ROOT / args.disc).resolve()
    roles = [("host", "Hawk"), ("guest1", "Mace"), ("guest2", "Smasher")]
    procs, logs = [], []
    for i, (role, name) in enumerate(roles):
        folder = out / role
        make_peer(build, folder)
        if args.fresh:
            (folder / "mods" / "state.toml").unlink()
            (folder / "settings.toml").unlink(missing_ok=True)
        env = dict(os.environ, LNG_SCRIPT=script(role, name, shots, args.room_port), LNG_TEST_HIDDEN="1",
                   PSX_HIDDEN_WINDOW="1", PSX_DEBUG_SERVER="1", PSX_OVERLAY_AUTOCOMPILE_OFF="1")
        log = open(folder / "game.log", "w")
        procs.append(subprocess.Popen([str(folder / EXE), "--game", str(folder / "game_3p.toml"),
                                       "--disc", str(disc), "--memcard-dir", str(folder / "cards"),
                                       "--debug-port", str(args.base_port + i)],
                                      cwd=folder, stdout=log, stderr=subprocess.STDOUT, env=env))
        logs.append(log)
        print(f"{role} started", flush=True)
        time.sleep(4)
    clients = []
    try:
        for i, (role, _) in enumerate(roles):
            clients.append(Client(port=args.base_port + i).connect(wait_s=400))
            print(f"{role}: debug server up", flush=True)
        host, g1, g2 = clients
        t0 = time.time()
        while time.time() - t0 < 300:
            st = [c.cmd("netplay_status") for c in clients]
            if all(s.get("active") == 1 and s.get("tick", 0) > 0 for s in st):
                break
            time.sleep(2)
        print("match:", [{k: s.get(k) for k in ("active", "host", "rollback", "players", "tick", "desync")}
                         for s in st], flush=True)
        f0 = host.frame()
        while host.frame() - f0 < 9000:
            if all(struct.unpack("<I", c.read(G_MENU_STATE, 4))[0] == FN_SELECT for c in clients):
                break
            host.wait_frames(30)
        host.wait_frames(60)
        sel = [(c.frame(), struct.unpack("<I", c.read(G_MENU_STATE, 4))[0] == FN_SELECT,
                struct.unpack("<I", c.read(0x80092784, 4))[0]) for c in clients]
        print("select:", sel, flush=True)
        for i, c in enumerate(clients):
            c.shot(str(out / f"select_peer{i}.png"))
        # each peer picks with its own pad (in a match a peer's port 1 is its seat)
        g2.tap("RIGHT", frames=6, gap=30)
        for c in clients:
            c.tap("CROSS", frames=6, gap=60)
        f0 = host.frame()
        while host.frame() - f0 < 6000:
            s = host.cmd("mod_cmd", name="ff.p3", args="show").get("reply", "")
            if "active=1" in s and int(s.split("passes=")[1].split()[0]) > 0:
                break
            host.wait_frames(30)
        host.wait_frames(120)
        for k in range(args.walk // 10):
            for c, d in zip(clients, ("UP", "RIGHT", "LEFT")):
                c.press(pad(d), frames=6)
            host.wait_frames(10)
        for i, c in enumerate(clients):
            c.shot(str(out / f"level_peer{i}.png"))
        recs = []
        for c in clients:
            b = c.read(R, 3 * 0xF4)
            recs.append([(struct.unpack_from("<h", b, i * 0xF4 + 0x34)[0], struct.unpack_from("<h", b, i * 0xF4 + 0x42)[0],
                          struct.unpack_from("<i", b, i * 0xF4 + 0x28)[0] >> 16) for i in range(3)])
        print("records (type, hp, x) per peer:", recs, flush=True)
        print("level / players per peer:", [(struct.unpack("<i", c.read(G_LEVEL, 4))[0],
                                              struct.unpack("<i", c.read(G_PLAYERS, 4))[0]) for c in clients],
              flush=True)
        end = [c.cmd("netplay_status") for c in clients]
        print("end:", [{k: e.get(k) for k in ("players", "tick", "desync", "rollback")} for e in end], flush=True)
        checks = {
            "match with 3 players": all(e.get("active") == 1 and e.get("players") == 3 for e in end),
            "no desync": all(not e.get("desync") for e in end),
            "every peer reached the select": all(on for _, on, _ in sel),
            "records equal on every peer": all(r == recs[0] for r in recs),
            "P3 in the level": 0 <= recs[0][2][0] <= 3,
        }
        print("checks:", checks, flush=True)
        return 0 if all(checks.values()) else 1
    finally:
        for c in clients:
            try:
                c.cmd("quit")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(3)
        for p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for log in logs:
            log.close()
        # per-frame digests: ff_net_digests.py reads peer*.log
        for i, (role, _) in enumerate(roles):
            src = out / role / "game.log"
            if src.exists():
                shutil.copy2(src, out / f"peer{i}.log")


if __name__ == "__main__":
    raise SystemExit(main())
