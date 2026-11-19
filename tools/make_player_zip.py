#!/usr/bin/env python3
"""Make the players' ZIP: everything a Windows player needs to build and play
Fighting Force Recompiled with THEIR OWN disc, without installing anything.

    python3 tools/make_player_zip.py --python-zip PATH/python-3.12.10-embed-amd64.zip [--out DIR]

The ZIP holds no game data and no game code:
  FightingForce3P/
    Setup.bat            double-click: copies to C:\\RecompDev, builds with the player's disc
    START_HERE.txt       the steps in short
    FightingForceRecomp/ this repository and its submodules at HEAD (git archive:
                         committed files only; no .git, no disc, no generated code,
                         no builds, no saves), plus .ff_files (the file list)
    python/              python.org's Windows embeddable package, with three
                         search paths added (our tools, the framework's tools)

Built from the committed state: the working tree must be clean. A content
scan refuses disc images, memory cards, savestates, generated code and
retail BIOS names before the ZIP is written. Licences ride along (the
framework: PolyForm Noncommercial 1.0.0; recomp-ui, recomp-net,
retcomm-rbengine: MIT; Python: its LICENSE.txt).
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "FightingForce3P"
PTH_EXTRA = ["../tools", "../psxrecomp/tools", "../psxrecomp"]
# Our files: nothing from the disc and no player data. The framework's public
# submodules are shipped as published (they hold their own tools, OpenBIOS and
# a test memory card); there only disc images and retail BIOS dumps refuse.
DISC_AND_BIOS = ["*.cue", "*.iso", "*.chd", "*.img", "*.ecm", "*.ccd", "*.sub", "*.pst",
                 "*scph*.bin", "*SCPH*.BIN", "*scph*.BIN", "*SCPH*.bin"]
FORBIDDEN = DISC_AND_BIOS + ["*.mcd", "*.mcr", "*.bin", "generated/*", "build-*/*", "disc/*",
                             "prepared_disc/*", "saves/*", "*overlay_captures.json*",
                             "FightingForce.ico"]          # made on each PC from that player's disc
SUBMODULES = ("psxrecomp/", "recomp-ui/")
START_HERE = """Fighting Force Recompiled, three players - START HERE
=====================================================

1. YOU NEED
   - Windows 10 (version 1903 or newer) or 11, 64-bit; internet for the
     first setup.
   - Your own Fighting Force (USA) disc dump: one .cue file and its 27 .bin
     files in one folder. Nothing in this ZIP comes from the game.
   - To play together online: Radmin VPN (radmin-vpn.com).

2. FIRST START (once)
   1. Put the ZIP and the folder with your disc (.cue + 27 .bin) side by
      side in one folder with a short path (Desktop or Downloads), then
      Extract All (right-click the ZIP). Not from inside the ZIP, and not
      into Program Files.
   2. Double-click Setup.bat. "Windows protected your PC"? More info - Run
      anyway (the scripts are not signed).
   3. It finds your disc folder by itself. If not, it asks: drag your .cue
      file into the window and press Enter. Fewer than 27 .bin files next
      to it? It says so and asks again.
   4. Wait. The first time it downloads the compiler once and builds the
      game for your PC from your disc. It installs into
      C:\\RecompDev\\FightingForceRecomp (about 1 GB on drive C:). If it stops
      while downloading (for example "504"), run Setup.bat again later.
   5. Double-click "Fighting Force" on your desktop. A black window opens
      first: keep it open while you play (closing it closes the game).
      Your controller: on the first screen, card PLAYER 1, choose it below
      the controller picture (Configure changes the buttons). Then PLAY.

3. OUT OF THE BOX
   The third player and Quick Start are on: no start-up videos, straight to
   the fighter select. Nothing to set. On one PC, players 2 and 3 join on
   the fighter select with X and leave with Triangle. The second gamepad
   is player 2 by itself; player 3's gamepad you choose once: first
   screen, tab PLAYER 3, below the controller picture (scroll the card down
   if you cannot see it).

4. PLAYING TOGETHER
   - Everyone has the same ZIP (the same version) and is in one Radmin VPN
     network.
   - Online, every PC sets its controller on its own PLAYER 1 card.
   - Host: NETPLAY - LAN / Direct IP - Host Lobby, Max Players 3, Create.
   - The others: Join Direct, Host IP = the host's Radmin address
     (26.x.x.x), Port 7777.
   - Windows asks about the firewall: tick Private and Public, then Allow
     access. Pressed Cancel? Windows Security - Firewall - "Allow an app
     through firewall" - Change settings - tick FightingForce_Recompiled
     (Private and Public).
   - When all three are seated, the host presses PLAY. The game opens on
     the fighter select with everyone: each picks a different fighter
     with X.
   - Who is who: on the fighter select, the ring colours: red = player 1
     (the host), green = player 2, yellow = player 3. In a level, your
     fighter's name bar: player 1 top left, player 2 top right, player 3 at
     the bottom.

