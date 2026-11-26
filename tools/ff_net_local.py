#!/usr/bin/env python3
"""Local 3-player netplay test: host + 2 guests of a netplay build on this
machine over UDP (Direct IP), each with its own debug port. Each peer's
injected local pad is its own player: P1 on the host, P2 on guest 1, P3 on
guest 2 (seat 3 through the synchronized session input — no ff.p3 test
override). Usage:

    python3 tools/ff_net_local.py --exe build-net/FightingForce_Recompiled --out DIR

Boots all three, goes title -> 2 PLAYER GAME -> select (P3 picks with its
own pad) -> level, fights a little, and compares the three peers' player
records and netplay_status at the end.
"""
from __future__ import annotations

import argparse
import json
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


G_LEVEL, G_DONE = 0x80093D58, 0x800941B8
G_FRAME, G_PAUSE = 0x80093DD0, 0x80093DD8
G_CHEATS, G_START_LEVEL = 0x80093D68, 0x800927B4   # as tools/ff_p3_textures.py


def p3_pause_round(peers, tap, shot):
    """P3 (guest 2) pauses with START on its own pad (seat 3 through the
    session input): every peer shows P3's pause (pause byte bit 0, the
    plugin's pause_p3) and the level's frame counter stops; P1's START
    (host) does not end P3's menu; P3's START resumes everywhere."""
    host, g1, g2 = peers

    def st(p):
        reply = p.cmd("mod_cmd", name="ff.p3", args="show").get("reply") or ""
        m = re.search(r"pause_p3=(\d+)", reply)
        return p.read(G_PAUSE, 1)[0], int(m[1]) if m else -1, struct.unpack("<i", p.read(G_FRAME, 4))[0]

    def p3_hp():
        return struct.unpack("<h", host.read(R + 2 * 0xF4 + 0x42, 2))[0]

    for p in peers:
        p.cmd("clear_input", port=1)
    host.wait_frames(90)
    for _ in range(3):                      # a downed P3 cannot pause (the game's rule): continue first
        if p3_hp() > 0:
            break
        tap(g2, "CROSS", frames=8, gap=90)
    if p3_hp() <= 0:
        print("P3 pause: P3 is down and could not continue; no pause round", flush=True)
        return False
    tap(g2, "START", frames=8, gap=40)
    a = [st(p) for p in peers]
    host.wait_frames(40)
    b = [st(p) for p in peers]
    paused = all(x[0] & 1 and x[1] == 1 for x in a) and all(a[i][2] == b[i][2] for i in range(3))
    shot("p3_paused")
    tap(host, "START", frames=8, gap=40)
    c = [st(p) for p in peers]
    still = all(c[i][2] == b[i][2] and c[i][1] == 1 for i in range(3))
    tap(g2, "START", frames=8, gap=60)
    d = [st(p) for p in peers]
    resumed = all(x[0] == 0 and x[1] == 0 for x in d) and all(d[i][2] > b[i][2] for i in range(3))
    print(f"P3 pause (pause byte, pause_p3, frame) per peer: paused {a} -> {b}; after P1 START {c}; "
          f"after P3 START {d}", flush=True)
    ok = paused and still and resumed
    print(f"P3 pause: {'PASS' if ok else 'FAIL'} (paused {paused}, P1 cannot resume {still}, resumed {resumed})",
          flush=True)
    return ok


