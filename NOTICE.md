# Notices

## This project

This project's own work is under the MIT licence in [`LICENSE`](LICENSE).
That work is:
- the mod plugins (`src/mods/`) and their packages (`mods/preloaded/packages/`);
- the tools and tests (`tools/`) and the Windows start files (`windows/`);
- the guides (`docs/`, `README.md`, `packaging/release/`);
- the recompiler configuration this project wrote: the seeds, symbols, AOT
  profile, and the project's changes to `game.toml` and `game_3p.toml`.

## Files from the PSXRecomp project scaffold

These files were created by PSXRecomp's new-project scaffold:
- `CMakeLists.txt`, `codegen_setup.c`, `codegen_setup.h`;
- `game_options.toml`, `catalog_identity.json`, `.recomp.json`,
  `framework_pins.txt`;
- `assets/psxrecomp.*`;
- `scripts/package_release.sh`, `tools/sync_symbols.py`;
- `packaging/linux/`, and the first versions of `game.toml`,
  `symbols.toml`, `psx_symbols.h`, `packaging/release.toml` and
  `packaging/release/START_HERE.txt`.

They keep PSXRecomp's terms (below). Where this project's changes to them can
be separated, those changes are offered under MIT.

## Framework and libraries (git submodules)

| Part | Licence |
|---|---|
| [PSXRecomp](https://github.com/mstan/psxrecomp) (`psxrecomp/`) | PolyForm Noncommercial 1.0.0, with additional permissions for voluntary donations and advertising (`psxrecomp/LICENSE`, `psxrecomp/PERMISSIONS.md`) |
| [recomp-ui](https://github.com/RetroPortingToolKit/recomp-ui) (`recomp-ui/`) | MIT |
| recomp-net (`psxrecomp/lib/recomp-net/`) | MIT |
| retcomm-rbengine (`psxrecomp/lib/retcomm-rbengine/`) | MIT |
| OpenBIOS (`bios/openbios.bin` in a build) | MIT (`bios/OpenBIOS.LICENSE`) |

Third-party components vendored into the PSXRecomp runtime keep their own
terms (`psxrecomp/runtime/licenses/`, `psxrecomp/THIRD_PARTY_ATTRIBUTION.md`).

**A built game is noncommercial.** It links the PSXRecomp runtime, so PSXRecomp's
PolyForm Noncommercial terms apply to it. Under those terms:
- it may be used and shared only for noncommercial purposes;
- payment must never be required to get, access or play it, or its updates,
  features or content.

## The game

*Fighting Force* (Core Design, Eidos Interactive, 1997) and its trademarks,
code, art, music and data belong to their respective owners. This is an
unofficial fan project, not affiliated with or endorsed by them.

This repository contains no game data and no game code:
- no disc image or anything extracted from the disc;
- no recompiled C (`generated/`);
- no overlay captures;
- no BIOS dump.

The screenshots in `docs/images/` show the original game's characters and
artwork; they belong to the game's owners and are shown only to describe this
project.

Every build is made from the builder's own disc. A prebuilt package made with
`windows/Make_Release.bat` contains compiled game code built from that disc.
It is not distributed from here.

## Reference

The existing two-player port
[alexbeavs-ps1-ports/fighting-force-recomp](https://github.com/alexbeavs-ps1-ports/fighting-force-recomp)
(GPL-3.0) was read as a source of knowledge about the game. No files were
copied from it.
