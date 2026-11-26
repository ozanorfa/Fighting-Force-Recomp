#!/usr/bin/env python3
"""TEST ONLY: check a framework bump before taking it (NOTES "Framework pins").

Builds this repository at HEAD with psxrecomp and recomp-ui at the given
commits, in a git worktree of its own (the checkout you work in is not
touched), and runs Fighting Force's checks against it:

    python3 tools/ff_bump_check.py --psxrecomp COMMIT --recomp-ui COMMIT [--out DIR]
        [--steps prepare,build,codegen,quick,pads,waves,textures,saves,soaks]
        [--disc "PATH/Fighting Force (USA).cue"] [--baseline-build build-net]

Steps (all by default, in this order; each writes under OUT and adds a line
to OUT/report.md):
  prepare   git worktree at OUT/proj (detached HEAD), submodules at the
            given commits (their own submodules as those commits pin them)
  build     tools/ff_build.py in OUT/proj: generate the game C from the
            disc, build build-net
  codegen   compare OUT/proj/generated with this checkout's generated/
            (a changed function boundary moves hook addresses and guards)
  quick     the quick test tier (tools/run_tests.py) on the new build
  pads      tools/ff_press_probe.py and tools/ff_pad_cmds.py (short presses,
            the pad driver's commands; FF reads its pads through the BIOS
            driver, which the native-BIOS work upstream touches)
  waves     tools/ff_p3_waves.py over all 25 levels: crashes and every
            override's guard misses
  textures  tools/ff_p3_textures.py: all 25 levels with the three largest
            fighters, and the 7 crowded levels with the other P3 fighters
  saves     the full tier's net_card: a match continuing from a memory
            card save with P3 (needs that card; skipped without it)
  soaks     BUGS #32's table: 3-peer rollback soaks with lag and loss,
            automatic delay and RBE_RB_AUTO_DELAY=0, on the new build and on
            the baseline build, each while --load-procs offline games run
            beside it (the 2026-11-09 recipe); digests, and the rollback
            divergence lines of the peers' logs

The machine is busy for hours with every step. Other heavy work on the same
Mac makes the network tests unreliable: agree on the time first.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
STEPS = ["prepare", "build", "codegen", "quick", "pads", "waves", "textures", "saves", "soaks"]
LAG = {"RNET_SIM_LATENCY_MS": "30", "RNET_SIM_JITTER_MS": "10", "RNET_SIM_LOSS_PCT": "3"}
DIVERGE = re.compile(r"post core diverge|baseline core mismatch|promote-no-resim")


def run(cmd, log: Path, cwd: Path = ROOT, env: dict | None = None) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write("\n$ " + " ".join(str(c) for c in cmd) + "\n")
        f.flush()
        return subprocess.call([str(c) for c in cmd], cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                               env=dict(os.environ, **(env or {})))


def git(*args, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def tail_summary(log: Path, n: int = 30) -> str:
    lines = log.read_text(errors="replace").splitlines() if log.exists() else []
    return "\n".join(lines[-n:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--psxrecomp", required=True, help="psxrecomp commit to try")
    ap.add_argument("--recomp-ui", required=True, help="recomp-ui commit to try (they move together)")
    ap.add_argument("--out", default="")
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--disc", default=str(ROOT.parent / "disc" / "Fighting Force (USA).cue"))
    ap.add_argument("--baseline-build", default="build-net", help="the current pin's build, for the soaks")
    ap.add_argument("--soaks", default="auto:1,nodelay:2", help="mode:count, mode = auto | nodelay")
    ap.add_argument("--soak-frames", type=int, default=18000)
    ap.add_argument("--load-procs", type=int, default=2, help="offline games run beside each soak")
    args = ap.parse_args()
    steps = [s for s in args.steps.split(",") if s]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        sys.exit(f"unknown steps: {bad}")
    out = Path(args.out or ROOT.parent / "test_runs" / f"bump_{args.psxrecomp[:8]}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    proj = out / "proj"
    exe = proj / "build-net" / "FightingForce_Recompiled"
    report = out / "report.md"
    if not report.exists():
        report.write_text(f"# Framework bump check\n\npsxrecomp `{args.psxrecomp}`, recomp-ui `{args.recomp_ui}`, "
                          f"Fighting Force at `{git('rev-parse', '--short', 'HEAD')}`\n\n| Step | Result | Notes |\n|---|---|---|\n")

    def note(step: str, ok: bool | None, text: str) -> None:
        res = "PASS" if ok else ("FAIL" if ok is False else "info")
        with open(report, "a") as f:
            f.write(f"| {step} | {res} | {text} |\n")
        print(f"[{time.strftime('%H:%M')}] {step}: {res} {text}", flush=True)

    for step in steps:
        log = out / f"{step}.log"
        if step == "prepare":
            if not proj.exists():
                git("worktree", "add", "--detach", str(proj), "HEAD")
            ok = run(["git", "submodule", "update", "--init"], log, cwd=proj) == 0
            for sub, commit in (("psxrecomp", args.psxrecomp), ("recomp-ui", args.recomp_ui)):
                ok = ok and run(["git", "fetch", "origin"], log, cwd=proj / sub) == 0
                ok = ok and run(["git", "checkout", "--detach", commit], log, cwd=proj / sub) == 0
                ok = ok and run(["git", "submodule", "update", "--init", "--recursive"], log, cwd=proj / sub) == 0
            pins = "; ".join(f"{s} {git('rev-parse', '--short=8', 'HEAD', cwd=proj / s)}" for s in ("psxrecomp", "recomp-ui")) if ok else "?"
            note(step, ok, f"worktree `{proj}`; {pins}")
            if not ok:
                return 1
        elif step == "build":
            ok = run([PY, "tools/ff_build.py", "--disc", args.disc], log, cwd=proj) == 0 and exe.exists()
            note(step, ok, f"`{exe}`" if ok else f"see {log.name}")
            if not ok:
                return 1
        elif step == "codegen":
            new, old = proj / "generated", ROOT / "generated"
            digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
            names = sorted({p.relative_to(new).as_posix() for p in new.rglob("*") if p.is_file()} |
                           {p.relative_to(old).as_posix() for p in old.rglob("*") if p.is_file()})
            differ = [n for n in names if not ((new / n).is_file() and (old / n).is_file()
                                               and digest(new / n) == digest(old / n))]
            code = [n for n in differ if n.endswith(".c") or n.endswith(".h")]
            log.write_text("\n".join(differ) + "\n")

            def entries(gen: Path) -> tuple[set, set]:
                """Function entries: the boot EXE's (decls) and GAME.EXE's static
                variants (overlays_static_*.c). FF's hooks sit at function
                entries; a moved boundary would need their guards checked."""
                slus = set(re.findall(r"^void func_(8[0-9A-F]{7})\(CPUState\* ?cpu\)",
                                      (gen / "SLUS_004.33_decls.h").read_text(errors="replace"), re.M))
                game = set()
                for f in sorted(gen.glob("overlays_static_*.c")):
                    game |= set(re.findall(r"^void ov_[0-9A-F_]+_func_(8[0-9A-F]{7})\(CPUState",
                                           f.read_text(errors="replace"), re.M))
                return slus, game
            (s_new, g_new), (s_old, g_old) = entries(new), entries(old)
            same = s_new == s_old and g_new == g_old
            note(step, same, f"{len(names)} files, {len(differ)} differ ({len(code)} C/H: emitter output); "
                             f"function entries boot EXE {len(s_old)} -> {len(s_new)}, GAME.EXE {len(g_old)} -> "
                             f"{len(g_new)}, {'identical' if same else 'MOVED: check every hook'} "
                             f"(only in new: {sorted((s_new - s_old) | (g_new - g_old))[:6]}, "
                             f"only in old: {sorted((s_old - s_new) | (g_old - g_new))[:6]})")
        elif step in ("quick", "saves"):
            cmd = [PY, "tools/run_tests.py", "--build", str(proj / "build-net"), "--out", str(out / step)]
            if step == "saves":
                cmd += ["-k", "net_card"]
            rc = run(cmd, log, env={})
            summary = [line.strip() for line in tail_summary(log, 40).splitlines()
                       if line.strip().startswith(("PASS", "FAIL", "SKIP"))]
            note(step, rc == 0, "; ".join(summary))
        elif step == "pads":
            rc1 = run([PY, "tools/ff_press_probe.py", "--exe", exe, "--out", out / "press"], log)
            rc2 = run([PY, "tools/ff_pad_cmds.py", "--exe", exe, "--out", out / "padcmds"], log)
            note(step, rc1 == 0 and rc2 == 0, f"press probe exit {rc1}, pad commands exit {rc2} (compare with "
                                               f"NOTES \"v0.3.7 short presses\": every press 2-12 frames taken, "
                                               f"every transaction `01 42` from kernel RAM)")
        elif step == "waves":
            rc = run([PY, "tools/ff_p3_waves.py", "--exe", exe, "--out", out / "waves", "--levels", "0-24"], log)
            misses = re.findall(r"misses (\d+)", log.read_text(errors="replace"))
            note(step, rc == 0, f"exit {rc}; guard misses per level: {sum(int(m) for m in misses)} in "
                                f"{len(misses)} levels")
        elif step == "textures":
            rc1 = run([PY, "tools/ff_p3_textures.py", "--exe", exe, "--out", out / "textures_levels",
                       "--levels", "0-24"], log)
            rc2 = run([PY, "tools/ff_p3_textures.py", "--exe", exe, "--out", out / "textures_combos",
                       "--levels", "5,7,8,9,11,12,18", "--scenarios", "alana,hawk,mace", "--port", "4427"], log)
            text = log.read_text(errors="replace")
            note(step, rc1 == 0 and rc2 == 0, f"25 levels exit {rc1}, crowded-level combos exit {rc2}; "
                 f"second loads {text.count('P3 in a second load')}, kept stock {text.count('no room, stock load')}, "
                 f"allocator copy mismatches {text.count('tex_sim_mismatch')}")
        elif step == "soaks":
            builds = [("new", exe), ("baseline", (ROOT / args.baseline_build / exe.name).resolve())]
            for mode_count in args.soaks.split(","):
                mode, _, count = mode_count.partition(":")
                for k in range(int(count or 1)):
                    for label, game in builds:
                        sdir = out / "soaks" / f"{label}_{mode}_{k + 1}"
                        if sdir.exists():
                            shutil.rmtree(sdir)
                        sdir.mkdir(parents=True)
                        env = dict(LAG)
                        if mode == "nodelay":
                            env["RBE_RB_AUTO_DELAY"] = "0"
                        load = [subprocess.Popen([PY, "tools/ff_p3_waves.py", "--exe", str(game), "--out",
                                                  str(sdir / f"load{j}"), "--levels", f"{j * 8}-{j * 8 + 7}",
                                                  "--port", str(4440 + j)], cwd=ROOT,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                for j in range(args.load_procs)]
                        time.sleep(20)
                        load_avg = os.getloadavg()[0] if hasattr(os, "getloadavg") else -1
                        rc = run([PY, "tools/ff_net_local.py", "--exe", game, "--out", sdir, "--autopilot",
                                  "--fight", str(args.soak_frames), "--stagger", "3", "--net-mode", "rollback",
                                  "--port", "7860"], sdir / "soak.log", env=env)
                        for p in load:
                            p.terminate()
                        for p in load:
                            try:
                                p.wait(15)
                            except subprocess.TimeoutExpired:
                                p.kill()
                        run([PY, "tools/ff_net_digests.py", sdir], sdir / "soak.log")
                        text = (sdir / "soak.log").read_text(errors="replace")
                        m = re.search(r"(\d+) digest points compared.*?(\d+) differ", text)
                        points, differ = (int(m[1]), int(m[2])) if m else (0, -1)
                        diverge = sum(len(DIVERGE.findall(p.read_text(errors="replace")))
                                      for p in sdir.glob("peer*.log"))
                        scale = sorted({m2 for p in sdir.glob("peer*.log")
                                        for m2 in re.findall(r"internal scale \w+", p.read_text(errors="replace"))})
                        ended = "ended early" if re.search(r"stopped responding|admit stall", text) else "ran to the end"
                        note(f"soak {label} {mode} {k + 1}", rc == 0 and differ == 0,
                             f"load {load_avg:.1f}; {points} digest points, {differ} differ; divergence lines "
                             f"{diverge}; {ended}; renderer {', '.join(scale) or '?'}")
    print(f"\nreport: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