5. SAVES
   - A match uses the host's memory card. Save at the game's checkpoints:
     YES, then X SAVE.
   - Continue: the same host; on the fighter select the host presses
     Triangle, then OPTIONS - LOAD MEMORY CARD DATA - X LOAD.
   - Someone else hosts next time: that player first double-clicks
     Take_Match_Save.bat (in C:\\RecompDev\\FightingForceRecomp\\windows).
   - Never Browse your memory card to saves\\netplay\\card1.mcd: that is the
     match copy, and the next match overwrites it.

6. UPDATING
   Extract the new ZIP and run its Setup.bat. Your saves and disc path
   stay. Everyone in a match needs the same version.

IF SOMETHING GOES WRONG
   Setup stopped: copy all the text in its window and send it to whoever
   sent you this ZIP.
   In the game (it closed by itself, it was slow or choppy, or everyone
   went back to the room with "The match ended: ..."): write down what
   happened and any message, then close the game. The black window packs
   a report ZIP and shows it (C:\\RecompDev\\FightingForceRecomp\\reports):
   send that ZIP. In a match, one PC's problem ends the match on every
   PC: EVERY player closes the game before anyone starts another match,
   and everyone sends their ZIP. Started the game another way?
   Double-click windows\\Report.bat afterwards: it packs the last run's
   files.
   More: FightingForceRecomp\\docs\\PLAYING_TOGETHER.md
"""


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def archive_into(repo: Path, commit: str, dest: Path) -> int:
    data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo, check=True,
                          capture_output=True).stdout
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        for m in t.getmembers():
            if m.isfile():
                target = dest / m.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(t.extractfile(m).read())
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python-zip", required=True, help="python.org python-3.x-embed-amd64.zip")
    ap.add_argument("--out", default=str(ROOT.parent / "transfer"))
    args = ap.parse_args()
    if git("status", "--porcelain", "--untracked-files=no").strip():
        sys.exit("the working tree has uncommitted changes: commit first (the ZIP is built from HEAD)")
    head = git("rev-parse", "--short", "HEAD").strip()
    stage = Path(tempfile.mkdtemp(prefix="ffzip-"))
    src = stage / TOP / "FightingForceRecomp"
    try:
        n = archive_into(ROOT, "HEAD", src)
        print(f"repository at {head}: {n} files")
        for line in git("submodule", "status", "--recursive").splitlines():
            sha, path = line[1:].split()[:2]
            k = archive_into(ROOT / path, sha, src / path)
            print(f"  {path} at {sha[:8]}: {k} files")
        files = sorted(p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file())
        def refused(f: str) -> bool:
            pats = DISC_AND_BIOS if f.startswith(SUBMODULES) else FORBIDDEN
            return any(fnmatch.fnmatch(f, pat) or fnmatch.fnmatch(Path(f).name, pat) for pat in pats)
        bad = [f for f in files if refused(f)]
        if bad:
            sys.exit(f"refused: {len(bad)} files look like game data: {bad[:10]}")
        (src / ".ff_files").write_text("\n".join(files + [".ff_files"]) + "\n", encoding="utf-8")
        (src / "SOURCE_VERSION").write_text(f"{head}\n", encoding="utf-8")
        py = stage / TOP / "python"
        with zipfile.ZipFile(args.python_zip) as z:
            z.extractall(py)
        pth = next(py.glob("python3*._pth"))
        lines = pth.read_text().splitlines()
        pth.write_text("\r\n".join(lines[:2] + PTH_EXTRA + lines[2:]) + "\r\n")
        print(f"python: {pth.name} search paths {lines[:2] + PTH_EXTRA}")
        shutil.copy2(src / "windows" / "Setup.bat", stage / TOP / "Setup.bat")
        (stage / TOP / "START_HERE.txt").write_bytes(START_HERE.replace("\n", "\r\n").encode("ascii"))
        for bat in [stage / TOP / "Setup.bat", *sorted((src / "windows").glob("*.bat"))]:
            if bat.read_bytes().replace(b"\r\n", b"").count(b"\n"):
                sys.exit(f"{bat.name} has LF line endings in the archive")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        zpath = out / f"{TOP}_{head}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted((stage / TOP).rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(stage).as_posix())
        total = sum(1 for p in (stage / TOP).rglob("*") if p.is_file())
        print(f"wrote {zpath} ({zpath.stat().st_size / 1e6:.1f} MB, {total} files)")
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
