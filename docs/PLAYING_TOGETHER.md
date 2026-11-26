# Playing Fighting Force together (three players, Windows, Radmin VPN)

*Fighting Force* (Core Design, 1997) is a 3D beat 'em up for the original
Sony PlayStation (PS1), for one or two players. Fighting Force Recompiled
makes a native Windows program of it from your own disc and adds a third
player. More about the game and the project: [README](../README.md).

For players. Building by hand, the command-line match and the developer
notes are in [WINDOWS_NETPLAY.md](WINDOWS_NETPLAY.md).

**Got the prebuilt ZIP** (`FightingForce3P-v<version>-playtest-windows-x64.zip`)?
Follow its `START_HERE.txt`: extract it, double-click `Play.bat` and choose
your disc's `.cue` once; there is no `Setup.bat` and nothing is built. From
*Mods: nothing to set* on, this guide applies to it too, except that its files
are in the extracted folder instead of `C:\RecompDev\FightingForceRecomp`,
and it has no `Take_Match_Save.bat`. The rest of this guide is for the
self-building ZIP.

## What everyone needs

- Windows 10 (version 1903 or newer) or 11, 64-bit; internet for the first
  setup.
- Your own *Fighting Force (USA)* disc dump: one `.cue` file and all **27**
  `.bin` tracks in one folder (the Redump layout). Nothing in the ZIP comes
  from the game; every player builds the game from their own disc. Netplay
  checks the disc's track layout, so everyone needs this same dump.
- The **same ZIP** as everyone else: its name ends with the version
  (`FightingForce3P_<version>.zip`).
