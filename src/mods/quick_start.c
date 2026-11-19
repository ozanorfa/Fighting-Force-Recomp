/*
 * Trusted static plugin "ff.quick-start" (package ff.quick-start, feature
 * quick-start): fewer waits between starting the game and playing.
 *
 * Both skips press the game's OWN skip button at the one place the game
 * polls it, so the game takes its own skip path (the same as a player
 * pressing it on the first frame). Nothing else sees the press, no guest
 * state is added, and the result is the same on every machine: the skips are
 * safe in netplay, where the title netplay profile runs this plugin as its
 * loading plugin (third_player.c).
 *
 *  - Start-up videos (boot EXE SLUS_004.33): the movie loop
 *    FUN_8001152c asks FUN_800112ac for a button: 1 = START (fade out, then
 *    `start()` skips the intro film too), 2 = X (this movie only). We return
 *    1. GAME.EXE later occupies the same addresses; the residency guard keeps
 *    the override to the boot EXE.
 *  - In-game cutscenes (GAME.EXE): FUN_80015720(n) plays cutscene n (level
 *    id 0x1B + n: 0 = helicopter before level 1, 1..5 between levels, 6/7 the
 *    ending). Each frame it reads every player's pad with FUN_8005f5ac and
 *    ends the scene on X or START (mask 0x4008). We add X to that one call
 *    (return address 0x80015B54); START would also make the game remember
 *    the skip for cutscene 7 (DAT_8008d4dc), X does not.
 *  - Straight to fighter select (option straight_to_select): the title
 *    FUN_80069f78 waits in its own frame loop for START, reading P1 through
 *    FUN_80068114(0) (call at 0x8006A048, return 0x8006A050), which reads the
 *    pad with FUN_8005f5ac (return 0x80068138; FUN_80068114 keeps its
 *    caller's return address at sp+0x14). We add START to that one read when
 *    the previous read (DAT_80092790[0]) had none, so the title sees a fresh
 *    press on its second frame. The menu part (no "1 / 2 PLAYER GAME" choice,
 *    others join on the select screen) is in third_player.c, which owns the
 *    main menu and select wrappers; it asks ff_qs_straight_to_select().
 *  - P3's pause menu (third_player.c): the framework arms one override per
 *    guest function, so this package's FUN_8005f5ac wrap hands each call to
 *    ff_p3_pad_read first; inside the pause menu while it is P3's, that
 *    reads seat 3 instead of P1's pad. Without this package P3's START does
 *    not pause.
 *  - Loading screens: host wall-clock pacing of sustained CD loads
 *    (psx_mod_set_load_acceleration). Every guest frame, CD deadline and
 *    interrupt still runs; only the real-time wait is shorter. Offline only:
 *    a netplay match is paced by its peers.
 *
 * Every address and call site here was checked in Ghidra and at run time
 * on the USA disc (SLUS-00433).
 */
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "func_override.h"
#include "mod_netplay.h"
#include "mod_plugins.h"

#define QS_PLUGIN_ID   "ff.quick-start"
#define QS_PACKAGE_ID  "ff.quick-start"
#define QS_FEATURE_ID  "quick-start"

/* Boot EXE (SLUS_004.33): movie loop's button check. */
#define FN_BOOT_MOVIE_BUTTON 0x800112ACu
static const uint32_t G_BOOT_MOVIE_BUTTON[4] = {0x27BDFFE8u, 0xAFBF0014u, 0xAFBE0010u, 0x03A0F021u};
#define BOOT_BUTTON_START    1u

/* GAME.EXE: pad read (buttons active-high) and the cutscene's call of it. */
#define FN_PAD_READ          0x8005F5ACu
static const uint32_t G_PAD_READ[4] = {0x27BDFFE8u, 0x00802821u, 0x3C068009u, 0x24C64770u};
#define RA_CUTSCENE_POLL     0x80015B54u /* jal FUN_8005f5ac at 0x80015B4C */
#define PAD_CROSS            0x4000u
#define PAD_START            0x0008u
#define RA_MENU_PAD          0x80068138u /* FUN_80068114's pad read */
#define RA_TITLE_P1          0x8006A050u /* the title's FUN_80068114(0) */
#define G_MENU_PREV          0x80092790u /* u32[2]: last buttons seen by FUN_80068114 */

static int g_skip_intro = 1;
static int g_skip_cutscenes = 1;
static int g_straight = 0; /* 0 until the feature is activated */
static int g_friendly_fire = 1; /* the original rule until the feature says otherwise */
static int g_pad_hook = 0; /* 1 while this session's plan has Quick Start (its FUN_8005f5ac wrap is armed) */

static _Atomic uint32_t g_dbg_intro_skips = 0;
static _Atomic uint32_t g_dbg_cutscene_skips = 0;
static _Atomic uint32_t g_dbg_title_skips = 0;
/* TEST / SETUP ONLY (debug command): the title's P1 pad reads seen, and a
 * switch that keeps this run on the title (no START injected) so
 * tools/ff_icon.py can take the title logo for the desktop icon. Host-side;
 * normal play and netplay never set it. */
static _Atomic uint32_t g_dbg_title_polls = 0;
static _Atomic int g_dbg_hold_title = 0;

/* For third_player.c: skip the player-count menu, join on the select screen. */
int ff_qs_straight_to_select(void) { return g_straight; }
/* For third_player.c: 0 = players' attacks never reach other players. Part
 * of this package's options, so of the match identity online. */