def load_and_change_level(host, peers, shot, slot):
    """The host loads savestate `slot` (made by ff_make_levelend_state.py at
    a level's end, level 0 by default) into the running match; the framework hands it to every
    peer. Then the level-done sequence and the next level's load (P3's
    fighter included) run inside the match."""
    def lv(p):
        return struct.unpack("<ii", p.read(G_LEVEL, 4) + p.read(G_DONE, 4))

    while host.frame() < 900:
        host.wait_frames(60)
    print("load request:", host.cmd("savestate", op="load", slot=slot), flush=True)
    f0 = host.frame()
    while host.frame() - f0 < 1800 and not all(lv(p)[1] > 0 for p in peers):
        host.wait_frames(30)
    print("after load (level, done):", [lv(p) for p in peers], "frame", host.frame(), flush=True)
    shot("loaded")
    loaded_level = lv(host)[0]  # the state's level (0 from ff_make_levelend_state.py's default)
    f0 = host.frame()
    while host.frame() - f0 < 9000 and not all(lv(p)[0] != loaded_level for p in peers):
        host.wait_frames(60)
    print("level change (level, done):", [lv(p) for p in peers], "after", host.frame() - f0, "frames", flush=True)
    host.wait_frames(900)
    print("  P3 plugin:", [p.cmd("mod_cmd", name="ff.p3", args="show").get("reply", "")[:160] for p in peers],
          flush=True)
    print("  P3 textures:", [" ".join(w for w in p.cmd("mod_cmd", name="ff.p3", args="show").get("reply", "").split()
                                      if w.startswith("tex_")) for p in peers], flush=True)
    shot("level1")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--mode", default="--headless")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--fight", type=int, default=1800)
    ap.add_argument("--title-frame", type=int, default=4300,
                    help="frame at which the title takes input (about 1400 with Quick Start's intro skip)")
    ap.add_argument("--net-mode", default="", help="PSX_NET_MODE for every peer (rollback / delay)")
    ap.add_argument("--host-card", default="",
                    help="copy this card file in as the host's slot-1 card before the match")
    ap.add_argument("--load-card", action="store_true",
                    help="host loads the game from its card (OPTIONS -> LOAD MEMORY CARD DATA) instead of a new game")
    ap.add_argument("--autopilot", action="store_true",
                    help="fight phase: each peer's player walks to the nearest enemy and attacks (its own pad); "
                         "a downed player presses X to continue")
    ap.add_argument("--checkpoint-save", action="store_true",
                    help="with --load-slot: after the level change the host answers the game's save prompt YES")
    ap.add_argument("--poke-desync", action="store_true",
                    help="detector check: after the level starts, change P3's health on guest 2 only "
                         "(a debug write) and report what the peers see")
    ap.add_argument("--mid-savestate", action="store_true",
                    help="in the level: host saves slot 2, the match plays on, host loads slot 2; compare")
    ap.add_argument("--save-slot", type=int, default=-1,
                    help="host saves this slot inside the match, prints the file names and stops")
    ap.add_argument("--load-slot", type=int, default=-1,
                    help="host loads this savestate slot instead of the menu route (level-change test)")
    ap.add_argument("--classic-menus", action="store_true",
                    help="press START and choose 2 PLAYER GAME (Quick Start's straight_to_select off)")
    ap.add_argument("--early-press", type=int, default=0,
                    help="guest 1 holds START for this many frames right after the start "
                         "(a press during power-on: rollbacks of the first ticks)")
    ap.add_argument("--p3-pause", action="store_true",
                    help="after the fight: P3 pauses with START on its own pad, P1's START does nothing, "
                         "P3's START resumes; checked on every peer")
    ap.add_argument("--start-level", type=int, default=-1,
                    help="TEST ONLY: start at this level id (the game's level-select cheat, debug writes on every peer)")
    ap.add_argument("--stagger", type=float, default=0.0,
                    help="seconds between starting the host, guest 1 and guest 2 (late joins)")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # A command-line session keeps 2 slots unless told otherwise (the slot
    # count is not taken from game.toml there): PSX_NET_SLOTS=3 on every peer.
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1",
               PSX_NET_SLOTS="3")
    if args.net_mode:
        env["PSX_NET_MODE"] = args.net_mode
    procs = []
    if args.host_card:
        (out / "cards0").mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copyfile(args.host_card, out / "cards0" / "card1.mcd")
    for i in range(3):
        cards = out / f"cards{i}"
        cards.mkdir(exist_ok=True)
        cmd = [args.exe, args.mode, "--no-launcher", "--game", args.game, "--disc", args.disc,
               "--memcard-dir", str(cards), "--debug-port", str(4371 + i), "--net-slot", str(i)]
        # Every peer binds its own UDP port (guests default to the host's port,
        # which collides on one machine); guests dial the host.
        cmd += ["--net-bind", f"127.0.0.1:{args.port + i}"]
        if i:
            cmd += ["--net-peer", f"127.0.0.1:{args.port}"]
        log = open(out / f"peer{i}.log", "w")
        procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env), log))
        if args.stagger:
            print(f"peer{i} started at {time.strftime('%H:%M:%S')}", flush=True)
            if i == 0:
                early_host = Client(port=4371).connect(wait_s=90)
            if i < 2:
                time.sleep(args.stagger)
        else:
            time.sleep(1.0 if i == 0 else 0.3)
    # The host waits at tick 0 until every seat has joined, while the debug
    # frame counter keeps counting: with late joins the title comes 4300
    # frames after the last guest starts (+ a margin for its boot and link).
    title_frame = (early_host.frame() + args.title_frame + 180) if args.stagger else args.title_frame
    peers = [Client(port=4371 + i).connect(wait_s=90) for i in range(3)]
    host, g1, g2 = peers

    def frames():
        return [p.frame() for p in peers]

    def status(tag):
        for i, p in enumerate(peers):
            st = p.cmd("netplay_status")
            print(f"  peer{i} {tag}: " + json.dumps({k: st.get(k) for k in st if k not in ("id", "ok")})[:400],
                  flush=True)

    def tap(peer, name, frames=6, gap=20):
        peer.press(pad(name), frames=frames)
        peer.wait_frames(frames + gap)

    def shot(tag):
        for i, p in enumerate(peers):
            p.shot(str(out / f"{tag}_peer{i}.png"))

    def to_select():
        """Straight to fighter select: wait until every peer shows the select
        state (no START, no menu), print what each peer sees."""
        f0 = host.frame()
        while host.frame() - f0 < 9000:
            if all(struct.unpack("<I", p.read(0x800927EC, 4))[0] == 0x80050798 for p in peers):
                break
            host.wait_frames(30)
        host.wait_frames(60)
        print("select:", [(p.frame(), struct.unpack("<I", p.read(0x800927EC, 4))[0] == 0x80050798,
                           struct.unpack("<I", p.read(0x80092784, 4))[0]) for p in peers],
              "(frame, on select, players) per peer", flush=True)

    print("frames at start", frames(), flush=True)
    if args.early_press:
        g1.press(pad("START"), frames=args.early_press)
        print(f"guest 1 holds START for {args.early_press} frames from frame {g1.frame()}", flush=True)
    status("start")
    if args.save_slot >= 0:
        # Name of a state saved inside a match (its file name carries the
        # match's content scope), then quit.
        while host.frame() < 900:
            host.wait_frames(60)
        print("save request:", host.cmd("savestate", op="save", slot=args.save_slot), flush=True)
        host.wait_frames(900)
        print("host state files:", sorted(str(f.relative_to(out)) for f in (out / "cards0").rglob("*.pst")), flush=True)
        args.fight = 0
    elif args.load_slot >= 0:
        load_and_change_level(host, peers, shot, args.load_slot)
        if args.checkpoint_save:
            host.wait_frames(600)
            shot("save_prompt")
            tap(host, "LEFT", gap=30)                   # YES
            tap(host, "CROSS", gap=250)
            shot("save_card")
            tap(host, "CROSS", gap=500)                 # X SAVE
            shot("saved")
            host.wait_frames(600)
    elif args.load_card:
        if args.classic_menus:
            while host.frame() < title_frame:
                host.wait_frames(60)
            shot("title")
            tap(host, "START", gap=450)
        else:
            to_select()
            tap(host, "TRIANGLE", gap=120)          # select -> the original main menu
        tap(host, "DOWN", gap=40)
        tap(host, "DOWN", gap=40)
        tap(host, "CROSS", gap=200)                 # OPTIONS
        for _ in range(5):
            tap(host, "DOWN", gap=30)               # LOAD MEMORY CARD DATA
        tap(host, "CROSS", gap=400)
        shot("card_found")
        tap(host, "CROSS", gap=300)                 # LOAD
        tap(host, "CROSS", gap=150)                 # acknowledge
        tap(host, "TRIANGLE", gap=200)              # leave OPTIONS: the loaded game starts
        f0 = host.frame()
        while host.frame() - f0 < 5000:
            s = host.cmd("mod_cmd", name="ff.p3", args="show").get("reply", "")
            if "active=1" in s and int(s.split("passes=")[1].split()[0]) > 0:
                break
            host.wait_frames(30)
        host.wait_frames(120)
        shot("level")
        print("loaded game:", [(struct.unpack("<i", p.read(0x80093D58, 4))[0], p.read(0x80094000, 1)[0],
                                struct.unpack("<i", p.read(0x9F000090, 4))[0]) for p in peers],
              "(level, loaded flag, P3 score) per peer", flush=True)
        if args.mid_savestate:
            host.wait_frames(300)
            print("savestate save:", host.cmd("savestate", op="save", slot=2), flush=True)
            host.wait_frames(600)
            def p3hp(p):
                return struct.unpack("<h", p.read(R + 2 * 0xF4 + 0x42, 2))[0]
            before = [p3hp(p) for p in peers]
            # change the game: everyone walks a while
            for k in range(30):
                for p, d in ((host, "RIGHT"), (g1, "UP"), (g2, "LEFT")):
                    p.press(pad(d), frames=6)
                host.wait_frames(10)
            print("P3 health before load:", [p3hp(p) for p in peers], "(saved:", before, ")", flush=True)
            print("savestate load:", host.cmd("savestate", op="load", slot=2), flush=True)
            host.wait_frames(600)
            shot("after_state_load")
    else:
        if args.classic_menus:
            while host.frame() < title_frame:
                host.wait_frames(60)
            shot("title")
            tap(host, "START", gap=450)
            tap(host, "DOWN", gap=40)
            tap(host, "CROSS", gap=400)
        else:
            to_select()
        shot("select")
        tap(g2, "RIGHT", gap=30)      # P3 moves its cursor with its own pad
        def write_start_level():
            for p in peers:
                cheats = struct.unpack("<I", p.read(G_CHEATS, 4))[0]
                p.cmd("write_bulk", addr=f"0x{G_CHEATS:08X}", hex=struct.pack("<I", cheats | 2).hex())
                p.cmd("write_bulk", addr=f"0x{G_START_LEVEL:08X}",
                      hex=struct.pack("<I", args.start_level).hex())

        if args.start_level >= 0:
            # TEST ONLY: the game's level-select cheat (the same debug writes as
            # ff_p3_textures.py) on every peer, read when the level starts.
            # They land a frame or two apart on the three peers. A rollback can
            # go back up to its snapshot ring's depth (40 x 16 frames) and undo
            # a write, so they are repeated every 30 frames from 300 frames
            # before the confirms until 750 frames after them.
            print("start level written from frames", frames(), flush=True)
            for _ in range(10):
                write_start_level()
                host.wait_frames(30)
        tap(host, "CROSS", gap=60)    # P1
        tap(g1, "CROSS", gap=60)      # P2
        tap(g2, "CROSS", gap=60)      # P3
        shot("picked")
        waited = 0
        if args.start_level >= 0:
            for _ in range(25):
                write_start_level()
                host.wait_frames(30)
            waited = 750
        host.wait_frames(3300 - waited)
        shot("level")
        print("level per peer:", [struct.unpack("<i", p.read(G_LEVEL, 4))[0] for p in peers], flush=True)
        print("  P3 textures:", [" ".join(w for w in p.cmd("mod_cmd", name="ff.p3", args="show").get("reply", "").split()
                                          if w.startswith("tex_")) for p in peers], flush=True)
    if args.poke_desync:
        host.wait_frames(120)
        addr = R + 2 * 0xF4 + 0x42
        before = [struct.unpack("<h", p.read(addr, 2))[0] for p in peers]
        g2.cmd("write_bulk", addr=f"0x{addr:08X}", hex=struct.pack("<h", 77).hex())
        print("poked P3 health on guest 2 only; before:", before, flush=True)
        for k in range(6):
            try:
                host.wait_frames(120)
            except Exception as ex:  # noqa: BLE001
                print("host gone:", ex, flush=True)
                break
            for i, p in enumerate(peers):
                try:
                    st = p.cmd("netplay_status")
                    hp = struct.unpack("<h", p.read(addr, 2))[0]
                    print(f"  +{(k + 1) * 120} peer{i}: P3 health {hp} frame {p.frame()} | active {st.get('active')} "
                          f"desync {st.get('desync')} stall {st.get('stall')}", flush=True)
                except Exception as ex:  # noqa: BLE001
                    print(f"  peer{i}: {ex}", flush=True)
        for proc, log in procs:
            if proc.poll() is None:
                proc.terminate()
            log.close()
        for i in range(3):
            lines = [l for l in (out / f"peer{i}.log").read_text(errors="replace").splitlines()
                     if "desync" in l.lower() or "stopped running" in l.lower() or "mismatch" in l.lower()]
            print(f"peer{i} log:", lines[:4], flush=True)
        return 0
    # fight a little: everyone punches / walks
    f0 = host.frame()
    t0 = time.time()
    k = 0
    EST = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}
    while host.frame() - f0 < args.fight:
        if not args.autopilot:
            for p, d in ((host, "RIGHT"), (g1, "UP"), (g2, "LEFT")):
                p.press(pad(d if k % 3 else "SQUARE"), frames=6)
            k += 1
            host.wait_frames(12)
            continue
        b = host.read(R, 8 * 0xF4)
        recs = [dict(t=struct.unpack_from("<h", b, i * 0xF4 + 0x34)[0], hp=struct.unpack_from("<h", b, i * 0xF4 + 0x42)[0],
                     x=struct.unpack_from("<i", b, i * 0xF4 + 0x28)[0] >> 16,
                     z=struct.unpack_from("<i", b, i * 0xF4 + 0x30)[0] >> 16) for i in range(8)]
        ens = [r for r in recs[3:] if r["t"] not in (-1, 0x11) and r["hp"] > 0]
        for i, p in enumerate(peers):
            me = recs[i]
            if me["t"] == -1 or me["hp"] <= 0:
                word = pad("CROSS") if k % 8 == 0 else 0xFFFF
            elif not ens:
                word = pad("UP") if k % 3 else 0xFFFF
            else:
                e = min(ens, key=lambda r: max(abs(r["x"] - me["x"]), abs(r["z"] - me["z"])))
                d = max(EST, key=lambda q: EST[q][0] * (e["x"] - me["x"]) + EST[q][1] * (e["z"] - me["z"]))
                near = max(abs(e["x"] - me["x"]), abs(e["z"] - me["z"])) <= 140
                word = pad(d) if (not near or k % 3 == 0) else (pad("SQUARE") if k % 3 == 1 else 0xFFFF)
            p.cmd("set_input", buttons=word)
        k += 1
        host.wait_frames(4)
    if args.autopilot:
        for p in peers:
            p.cmd("clear_input", port=1)
    pause_ok = p3_pause_round(peers, tap, shot) if args.p3_pause else True
    if args.fight:
        print(f"fight: {host.frame() - f0} frames in {time.time() - t0:.1f} s "
              f"({(host.frame() - f0) / max(1e-6, time.time() - t0):.1f} fps on the host)", flush=True)
    host.wait_frames(120)
    shot("end")
    print("frames at end", frames(), flush=True)
    status("end")
    # compare the three peers' player records at the same frame
    target = max(frames()) + 30
    snaps = []
    for p in peers:
        while p.frame() < target:
            time.sleep(0.01)
        # Read until two reads agree: a read can land inside a frame, while
        # the plugin has records 1 / 2 swapped or P3 hidden for a moment.
        b = p.read(R, 3 * 0xF4)
        for _ in range(16):
            b2 = p.read(R, 3 * 0xF4)
            if b2 == b:
                break
            b = b2
        snaps.append([(struct.unpack_from("<h", b, i * 0xF4 + 0x34)[0],
                       struct.unpack_from("<h", b, i * 0xF4 + 0x42)[0],
                       struct.unpack_from("<i", b, i * 0xF4 + 0x28)[0] >> 16,
                       struct.unpack_from("<i", b, i * 0xF4 + 0x30)[0] >> 16) for i in range(3)])
    for i, s in enumerate(snaps):
        print(f"  peer{i} records (type, hp, x, z):", s)
    print("  P3 plugin:", [p.cmd("mod_cmd", name="ff.p3", args="show").get("reply", "")[:120] for p in peers])
    print("  Quick Start:", [p.cmd("mod_cmd", name="ff.qs", args="show").get("reply", "") for p in peers])
    for p in peers:
        try:
            p.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
    time.sleep(2)
    for proc, log in procs:
        if proc.poll() is None:
            proc.terminate()
        log.close()
    # The verdict comes from the per-frame state digests, which are taken at
    # the same frame on every peer. The record reads above are not
    # frame-aligned (each peer is read when it reaches the target frame), so a
    # hit landing between two peers' reads can make them differ harmlessly.
    import ff_net_digests
    logs = sorted(out.glob("peer*.log"))
    per = [ff_net_digests.digests(p) for p in logs]
    common = sorted(set.intersection(*(set(x) for x in per))) if per else []
    bad = [n for n in common if len({x[n] for x in per}) > 1]
    same = all(s == snaps[0] for s in snaps)
    print("records on every peer:", "identical" if same else "different (reads not frame-aligned)", flush=True)
    print(f"state digests: {len(common)} points, {len(bad)} differ", flush=True)
    return 0 if common and not bad and pause_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
