#!/usr/bin/env python3
"""Build the 3-player netplay game from this repo and your own disc.

Standard library only; the same steps on Windows and macOS. Run from the
repository root after `git submodule update --init --recursive`:

    python tools/ff_build.py --disc "PATH/TO/Fighting Force (USA).cue"

1. Windows only: makes sure the framework's compiler pack (cmake-clang-v1)
   is there (`psxrecomp_cli.py ensure-toolchain`; downloads it once into the
   shared cache unless PSXRECOMP_TOOLCHAIN_DIR points at an extracted pack).
2. Generates the game's C from the disc (`psxrecomp_cli.py generate`): copies
   the 27-track dump into disc/ (original files untouched), checks its
   digests against game.toml, recompiles SLUS_004.33 and GAME.EXE into
   generated/. Skipped when generated/ already exists (--regenerate forces
   it).
3. Builds the runtime (`psxrecomp_cli.py rebuild`) into build-net with
   5 controller seats (P3 is a multitap seat) and LAN / Direct IP netplay.

Then start a match with tools/ff_netplay.py.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "psxrecomp" / "psxrecomp_cli.py"
MARKER = ROOT / "generated" / "SLUS_004.33_dispatch.c"
STAGED_CUE = ROOT / "disc" / "FightingForce.cue"
EXE_NAME = "FightingForce_Recompiled.exe" if os.name == "nt" else "FightingForce_Recompiled"


def run(cmd: list[str]) -> None:
    print("\n>>", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        sys.exit(f"step failed (exit {rc}); the output above says why")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disc", default="", help="your .cue (all 27 .bin tracks beside it); "
                    "default: the copy already in disc/")
    ap.add_argument("--build-dir", default="build-net")
    ap.add_argument("--regenerate", action="store_true", help="generate the game C again")
    ap.add_argument("--no-netplay", action="store_true", help="3 seats, no netplay (like build-p3)")
    args = ap.parse_args()

    missing = [p for p in (CLI, ROOT / "psxrecomp" / "lib" / "recomp-net" / "CMakeLists.txt",
                           ROOT / "recomp-ui" / "CMakeLists.txt") if not p.exists()]
    if missing:
        sys.exit("framework checkout incomplete (missing " + ", ".join(str(m) for m in missing) +
                 "). Run: git submodule update --init --recursive")

    disc = Path(args.disc).expanduser().resolve() if args.disc else STAGED_CUE
    py = sys.executable
    common = ["--config", "game.toml", "--project-root", "."]

    if os.name == "nt":
        run([py, str(CLI), "ensure-toolchain", "--project-root", "."])

    if args.regenerate or not MARKER.exists():
        if not disc.is_file():
            sys.exit(f"no disc at {disc}: pass --disc with the path of your .cue")
        run([py, str(CLI), "generate", *common, "--disc", str(disc)])
    else:
        print("generated/ is there; not generating again (--regenerate to force)")

    extra = ["-DFF_MAX_PLAYERS=5"]
    if not args.no_netplay:
        extra += ["-DFF_NETPLAY=ON", "-DRNET_ENABLE_ICE=OFF"]
    run([py, str(CLI), "rebuild", *common, "--build-dir", args.build_dir, "--no-pgo",
         *[f"--cmake-extra={e}" for e in extra]])

    exe = ROOT / args.build_dir / EXE_NAME
    if not exe.is_file():
        sys.exit(f"build finished but {exe} is missing")
    print(f"\nbuilt {exe}")
    if not args.no_netplay:
        print("start a match:  python tools/ff_netplay.py host --listen ADDRESS:7777\n"
              "                python tools/ff_netplay.py join --slot 1 --host ADDRESS:7777")
    return 0


if __name__ == "__main__":
    sys.exit(main())
