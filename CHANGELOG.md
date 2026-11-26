# Changelog

A version here is the **netplay compatibility number**. Version `0.N.0` is
the build whose third-player mod reports `ff.third-player/N`. The number goes
up whenever what the game does changes. Every player in a match needs the
same version; a match between two versions does not start. The `VERSION` file
holds the current number. The two mod packages keep their own package version
(`0.1.0`).

Newest first. No version has been played on Windows yet: every test so far
ran on macOS.

## 0.12.0 — 2026-11-18 — not yet given to players

### Fixed
- **Player 3's fighter has the right clothes and skin in most levels.**
  - The game loads only the textures of player 1's and player 2's fighters
    at a level start. So a player 3 fighter that neither of them plays, such
    as Alana next to Hawk and Mace, was drawn with another texture set and
    looked like a metal robot.
  - The level now also loads player 3's fighter textures where the game has
    room for them: 18 of the 25 levels with the largest fighters.
  - The game's texture table and video memory are fixed in size. In 7
    crowded levels (5, 7, 8, 9, 11, 12 and 18) there is no room for a third
    fighter, and player 3 still looks wrong there. The game measures this at
    each level start and never overfills a table.
- **Setup accepts a disc path typed by hand** that is relative or has no
  drive letter. Before, the build read such a path from the install folder
  and stopped with "no disc at ...". A dragged `.cue` and the automatic
  disc search were not affected.

### Changed
- **README** reorganised, with pictures of the game and the launcher.
- **README: what the game and the project are.** New sections say that
  *Fighting Force* is a 1997 PlayStation (PS1) beat 'em up and how it plays,
  and what a recompilation is and is not. The players' guides and
  `START_HERE.txt` now open with the same short line about the game.

## 0.11.0 — 2026-11-17 to 2026-11-18

### Added
- **Weapon gift for player 3.** When a level starts after the previous one
  was finished, player 3 also finds a random weapon at their feet, as players
  1 and 2 do. A player who is down at that moment gets none.
- **Player 3 can pause.** START pauses the game for everyone with its own
  "PAUSED 3P" menu. Only player 3 can resume it or quit it. This needs Quick
  Start, which is on by default.
- **A ready-to-play friends' ZIP.** The maintainer can now cut one on a
  Windows PC with `windows/Make_Release.bat` (the framework's release
  packager). Friends extract it and point the launcher at their own disc. It
  is shared privately and never published here. Not built yet.
- **LICENSE (MIT) and NOTICE.md** for this project's own work and the
  framework's terms.

### Changed
- **The picture is drawn in true colour,** without the PS1 dither pattern.
  To bring it back: Mods → Image Filtering (turn it on) → Dithering →
  Native (PS1). Picture settings are each player's own, also in a match.
- **README and guides** rewritten for new readers.

## 0.10.0 — 2026-11-17

### Fixed
- **Player 3 starts a level the way players 1 and 2 do.**
  - After a level that ended with player 2 down, player 3 no longer starts
    frozen with health left and no continue prompt.
  - A player 3 who was down at the level end, or in a saved game, stays
    down instead of getting a free revive.

## 0.9.0 — 2026-11-14

### Fixed
- **Gun shots between player 3 and the others hurt** when friendly fire is
  on, as shots between players 1 and 2 always did.
- **The fighter select no longer waits for a player 3 whose controller was
  unplugged.**
- **Player 3's in-level state is cleared** at every cutscene and in the
  menus.

### Changed
- **Windows: every start keeps a log.** When the game closes, the start
  file packs a one-file report ZIP to send if something went wrong.
- **The players' guide** says what to send when a match ends on every PC.

## 0.8.0 — 2026-11-10

### Added
- **Friendly fire on/off** (Mods → Quality of Life → Quick Start, heading
  Gameplay).
  - On keeps the original rule.
  - Off: a player's punches, kicks, thrown objects, blasts and shots reach
    only the enemies. Enemies still hit everyone.
  - Everyone in a match needs the same setting.

## 0.7.0 — 2026-11-10

### Fixed
- **A rematch and a fresh start behave the same.**
  - The third-player mod set up its memory again at every session start,
    so after a rematch one PC could differ from a freshly started one.
    Its state now stays in one place and is cleared every session.
  - The high-score table no longer waits forever for a player 3 without a
    controller: the default name is kept and the entry ends.
  - The camera's centre of the three players no longer overflows when the
    players are far apart.

### Changed
- **The runtime overlay compiler is off** in the start files, as in every
  test. The game code is compiled at setup, so it only cost CPU.

## 0.6.0 — 2026-11-09

### Changed
- **No coloured rings under the players in a level.** The fighter select
  keeps them: red = player 1, green = player 2, yellow = player 3.
- **Player 2 takes the second gamepad by itself** on a fresh install.
- **Setup finds the disc by itself** next to the extracted ZIP, and makes
  the desktop icon from the title logo of the player's own disc.
- **`Play_with_log.bat`** keeps the game's messages. Online, every PC uses
  its own PLAYER 1 card.

## 0.5.0 — 2026-11-09

### Added
- **Straight to fighter select** (a Quick Start option, on by default): no
  PRESS START and no 1 / 2 PLAYER GAME menu.
  - Offline, players 2 and 3 join on the select screen with ✕ and leave
    with △.
  - Online, everyone in the match is in.
- **A self-building players' ZIP:** each player builds the game once from
  their own disc. Nothing to install.

### Changed
- **The third player is on by default.**

## 0.4.0 — 2026-11-09

### Fixed
- **A crash in a later level** (level 0x11), where the game spawned a guard
  with every enemy slot taken. This is a bug of the original game, which
  also happened with two players. Spawns now wait a frame for a free slot.
- **Friendly fire in crowds:** hits on and by player 3 test the players
  before the enemies, as the game's own hit test does.

## 0.3.0 — 2026-11-08

### Added
- **Player 3 in the high-score table,** with the game's own entry screen
  ("CONGRATULATIONS P3!").
- **Player 3 keeps their health between levels** (with the game's refill)
  and is saved to the memory card with players 1 and 2.
- **Coloured rings under the players** (red, green, yellow), so everyone
  finds their own fighter on the shared screen.
- **Match saves and controls for online play.** Saves stay on the host's PC,
  and save states are taken and loaded by the host for everyone.
- **Windows start files** for setup, play, host and join.

### Fixed
- **Player 3's score is reset at a new game.**

## 0.2.0 — 2026-11-08

### Added
- **Quick Start** (on by default):
  - no start-up logos and intro film, and no in-game cutscenes (it presses
    the game's own skip buttons);
  - faster loading screens offline.

## 0.1.0 — 2026-11-07 to 2026-11-08 — development

### Added
- **The recompiled game,** boot to gameplay, two players.
- **A third simultaneous player:**
  - their own fighter on the select screen, HUD panel, health and score;
  - moving, fighting and being fought, with the downed-player rules and
    continues of players 1 and 2;
  - a camera that keeps all three in view;
  - enemies that target player 3 too.
- **Netplay for three PCs** (rollback, LAN / Direct IP), with three seats in
  the launcher's LAN rooms.
- **A Windows build and Radmin VPN guide.**
