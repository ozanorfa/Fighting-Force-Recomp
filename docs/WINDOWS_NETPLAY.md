# Windows build and 3-player netplay (Radmin VPN)

*Fighting Force* (Core Design, 1997) is a 3D beat 'em up for the original
Sony PlayStation (PS1), for one or two players. Fighting Force Recompiled
makes a native Windows program of it from your own disc and adds a third
player. More about the game and the project: [README](../README.md).

How to build Fighting Force Recompiled with the third player and netplay on
a Windows PC, and how to start a three-player match over Radmin VPN.

Every player does sections 1–4 on their own PC. Section 5 is the match.

**Status.** The players' ZIP was built and its contents were built into a
working game on macOS with the portable Python's path rules (not on Windows).
The steps were checked on macOS from a fresh clone, using the
original 27-track dump (`tools/ff_build.py`, same commands). On one Mac,
three copies played together: late joins, about 80 ms of added delay, and a
level change with the third player reloaded, all without desync. Nothing
has been run on Windows yet (Setup.bat included). If a step fails, keep the exact command and
its error text.

## Quick setup for players: the ZIP (nothing to install)

**Players: read [PLAYING_TOGETHER.md](PLAYING_TOGETHER.md)** (the same steps,
written for players). This section is the background.

**The friends' prebuilt ZIP** (preferred, not built yet): the maintainer cuts
a ready-to-play ZIP on a Windows PC with `windows\Make_Release.bat`, in the
git checkout this setup makes from a bundle (see *Developers* below). It runs
the framework's release packager (`packaging/release.toml`, channel
`playtest`; `psxrecomp/docs/RELEASING_A_TITLE.md`), which needs Python 3.11+
and MSYS2 MinGW-w64 (gcc, g++, objdump, cmake, ninja) on that PC. Friends
extract it, start `Play.bat` and choose their own disc's `.cue` once; its
`START_HERE.txt` has the steps. The ZIP holds the compiled game, so it is
shared privately with friends who own the game. Until it exists, the
self-building ZIP below is the way in.

What a player gets: **one file**, `FightingForce3P_<version>.zip` (made with
`tools/make_player_zip.py`). It holds this repository and the framework's
source, python.org's portable Python, `Setup.bat` and `START_HERE.txt`;
**no game data and no game code**: every player needs their own Fighting
Force (USA) disc dump (one `.cue` + 27 `.bin`).

