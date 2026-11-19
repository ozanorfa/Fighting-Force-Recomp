#!/usr/bin/env python3
"""TEST ONLY: fight through every wave of a level with three players. Offline, debug
server, one session. For each level id: title -> 2 PLAYER GAME -> select
(P3 picks with the ff.p3 seat override) with the game's own level-select cheat
switched on by test-only writes (DAT_80093d68 bit 1, start level
DAT_800927b4; FUN_8004fbcc reads them when the game starts) -> the level
starts -> all three fight on autopilot while the test clears the wave
script's wait-for-the-players flag (DAT_80093dd9; normally an area trigger
clears it when the players walk on), so the waves -- bosses included -- keep
coming until the level is done or --fight frames pass. Records every enemy
type met and its largest health, P3's hits and deaths, the enemy weapon /
special attack passes and guard misses. A fresh process per level.

    python3 tools/ff_p3_waves.py --exe build-net/FightingForce_Recompiled --out DIR [--levels 0-24] [--fight 2400]
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ffdbg import Client, Stalled, pad  # noqa: E402

R = 0x800B4318
G_LEVEL, G_DONE, G_LIVES, G_GAME_OVER = 0x80093D58, 0x800941B8, 0x80093D50, 0x80093DD4
G_CHEATS, G_START_LEVEL = 0x80093D68, 0x800927B4
EST = {"RIGHT": (0.92, -0.38), "LEFT": (-0.74, 0.3), "UP": (0.4, 0.9), "DOWN": (-0.3, -1)}
WATCH = {0x0004930C: "weapon", 0x0004DB94: "special"}


def parse_levels(s: str) -> list[int]:
    res = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            res += range(int(a, 0), int(b, 0) + 1)
        else:
            res.append(int(part, 0))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default="game_3p.toml")
    ap.add_argument("--disc", default="disc/FightingForce.cue")
    ap.add_argument("--levels", default="0-24")
    ap.add_argument("--fight", type=int, default=2400)
    ap.add_argument("--port", type=int, default=4370)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PSX_OVERLAY_AUTOCOMPILE_OFF="1", PSX_DEBUG_SERVER="1")
    state = {}

    def start(tag):
        state["log"] = open(out / f"run_{tag}.log", "w")
        state["proc"] = subprocess.Popen([args.exe, "--headless", "--no-launcher", "--game", args.game,
                                          "--disc", args.disc, "--memcard-dir", str(out / "cards"),
                                          "--debug-port", str(args.port)],
                                         stdout=state["log"], stderr=subprocess.STDOUT, env=env)
        return Client(port=args.port).connect(wait_s=90)

    def stop(c):
        try:
            c.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
        if state["proc"].poll() is None:
            state["proc"].terminate()
            time.sleep(1)
        state["log"].close()

    def w(a, f="<i"):
        return struct.unpack(f, c.read(a, struct.calcsize(f)))[0]

    def wr(a, v, f="<I"):
        c.cmd("write_bulk", addr=f"0x{a:08X}", hex=struct.pack(f, v).hex())

    def p3(s):
        return c.cmd("mod_cmd", name="ff.p3", args=s).get("reply") or ""

    def field(s, name):
        return int(s.split(name + "=")[1].split()[0]) if name + "=" in s else -1

    def p3_tap(name):
        p3(f"seat {pad(name):04X}"); c.wait_frames(4); p3("seat FFFF"); c.wait_frames(16)

    def recs(n=8):
        b = c.read(R, n * 0xF4)
        for _ in range(8):
            b2 = c.read(R, n * 0xF4)
            if b2 == b:
                break
            b = b2
        g = lambda i, o, f: struct.unpack_from(f, b, i * 0xF4 + o)[0]
        return [dict(t=g(i, 0x34, "<h"), hp=g(i, 0x42, "<h"), x=g(i, 0x28, "<i") >> 16, z=g(i, 0x30, "<i") >> 16)
                for i in range(n)]

    def overrides():
        res, misses = {}, 0
        for o in c.cmd("func_override").get("overrides", []):
            a = o.get("addr")
            a = (int(a, 16) if isinstance(a, str) else int(a)) & 0x1FFFFFFF
            misses += int(o.get("guard_misses", 0))
            if a in WATCH:
                res[WATCH[a]] = o.get("calls")
        return res, misses

    def send(i, word):
        if i == 2:
            p3(f"seat {word:04X}")
        elif i == 1:
            c.cmd("set_input", buttons=word, port=2, connected=1)
        else:
            c.cmd("set_input", buttons=word)

    def release_all():
        for i in range(3):
            send(i, 0xFFFF)
        c.cmd("clear_input", port=1)
        c.hold(0xFFFF, port=2)

    tick = [0]

    def autopilot_step():
        s = recs()
        ens = [r for r in s[3:] if r["t"] not in (-1, 0x11) and r["hp"] > 0]
        for i in range(3):
            me = s[i]
            if me["t"] == -1 or me["hp"] <= 0:
                send(i, pad("CROSS") if tick[0] % 8 == 0 else 0xFFFF)   # continue
                continue
            if not ens:
                send(i, pad("UP") if tick[0] % 3 else 0xFFFF)
                continue
            e = min(ens, key=lambda r: max(abs(r["x"] - me["x"]), abs(r["z"] - me["z"])))
            d = max(EST, key=lambda k: EST[k][0] * (e["x"] - me["x"]) + EST[k][1] * (e["z"] - me["z"]))
            near = max(abs(e["x"] - me["x"]), abs(e["z"] - me["z"])) <= 140
            k = tick[0] % 3
            send(i, pad(d) if (not near or k == 0) else (pad("SQUARE") if k == 1 else 0xFFFF))
        tick[0] += 1
        c.wait_frames(4)

    results = []
    c = None
    try:
        for lv in parse_levels(args.levels):
            c = start(f"lv{lv:02d}")
            p3("seat FFFF")
            c.hold(0xFFFF, port=2)
            title_at = 1400
            while c.frame() < title_at:
                c.wait_frames(30)
            c.enter_select()                   # straight-to-select or classic menus
            # the game's level-select cheat (test only), read when the game starts
            wr(G_CHEATS, w(G_CHEATS) | 2)
            wr(G_START_LEVEL, lv)
            p3_tap("RIGHT")
            c.tap("CROSS", frames=6, gap=60)
            c.tap("CROSS", frames=6, gap=30, port=2)
            p3_tap("CROSS")
            pass0 = field(p3("show"), "passes")
            loads0 = field(p3("show"), "loads")
            f0 = c.frame()
            started = False
            while c.frame() - f0 < 6000:
                s = p3("show")
                if field(s, "passes") > pass0 and "active=1" in s:
                    started = True
                    break
                c.wait_frames(30)
            level_now = w(G_LEVEL)
            c.shot(str(out / f"lv{lv:02d}_start.png"))
            wr(G_LIVES, 9)   # test only: keep continuing
            ov0, miss0 = overrides()
            h0 = field(p3("show"), "p3_hits")
            f1 = c.frame()
            types = set()
            min_hp = [999, 999, 999]
            max_hp = {}
            pauses = 0
            p3_deaths = 0
            p3_was_up = True
            try:
                while started and c.frame() - f1 < args.fight and w(G_LEVEL) == level_now and w(G_DONE) == 0:
                    autopilot_step()
                    s = recs()
                    for r in s[3:]:
                        if r["t"] not in (-1,) and r["hp"] > 0:
                            types.add(r["t"])
                            max_hp[r["t"]] = max(max_hp.get(r["t"], 0), r["hp"])
                    for i in range(3):
                        if s[i]["t"] != -1:
                            min_hp[i] = min(min_hp[i], s[i]["hp"])
                    # record 2 reads -1 while records are swapped inside a frame: no news
                    if s[2]["t"] != -1:
                        up = s[2]["hp"] > 0
                        if p3_was_up and not up:
                            p3_deaths += 1
                        p3_was_up = up
                    if c.read(0x80093DD9, 1)[0]:          # test only: let the wave script go on
                        c.cmd("write_bulk", addr="0x80093DD9", hex="00")
                        pauses += 1
                    if w(G_LIVES) < 3:
                        wr(G_LIVES, 9)
            except Stalled as e:
                # the guest halted (FAIL-FAST): report it and go on with the next level
                state["log"].flush()
                fatal = [l for l in (out / f"run_lv{lv:02d}.log").read_text(errors="replace").splitlines()
                         if "FATAL" in l]
                r = dict(level=lv, frames=c.frame() - f1, crashed=(fatal[0][:200] if fatal else str(e)),
                         done=0, pauses_cleared=pauses, p3_hits=0, p3_deaths=p3_deaths, weapon=0, weapon_p3=0,
                         special=0, guard_misses=0, max_hp=dict(sorted(max_hp.items())))
                print(r, flush=True)
                results.append(r)
                stop(c)
                c = None
                continue
            release_all()
            c.shot(str(out / f"lv{lv:02d}_fight.png"))
            ov1, miss1 = overrides()
            show = p3("show")
            r = dict(level=lv, loaded=level_now, started=started, frames=c.frame() - f1,
                     p3=recs(3)[2], fighter_loads=field(show, "loads") - loads0,
                     p3_hits=field(show, "p3_hits") - h0, min_hp=min_hp,
                     enemy_types=sorted(types), weapon=ov1.get("weapon", 0) - ov0.get("weapon", 0),
                     special=ov1.get("special", 0) - ov0.get("special", 0), guard_misses=miss1 - miss0,
                     game_over=w(G_GAME_OVER), level_after=w(G_LEVEL), done=w(G_DONE),
                     max_hp=dict(sorted(max_hp.items())), pauses_cleared=pauses, p3_deaths=p3_deaths,
                     weapon_p3=field(show, "weapon_p3"),
                     held=[field(show, k) for k in ("held_waves", "held_pod", "held_dropoff")], crashed="")
            print(r, flush=True)
            results.append(r)
            stop(c)
            c = None
        print("SUMMARY", flush=True)
        for r in results:
            print(f"  level {r['level']:2d} (0x{r['level']:02X}): {r['frames']} frames, done {r['done'] > 0}, "
                  f"waits cleared {r['pauses_cleared']} | P3 hits {r['p3_hits']} deaths {r['p3_deaths']} "
                  f"| weapon {r['weapon']} (P3 {r['weapon_p3']}) special {r['special']} misses {r['guard_misses']} "
                  f"| held {r.get('held')} | enemies (type: max hp) {r['max_hp']}"
                  + (f" | CRASH {r['crashed']}" if r["crashed"] else ""), flush=True)
        if any(r["crashed"] for r in results):
            return 1
        return 0
    finally:
        if c is not None:
            stop(c)


if __name__ == "__main__":
    raise SystemExit(main())
