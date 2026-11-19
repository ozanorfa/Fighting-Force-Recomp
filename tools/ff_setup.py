#!/usr/bin/env python3
"""Setup helper run by windows/Setup.bat (works on macOS too), after it put
this folder in place: a git clone / pull from a bundle, or a copy from the
players' ZIP (no Git; the ZIP lists its files in .ff_files).

1. With Git: fetches the framework, launcher UI and netplay library at the
   pinned commits (git submodules; long paths for this repository only).
   Without Git (the ZIP) they are already in the folder.
2. Decides whether the game's C must be generated again: on the first build,
   and whenever the files the recompiler reads changed since the last
   generation (framework, seeds, game.toml, symbols, ...). That is a SHA-256
   over those files, kept in generated/.ff_gen_key. Changes to the plugins,
   tools, docs, launcher files or the 3-player config only need a rebuild.
3. Builds build-net with tools/ff_build.py and your disc.
4. Remembers the disc path in disc_source.txt (ignored by Git) for the next
   update.
5. Makes FightingForce.ico (the title logo, from the player's own disc) for
   the desktop shortcut, once (tools/ff_icon.py; ignored by Git).

    python tools/ff_setup.py --disc "PATH/Fighting Force (USA).cue" [--dry-run]

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / "generated" / "SLUS_004.33_dispatch.c"
KEY_FILE = ROOT / "generated" / ".ff_gen_key"
NOTE = ROOT / "disc_source.txt"
FILE_LIST = ROOT / ".ff_files"      # the players' ZIP: every file it holds, one per line
# Paths a change to which needs no new game C. Everything else that is part
# of the source (psxrecomp, seeds/, game.toml, symbols.toml, aot/,
# codegen_setup.*, psx_symbols.h, catalog_identity.json, ...) counts.
BUILD_ONLY = ("src/", "tools/", "docs/", "windows/", "mods/", "assets/", "packaging/", "scripts/",
              ".github/", "recomp-ui/", "README.md", "LICENSE", "NOTICE.md", "VERSION", "CMakeLists.txt", "game_3p.toml",
              "game_options.toml", ".gitignore", ".gitattributes", ".ff_files")


def git(*args: str, capture: bool = False) -> str:
    cmd = ["git", "-c", "core.longpaths=true", *args]
    if capture:
        return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    print(">> git " + " ".join(args), flush=True)
    if subprocess.call(cmd, cwd=ROOT) != 0:
        sys.exit(f"git {' '.join(args)} failed; the output above says why")
    return ""


def source_files() -> list[str]:
    if FILE_LIST.is_file():
        return [l for l in FILE_LIST.read_text(encoding="utf-8").splitlines() if l]
    if (ROOT / ".git").exists():
        return [l for l in git("ls-files", "--recurse-submodules", capture=True).splitlines() if l]
    sys.exit("no .ff_files and no .git: this folder is neither the players' ZIP nor a clone")


def generation_key() -> str:
    h = hashlib.sha256()
    for rel in sorted(source_files()):
        if rel.startswith(BUILD_ONLY):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disc", required=True, help="your .cue (all 27 .bin tracks beside it)")
    ap.add_argument("--old", default="", help="(ignored; kept for older Setup.bat files)")
    ap.add_argument("--dry-run", action="store_true", help="say what would be done, build nothing")
    args = ap.parse_args()
    disc = Path(args.disc).expanduser()
    if not disc.is_file():
        sys.exit(f"no disc at {disc}")

    if (ROOT / ".git").exists() and not FILE_LIST.is_file() and not args.dry_run:
        git("submodule", "sync", "--recursive")
        git("submodule", "update", "--init", "--recursive")
    key = generation_key()
    last = KEY_FILE.read_text().strip() if KEY_FILE.is_file() else ""
    if not MARKER.exists():
        regen, why = True, "first build"
    elif key != last:
        regen, why = True, "the files the recompiler reads changed" if last else "no record of the last generation"
    else:
        regen, why = False, "the recompiler's inputs are unchanged"
    print(f"\ngenerate the game C: {'yes' if regen else 'no'} ({why})", flush=True)
    cmd = [sys.executable, str(ROOT / "tools" / "ff_build.py"), "--disc", str(disc)]
    if regen and MARKER.exists():
        cmd.append("--regenerate")
    if args.dry_run:
        print("would run:", " ".join(cmd))
        return 0
    print(">> " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        # A note an older Setup wrote before its build may name a wrong dump:
        # drop it, so the next run uses the install's copy or asks again.
        NOTE.unlink(missing_ok=True)
        sys.exit(f"build failed (exit {rc}); the output above says why")
    # Remembered only after a successful build, so a wrong dump is never
    # reused by the next Setup run. Setup.bat reads it back with `set /p`,
    # which decodes the console's code page (OEM, e.g. 857 on Turkish
    # Windows), not UTF-8; a path that code page cannot hold is not kept
    # (Setup.bat then uses the install's own disc copy or asks again).
    try:
        NOTE.write_text(str(disc) + "\n", encoding="oem" if os.name == "nt" else "utf-8")
    except (OSError, UnicodeError, LookupError):
        NOTE.unlink(missing_ok=True)
    if MARKER.exists():
        KEY_FILE.write_text(key + "\n")
    # The desktop icon: the title logo from this player's own disc, made once
    # (tools/ff_icon.py). Setup.bat uses it for the shortcut when it exists.
    icon = ROOT / "FightingForce.ico"
    if not icon.is_file():
        print("\n>> making the desktop icon from your disc's title screen", flush=True)
        try:
            # ff_icon.py's own worst case (60 s connect + 180 s title + quitting)
            # stays under this, so it always gets to close the hidden game.
            rc = subprocess.call([sys.executable, str(ROOT / "tools" / "ff_icon.py")], cwd=ROOT, timeout=360)
        except subprocess.TimeoutExpired:
            rc = -1
        if rc != 0:
            print("   (no icon this time: the shortcut keeps the default one)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