1. Extract the whole ZIP, anywhere. Install [Radmin VPN](https://www.radmin-vpn.com/)
   for online matches. Nothing else.
2. Double-click `Setup.bat` (if Windows says it protected the PC: More info →
   Run anyway). The first time it looks for the disc by itself (its folder,
   the two above, and their subfolders: the first `.cue` with 27 `.bin`
   beside it); if none is found, drag your `.cue` into the window and press
   Enter.
3. It copies the code and Python to `C:\RecompDev\FightingForceRecomp`
   (short path: OneDrive, Program Files and non-English user folders stay
   out of the build), downloads the compiler once and builds the game with
   your disc. Measured on macOS from the ZIP's contents: the whole build
   (recompiler, game C, game) in 94 s on an 8-core Apple M1 without a
   compile cache; a Windows PC is likely slower, plus the one-time compiler
   download (from GitHub; if it stops with an error such as 504, run
   `Setup.bat` again later). About 1 GB on drive C: (the build keeps its own
   copy of the disc, ~560 MB); the compiler pack goes to
   `%LOCALAPPDATA%\retcomm\toolchains`. Then it makes the shortcut's icon
   from the title logo of your disc (`tools/ff_icon.py`: the game runs
   hidden for a few seconds; if it fails, the default icon stays; Windows
   may show an old icon until you sign out and in). It ends with a
   **"Fighting Force"** shortcut on the desktop (the launcher: play,
   settings, and NETPLAY → LAN / Direct IP for the room) and opens the
   `windows` folder:

| File | What it does |
|---|---|
| `Play_with_log.bat` | what the desktop icon starts: the launcher (play on this PC, controls, settings, the netplay room), with the game's messages kept in `logs\last_run.txt`; when the game closes, `Report.bat` packs the files to send into `reports\` and shows the ZIP. Its black window stays open while you play: closing it closes the game |
| `Play.bat` | the launcher without a log or report ZIP |
| `Host_Match.bat` | host a match over Radmin VPN without the launcher room: you are player 1 (log and report ZIP as above) |
| `Join_Match.bat` | join a match: asks for the host's `26.x.x.x` address and your player number (2 or 3; the two guests take different numbers) |
| `Take_Match_Save.bat` | after a match as a guest: make its campaign save your own card, to host next time (backup kept) |
| `Report.bat` | packs the last run's files into one ZIP in `reports\` (`logs\last_run.txt`, `psx_last_run_report.json`, `psx_crash.txt`, `SOURCE_VERSION`, whichever exist; the newest 20 ZIPs are kept). The start files call it; double-click it after a run started another way |

**On one PC** the game opens on the fighter select screen for player 1;
players 2 and 3 join there with ✕ ("PLAYER 2 PRESS X TO JOIN") and leave
with △ before they confirm. Player 1's △ goes back to the original menu.
Quick Start → "Straight to fighter select" off gives the PS1 menus back.

**A new version:** extract the new ZIP and run its `Setup.bat`. It copies
the new code over the old, keeps your saves, disc copy and disc path,
regenerates the game C only when the files the recompiler reads changed
(`tools/ff_setup.py`: a SHA-256 over them, kept in `generated/.ff_gen_key`),
and rebuilds. All players must be on the same version.

**Developers** can use a bundle instead of the ZIP: put
`FightingForceRecomp.bundle` next to `Setup.bat`; it then clones / updates
with Git (Git for Windows and Python 3 needed).

Another install folder: `set FF_DIR=D:\Games\FightingForceRecomp` in a
command prompt, then run `Setup.bat` from that prompt. If a step fails,
the window says so; copy its text.

The sections below are the same steps by hand.

## 1. What each player needs

| What | Notes |
|---|---|
| Windows 10 or 11, x64 | Radmin VPN runs on Windows only (its site: "Compatible with Windows 11, 10, 8, 7"). |
| Your own *Fighting Force (USA)* disc dump | Redump layout: one `.cue` plus all **27** `.bin` tracks beside it. Netplay checks the track count and the disc's track layout, so every player needs this same dump. |
| [Git for Windows](https://git-scm.com/downloads/win) | Default options. |
| [Python 3](https://www.python.org/downloads/windows/) | Tick **Add python.exe to PATH**. If `python --version` opens the Microsoft Store, turn off the `python.exe` / `python3.exe` App execution aliases in Windows Settings. |
| [Radmin VPN](https://www.radmin-vpn.com/) | For the match (section 5). |

The compiler (the framework's `cmake-clang-v1` pack, with CMake, Ninja and
Clang) is downloaded automatically by step 3, once. To use a pack you
already extracted, set `PSXRECOMP_TOOLCHAIN_DIR` to the folder that contains
its `bin` (see `psxrecomp/docs/GETTING_STARTED.md`, step 2).

## 2. Get the repository

All players must build **the same commit**. Use a short folder path without
spaces, for example `C:\RecompDev`.

From a Git remote:

```powershell
git clone https://YOUR-REMOTE/FightingForceRecomp.git C:\RecompDev\FightingForceRecomp
```

Or from a bundle file made on the Mac (`git bundle create
FightingForceRecomp.bundle --all` in the repository) and copied over:

```powershell
git clone C:\path\to\FightingForceRecomp.bundle C:\RecompDev\FightingForceRecomp
```

Then fetch the framework, launcher UI and netplay library at the pinned
commits (public GitHub repositories):

```powershell
cd C:\RecompDev\FightingForceRecomp
git submodule update --init --recursive
```

If Git reports `Filename too long`, run `git config --global core.longpaths
true` and repeat the last command.

## 3. Build

Point `--disc` at your own `.cue` (keep the quotes):

```powershell
python tools\ff_build.py --disc "C:\Discs\Fighting Force (USA)\Fighting Force (USA).cue"
```

What it does:

1. Makes sure the compiler pack is there (download on the first run).
2. Copies your dump into `disc\` (your original files are not changed),
   checks its digests against `game.toml`, and recompiles the game into
   `generated\`.
3. Builds `build-net\FightingForce_Recompiled.exe`: 5 controller seats (P3
   is a multitap seat) and LAN / Direct IP netplay
   (`-DFF_MAX_PLAYERS=5 -DFF_NETPLAY=ON -DRNET_ENABLE_ICE=OFF`).

Running it again rebuilds only what changed. `--regenerate` regenerates the
game C (needed after a framework or seed change). `--no-netplay
--build-dir build-p3` builds the local (one PC, three controllers) variant.

`disc\`, `generated\` and the build folders hold data derived from your disc.
They are ignored by Git and must never be committed or shared.

## 4. Check the build alone

```powershell
.\build-net\FightingForce_Recompiled.exe --game game_3p.toml --disc disc\FightingForce.cue --no-launcher
```

The fighter select screen should appear (Quick Start's "Straight to fighter
select"; with it off, the title screen). Close the window.

Player 1 is your seat in a match. It uses the first connected gamepad
(`p1_device = "auto"` in `game_3p.toml`) and the keyboard at the same time:
arrows, X = ✕, S = ○, Z = □, A = △, Return = Start. To pick another device
or change buttons, start the exe without `--no-launcher` once and use the
launcher's Controls / Settings (saved on your PC only).

## 5. Play over Radmin VPN

1. Everyone starts Radmin VPN. One player creates a network, the other two
   join it. Each PC now has a `26.x.x.x` address, shown in Radmin VPN.
2. **Firewall.** The first time the game opens a netplay port, Windows asks
   whether to allow it. Allow it for the network type Windows uses for the
   Radmin VPN adapter (if unsure, tick both *Private* and *Public*). The host
   receives on UDP **7777**; player 2 on 7778, player 3 on 7779 (in the
   launcher room guests take ports from 7778 up, and rooms announce
   themselves on UDP 48777 for the room list; framework source). Pressed
   Cancel? Windows Security → Firewall → "Allow an app through firewall" →
   tick `FightingForce_Recompiled` for Private and Public.
3. The host (player 1) starts first (`windows\Host_Match.bat` does the
   same):

   ```powershell
   python tools\ff_netplay.py host
   ```

   This listens on every address of the PC (port 7777). `--listen
   26.x.x.x:7777` listens on the Radmin address only.
4. Player 2 and player 3, with the host's Radmin address
   (`windows\Join_Match.bat` asks for the address and the number):

   ```powershell
   python tools\ff_netplay.py join --slot 1 --host 26.x.x.x:7777
   ```

   ```powershell
   python tools\ff_netplay.py join --slot 2 --host 26.x.x.x:7777
   ```

   The host's game waits at the start until both guests have joined, then
   all three boot together (tested with guests joining 40 and 80 seconds
   after the host).
5. The game opens straight on the **fighter select screen** with everyone
   (no PRESS START, no 1 / 2 PLAYER GAME menu). Each player moves their own
   cursor and confirms with ✕; all three fighters must be different. The
   level starts when everyone has picked. After a game over or the ending
   the game comes back to this screen with the same players. The host's △
   goes to the original main menu (options, memory card).

**Quick Start** (on by default) skips the start-up videos, the title and the
player-count menu, and the helicopter scene, in the match too. Every player must have the same Quick Start setting
(launcher → Mods → Quality of Life): the setting is part of the match's
identity, so players whose settings differ should not be able to start a
match together (framework source; not tested).

Options (every player must use the same values): `--mode delay` instead of
the default rollback; `--delay N` for a fixed input delay in frames.

**Input delay.** In rollback mode the game picks the input delay itself from
how late the other players' inputs arrive. In a test with about 80 ms added
between players it went from 5 to 10 frames (about 1/6 s), with no
corrections. A faster link gives a shorter delay.

If the controls feel slow, every player can add `--fixed-delay 5` to their
command: the delay stays at 5 frames (rollback's minimum, about 1/12 s) and
rollback corrects late inputs instead. In the same 80 ms test that gave
12–27 corrections a minute per player, no desync, and the game still ran
faster than needed. A correction can make a fighter jump a little on
screen. Every player must use the same value.

### Or start from the launcher's room

A room with names, a chat box and the host's Start button, instead of
commands: the desktop icon (`windows\Play_with_log.bat`) opens the launcher.

The netplay build opens `game_3p.toml` when started without `--game`, so its
launcher offers three players. Double-click
`build-net\FightingForce_Recompiled.exe`; on the first start pick
`disc\FightingForce.cue` when it asks for the disc. Then **NETPLAY** →
**LAN / Direct IP**:

- the host creates a room (**Host Lobby**) with **Max Players** 3;
- the others use **Join Direct** with **Host IP** = the host's `26.x.x.x`
  address and **Port** 7777 (the room list's Refresh only finds rooms whose
  broadcasts reach you).

When everyone is seated the host starts the match; the game opens on the
fighter select screen as above. After a match everyone returns to the room.
Start the three games a few seconds apart if they run from one folder on one
PC (a framework race on shared files in that folder; not an issue with one
game per PC).

Tested on one Mac with three launchers driven by a script
(`tools/ff_lanroom.py`): the room showed 3 / 3 seated with the three names,
the host's PLAY started the match (rollback, 3 players), every game opened on
the fighter select screen, and the games stayed identical (per-frame digests
equal). Not yet tried on Windows or over Radmin.

- If **"Port 7777 is already in use"** appears when creating the room,
  another program holds that port: type another port (e.g. 7790) in Host
  Lobby → Port, and the same port in Join Direct → Port.
- The room's chat repeats "<name> has joined." every few seconds while
  guests wait: a launcher (framework) display bug, harmless.

If this path fails, use `Host_Match.bat` / `Join_Match.bat`.

## Playing a campaign together

**Who is who.** On the fighter select the rings show the players: **red =
player 1 (the host), green = player 2, yellow = player 3**. In a level each
player has their own fighter (all three differ) and its name and health bar:
**player 1 top left, player 2 top right, player 3 at the bottom**. Everyone
sees the same screen, as in modern online beat-'em-ups; the game keeps the three of you
close together.

**Controls.** On your own PC you are player 1: a connected gamepad and the
keyboard keys both work (arrows, X = ✕, S = ○, Z = □, A = △, Return =
Start). Your controller's buttons can be changed in the launcher (start the
exe without `--no-launcher`). In a level, Start pauses the game on every PC;
only the player who paused can resume (Start) or quit (Select → QUIT), as in
the original game. Player 3's pause ("PAUSED 3P") needs Quick Start (on by
default): its menu reads player 3's controller through Quick Start's pad hook.

**Saving.** Two ways, both kept on the **host's** PC:

- *Checkpoints* (the game's own saves): after some levels the game asks
  "DO YOU WISH TO SAVE THE GAME?" — choose YES, then ✕ SAVE. The save holds
  everyone's fighter, score and health, the third player's too.
- *Save anywhere* (save states): the host presses **F7**, picks a slot with
  the number keys and presses **S** to save or **L** to load. All three
  games save or load together. Only the host can do this; F5 quick save is
  off in a match. Save states belong to this build: keep everyone on the
  same commit.

**Continuing next time.** Same host, same three players: start as in
section 5; on the fighter select screen the host presses **△** (the original
main menu), then **OPTIONS → LOAD MEMORY CARD DATA → ✕ LOAD**, ✕, then △;
the game continues at the saved level, with every player's fighter, score
and health (or the host loads a save state with F7 → L). Tested in a
3-peer match.

**Someone else hosts next time.** After a match each guest has a copy of
the host's card. Double-click `windows\Take_Match_Save.bat`: it shows what
your card and the last match's copy hold (level, fighters, scores, the third
player), asks, then copies the match's save over your card 1 and keeps your
old card as a backup. The same by hand:

```powershell
python tools\ff_match_save.py import
```

Do not point your memory card at `saves\netplay\card1.mcd` itself in the
launcher's Memory Card panel: that file is the match copy, and the next
match you join as a guest writes the host's card into it.

**If a player drops out** the match ends for everyone. Start again and load
the last checkpoint or save state.

## Known limits

- **Mac ↔ Windows** in one match has not been tested, and the framework does
  not claim that a macOS build and a Windows build stay in step. A Mac also
  cannot run Radmin VPN.
- The **launcher's LAN room** was tested on one Mac (scripted), not yet on
  Windows; the command-line start is the other tested path.
- `tools/ff_netplay.py` sets `PSX_NET_SLOTS` because the runtime's
  command-line start does not take the player count from `game_3p.toml`
  (a framework limit, reported upstream).

## When something fails

| Symptom | What to check |
|---|---|
| `framework checkout incomplete` | Run `git submodule update --init --recursive` in the repository folder. |
| `disc digests not in prepare_disc.known_*` | The dump is not the Redump *Fighting Force (USA)* this project was made for. |
| `Netplay could not open UDP port 7777 ... (system error 10048)` | Another copy of the game is still running, or another program holds the port. Close it, or use another port on every player's command line. |
| Guests wait forever | The host's firewall or a wrong address. Check the host's `26.x.x.x` address in Radmin VPN and the firewall prompt in step 2. |
| The match ends right after it starts, or the games drift apart | The players are not on the same commit, build options or disc dump. Check `git log -1` and the build command on every PC. |