int ff_qs_friendly_fire(void) { return g_friendly_fire; }
/* For third_player.c: the framework arms one override per guest function, and
 * FUN_8005f5ac's is this package's wrap_pad_read, which hands each call to
 * ff_p3_pad_read first (P3's pause menu reads seat 3). 1 while it is armed. */
int ff_qs_pad_hook(void) { return g_pad_hook; }
int ff_p3_pad_read(struct CPUState* cpu); /* third_player.c: 1 = handled */

static int option_bool(const char* id, int fallback) {
    char buf[16];
    if (!psx_mod_option_value(QS_PACKAGE_ID, QS_FEATURE_ID, id, buf, sizeof(buf))) return fallback;
    return strcmp(buf, "true") == 0 || strcmp(buf, "1") == 0;
}

static int option_int(const char* id, int fallback) {
    char buf[16];
    if (!psx_mod_option_value(QS_PACKAGE_ID, QS_FEATURE_ID, id, buf, sizeof(buf))) return fallback;
    return atoi(buf);
}

/* Before every session's plan, also one without Quick Start: third_player.c
 * reads g_straight in every session, so a stale 1 from an earlier session
 * (offline with Quick Start, then a match without it) must not survive.
 * qs_activate sets the flags again for a plan that has Quick Start. */
static void qs_session_reset(void) {
    g_skip_intro = 0;
    g_skip_cutscenes = 0;
    g_straight = 0;
    g_friendly_fire = 1;
    g_pad_hook = 0;
}

static void qs_activate(void) {
    g_skip_intro = option_bool("skip_intro", 1);
    g_skip_cutscenes = option_bool("skip_cutscenes", 1);
    g_straight = option_bool("straight_to_select", 1);
    g_friendly_fire = option_bool("friendly_fire", 1);
    g_pad_hook = 1;
    int speed = option_int("load_speed", 0);
    if (speed < 0 || speed > 64) speed = 0;
    if (!psx_mod_netplay_is_active() && speed != 1)
        (void)psx_mod_set_load_acceleration((uint32_t)speed, 0);
    psx_mod_counter_add("ff.qs.activated", 1);
}

static int wrap_boot_movie_button(struct CPUState* cpu) {
    func_override_call_original(cpu);
    if (g_skip_intro && cpu->gpr[2] == 0) {
        cpu->gpr[2] = BOOT_BUTTON_START;
        atomic_fetch_add(&g_dbg_intro_skips, 1);
    }
    return 1;
}

static int wrap_pad_read(struct CPUState* cpu) {
    const uint32_t ra = cpu->gpr[31];
    const uint32_t sp = cpu->gpr[29];
    if (ff_p3_pad_read(cpu)) return 1; /* P3's pause menu (reads seat 3 instead) */
    func_override_call_original(cpu);
    if (g_skip_cutscenes && ra == RA_CUTSCENE_POLL && !(cpu->gpr[2] & 0x4008u)) {
        cpu->gpr[2] |= PAD_CROSS;
        atomic_fetch_add(&g_dbg_cutscene_skips, 1);
    }
    const int title_p1 = ra == RA_MENU_PAD && psx_mod_read_word(sp + 0x14u) == RA_TITLE_P1;
    if (title_p1) atomic_fetch_add(&g_dbg_title_polls, 1);
    if (g_straight && title_p1 && !atomic_load(&g_dbg_hold_title) &&
        !(psx_mod_read_word(G_MENU_PREV) & PAD_START)) {
        cpu->gpr[2] |= PAD_START;
        atomic_fetch_add(&g_dbg_title_skips, 1);
    }
    return 1;
}

/* Debug command "ff.qs": "show" (default); "hold-title on|off" (setup / test
 * only: stay on the title screen). */
static int cmd_qs(const char* args, char* reply, int cap) {
    if (args && strncmp(args, "hold-title ", 11) == 0)
        atomic_store(&g_dbg_hold_title, strcmp(args + 11, "on") == 0);
    return snprintf(reply, (size_t)cap,
                    "skip_intro=%d skip_cutscenes=%d straight_to_select=%d intro_skips=%u cutscene_skips=%u "
                    "title_skips=%u title_polls=%u hold_title=%d friendly_fire=%d",
                    g_skip_intro, g_skip_cutscenes, g_straight, (unsigned)atomic_load(&g_dbg_intro_skips),
                    (unsigned)atomic_load(&g_dbg_cutscene_skips), (unsigned)atomic_load(&g_dbg_title_skips),
                    (unsigned)atomic_load(&g_dbg_title_polls), atomic_load(&g_dbg_hold_title), g_friendly_fire);
}

PSX_MOD_CONSTRUCTOR(register_ff_quick_start) {
    (void)psx_mod_register_activation_plugin(QS_PLUGIN_ID, qs_activate);
    (void)psx_mod_register_session_reset_plugin(QS_PLUGIN_ID, qs_session_reset);
    (void)psx_mod_register_function_override(QS_PLUGIN_ID ":intro", FN_BOOT_MOVIE_BUTTON,
                                             wrap_boot_movie_button, G_BOOT_MOVIE_BUTTON, 4,
                                             FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(QS_PLUGIN_ID ":cutscene", FN_PAD_READ, wrap_pad_read,
                                             G_PAD_READ, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_command("ff.qs", cmd_qs);
}
