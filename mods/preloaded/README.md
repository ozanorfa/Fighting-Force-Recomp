# Preloaded mods

This project's own mod packages. The build copies `mods/preloaded/packages`
next to the game executable as `mods/bundled/`, together with the framework's
built-in `psx.*` packages. That tree is build output: every build wipes and
re-stages it, so nothing placed there by hand survives.

| Package | On by default | What it does | Plugin source |
|---|---|---|---|
| `ff.third-player` | yes | A third player: online, the match's third seat; on one PC, controller 3 joins on the fighter select screen | `src/mods/third_player.c` |
| `ff.quick-start` | yes | No start-up videos or cutscenes, straight to the fighter select, faster loading offline, and the friendly-fire option | `src/mods/quick_start.c` |

Both are trusted static plugins compiled into the runtime. Their manifests
(`packages/<id>/<version>/manifest.toml`) hold the names, descriptions and
options the launcher's Mods page shows. Online, every player in a match must
use the same options for these two packages.

Player-installed `.psxmod` archives live in `mods/installed/`. The launcher
owns that folder and no build ever touches it. Install them through the
launcher's Mods manager instead of committing them here.

See `psxrecomp/docs/MOD_PACKAGES.md`.