- To play online: [Radmin VPN](https://www.radmin-vpn.com/).

## The first start on Windows

1. Put the ZIP and the folder with your disc (the `.cue` and its 27 `.bin`)
   **side by side in one folder** with a short path (Desktop or Downloads;
   Windows cannot extract paths over 260 characters), then **Extract All**
   (right-click the ZIP). Not from inside the ZIP, and not into Program
   Files.
2. Double-click **`Setup.bat`**. If Windows says it protected your PC:
   *More info* → *Run anyway* (the scripts are not signed).
3. It **finds your disc by itself** (it looks in its own folder, the one
   above and the one above that, and their subfolders, for a `.cue` with 27
   `.bin` files beside it). If it does not, it asks: **drag your `.cue` file
   into the window** and press Enter. If fewer than 27 `.bin` files are next
   to it, it says so and asks again. The disc path is remembered for
   updates.
4. **Wait.** The first time it downloads the compiler once and builds the
   game for your PC. It installs into `C:\RecompDev\FightingForceRecomp`
   (about 1 GB on drive C:; the build keeps its own copy of your disc) and
   puts **"Fighting Force"** on your desktop, with the game's title logo as
   its icon (made from your own disc at the end of the setup). If it stops
   while downloading (for example "504"), run `Setup.bat` again later.
5. Double-click **"Fighting Force"**. A black window opens first: keep it
   open while you play (closing it closes the game). The launcher opens.
   Your controller: on the first screen, card **PLAYER 1**, choose it below
   the controller picture; **Configure** changes the buttons. The keyboard works too:
   arrows, X = ✕, S = ○, Z = □, A = △, Return = Start. Then **PLAY**.
   In a level, **Start pauses the game for everyone**; only the player
   who paused can resume (Start) or quit (Select → QUIT). Player 3 can pause too.

The first time the game opens a netplay port, Windows asks about the
firewall: tick **Private** and **Public**, then **Allow access**. Pressed
Cancel? *Windows Security → Firewall → "Allow an app through firewall" →
Change settings* → tick `FightingForce_Recompiled` for Private and Public.

## Mods: nothing to set

The third player and Quick Start are on out of the box: no start-up videos,
no title screen, straight to the fighter select. Alone on one PC, players 2
and 3 join on the fighter select with ✕ ("PLAYER 2 PRESS X TO JOIN") and
leave with △ before they confirm. Player 1's △ opens the original main menu
(options, memory card). In the launcher, *Mods → Quality of Life → Quick
Start → "Straight to fighter select"* off gives the PS1 menus back; in a
match everyone must use the same setting.

The picture has no PS1 dither pattern (true colour). If you want it back:
*Mods → Image Filtering*, turn the mod on, *Dithering → Native (PS1)*. Picture
settings like this one are each player's own, also in a match.

**Three players on one PC:** the first gamepad is player 1 and the second is
player 2 by themselves. Player 3's gamepad you choose once in the launcher:
first screen, tab **PLAYER 3**, below the controller picture (scroll the card
down if the row is hidden); **Configure** changes its buttons. The framework
has no automatic choice for player 3.

## Starting a match

0. **Radmin VPN.** Everyone starts Radmin VPN. One player creates a network,
   the other two join it. Each PC now has a `26.x.x.x` address, shown in
   Radmin VPN.
   **Online, every PC uses its own PLAYER 1 card** for its controller (the
   PLAYER 2 / PLAYER 3 tabs are for extra players on one PC).
1. **The host** (player 1): the "Fighting Force" launcher → **NETPLAY** →
   **LAN / Direct IP** → type your name, Save → **Host Lobby**, Max Players
   **3**, **Create**.
2. **The others**: **NETPLAY** → **LAN / Direct IP** → your name, Save →
   **Join Direct**: Host IP = the host's `26.x.x.x` address, Port **7777**.
3. When all three are seated, **the host presses PLAY**.
4. **In the game**: the fighter select with everyone (no PRESS START, no
   player-count menu). Each player moves their own cursor and confirms with
   ✕; the three fighters must be different. The level starts when everyone
   has picked. After a game over or the ending the game comes back to this
   screen with the same players; after the match everyone returns to the
   room.

Ports, if something else on a PC uses them: the room is on UDP **7777**,
guests take **7778** and up, and rooms announce themselves on **48777**. If
*"Port 7777 is already in use"* appears, another program holds it: type
another port (for example 7790) in Host Lobby → Port, and the same port in
Join Direct → Port.

The room's chat repeats "<name> has joined." every few seconds while guests
wait: a harmless launcher display bug.

## Saves

Both kinds stay on the **host's** PC:

- **Checkpoints** (the game's own saves): after some levels the game asks
  "DO YOU WISH TO SAVE THE GAME?" — choose YES, then ✕ SAVE. The save holds
  everyone's fighter, score and health, the third player's too.
- **Save anywhere** (save states): the host presses **F7**, picks a slot
  with the number keys and presses **S** to save or **L** to load. All three
  games save or load together. Only the host can do this.

**Continuing next time.** The same host and players: start the match; on the
fighter select the host presses **△**, then **OPTIONS → LOAD MEMORY CARD
DATA → ✕ LOAD**, ✕, then △. The game continues at the saved level.

**Someone else hosts next time.** After a match each guest has a copy of the
host's card. Double-click `windows\Take_Match_Save.bat` (in
`C:\RecompDev\FightingForceRecomp`): it shows what your card and the match's
copy hold, asks, then makes the match's save your card 1 and keeps your old
card as a backup.

Never **Browse** your memory card to `saves\netplay\card1.mcd` in the
launcher: that file is the match copy, and the next match you join
overwrites it.

## The rules of the three-player game

- Three different fighters out of Hawk, Mace, Smasher and Alana.
- **Who is who:** on the fighter select, the ring under your cursor's
  fighter: **red = player 1 (the host), green = player 2, yellow = player 3**.
  In a level, your fighter's name and health bar: **player 1 top left,
  player 2 top right, player 3 at the bottom**.
- Everyone sees the same screen; the camera keeps the three of you in view,
  so nobody can walk far away from the others.
- **Friendly fire** is on, as in the original two-player game: players can
  hit each other. To turn it off: launcher, *Mods → Quality of Life → Quick
  Start*, heading *Gameplay*, *"Friendly fire"* off. Then your punches, kicks, thrown objects and
  shots reach only the enemies (enemies still hit everyone). In a match
  everyone must use the same setting, or the match does not start.
- On your own PC you are player 1: your controller and keyboard drive your
  own fighter.
- If a player drops out, the match ends for everyone. Start again and load
  the last checkpoint or save state.

## Updating

Extract the new ZIP and run its `Setup.bat`. It updates the folder, keeps
your saves, disc copy and disc path, and builds again. **Everyone in a match
needs the same version.**

## If something goes wrong

| What happens | What to do |
|---|---|
| `Setup.bat` stops | Select all the text in its window, copy it and send it to whoever sent you the ZIP: it says what went wrong. |
| "Only N .bin files next to that .cue" | The dump needs all 27 tracks in one folder with the `.cue`. |
| "Fighting Force is still running" | Close the game and its launcher, then run `Setup.bat` again. |
| Setup was closed early, or drive C: ran full, during the first setup, and the game then has no music or will not start | Delete the folder `C:\RecompDev\FightingForceRecomp\disc` and run `Setup.bat` again (it copies the disc again). |
| Guests wait forever in Join Direct | The host's firewall (see the first start) or a wrong address: check the host's `26.x.x.x` in Radmin VPN. |
| The match will not start, or the games drift apart | Not everyone has the same ZIP version, the same disc dump or the same Quick Start setting. Compare the ZIP names; `FightingForceRecomp\SOURCE_VERSION` holds the version. |
| "Port 7777 is already in use" | Another program holds it: another port in Host Lobby and Join Direct (see Starting a match). |
| Online the game is slow, the sound stutters, or buttons answer late | Note when it happens, keep playing the same match for a while, then everyone closes the game and sends the report ZIP their black window shows (see the next row), plus each PC's ping to the host in Radmin VPN. Do not switch on "Disable Rollback" to fix it: in our test that mode ran much slower on a link with lost packets. |
| The game closes by itself, or in a match everyone is sent back to the room with "The match ended: ..." | When one PC's game crashes or stops responding, the other PCs end the match and go back to the room with that message, so it looks as if every game crashed at the same moment. Write down the message (or take a photo of it). Then, on **every** PC, close the game before anyone starts another match. The black window that started the game then packs a **report ZIP** into `C:\RecompDev\FightingForceRecomp\reports` and shows it: everyone sends theirs. It holds `logs\last_run.txt` (the game's messages), `psx_last_run_report.json` (how the game ended; it helps show which PC had the problem), `psx_crash.txt` if the game really crashed in that run, and the version. Started the game with `Play.bat` or another way? Double-click `windows\Report.bat` after closing it. |
| Something odd in the game | Note what happened and when, take a screenshot, and send both. |
