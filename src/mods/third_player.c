/* third_player.c — Fighting Force: a third player in two-player games.
 *
 * Trusted static plugin "ff.third-player" (package ff.third-player, feature
 * "p3"). Addresses are in GAME.EXE (loaded at 0x80010000 by the boot EXE's
 * LoadExec). Every address and structure here was checked in Ghidra and at
 * run time on the USA disc (SLUS-00433).
 *
 * Input path (netplay-safe): P3's buttons come from controller seat 2 of the
 * framework (sio_get_pad_buttons_slot(2)), which is the per-seat input that
 * netplay synchronizes. No host gamepad is read here.
 *
 * M1 input: seat 2 is sampled when the game samples P1/P2 (wrap of the pad
 * sampler FUN_8005f23c) into a P3 pad word in the game's cache format.
 * M2 spawn: in 2-player games P3 joins in record 2 (the first enemy record)
 * as a twin of P2's fighter. M3 control and combat: P3 runs through the
 * game's own player code as "player 1" in a view (p3_view_enter), is hidden
 * from code that treats records 2..7 as enemies, and is counted where the
 * code loops over the players.
 *
 * Determinism: all mutable plugin state lives in mod memory (guest RAM), so
 * savestates, rewind and rollback carry it. Host statics are limited to the
 * mod-memory address (fixed after activation) and debug-only mirrors.
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
#include "sio.h"

#define P3_PLUGIN_ID "ff.third-player"

/* Seat the third player reads (seat 0 = port 1, seats 1..4 = tap A..D with
 * the multitap on port 2). */
#define P3_SEAT 2

/* ---- GAME.EXE addresses (verified in Ghidra + at runtime) -------------- */
#define FN_PAD_SAMPLE  0x8005F23Cu /* per-frame pad cache fill (P1/P2) */
#define FN_SPAWN_POS   0x80014808u /* level start: place players */
#define FN_ENEMY_COUNT 0x8004BE28u /* live enemies in records 2..7 */
#define FN_WAVES       0x8004B228u /* enemy wave script; -1 = script ended */
#define FN_EXIT_LINE   0x8001886Cu /* called right after the level-clear test */

#define REC_BASE  0x800B4318u /* 8 character records */
#define REC_SIZE  0xF4u
#define P3_REC    2u          /* record the third player lives in */
#define RO_X      0x28u
#define RO_Z      0x30u
#define RO_TYPE   0x34u
#define RO_HEALTH 0x42u
#define RO_DESC   0xB4u       /* model descriptor pointer */
#define RO_DESC2  0xB8u
#define RO_CHAR   0xF0u       /* per-character data pointer */
#define DESC_SIZE 0x28u
#define P2_DESC   0x800A85B0u /* P2's writable model descriptor */
/* Fighter stats (u32 tables by type), copied by the game into descriptor
 * bytes 5 and 7; the damage function FUN_8001f230 reads them from row +0xB8
 * of the descriptor: Hawk 96/128, Mace 88/136, Smasher 112/112, Alana 80/144. */
#define STAT_STRENGTH 0x8008D4E0u /* descriptor byte 5: attack strength */
#define STAT_DEFENCE  0x8008D4F4u /* descriptor byte 7: defence (damage taken x n/256) */
#define CHAR_DATA 0x800B6E90u /* per-character data pointers (u32 table) */
#define G_PLAYERS 0x800940B0u /* number of players in the game (1/2) */
#define G_DEMO    0x80093D08u /* 1 = demo playback, 2 = demo recording */

/* M3: the player update and the enemy target choosers. */
#define FN_PLAYER_UPDATE  0x80024008u /* for p < players: update record p */
#define FN_NEAREST_PLAYER 0x80049B78u /* enemy: nearest living player */
#define FN_WEIGHTED_TGT   0x800531C4u /* enemy: weighted player/item choice */
#define FN_ENEMY_MELEE    0x8004930Cu /* enemy attack hits players p < players */
#define FN_ENEMY_SPECIAL  0x8004DB94u /* enemy special attack on players p < players */
#define FN_ENEMY_WALK     0x80047CE4u /* enemy walks toward a point (scans 2..7) */
#define FN_CAM_TARGET     0x800476E8u /* camera target; flags enemies 2..7 */
#define FN_ENEMY_HIT      0x80048DBCu /* enemy limb hit test: records 0 and 1 only */
#define FN_ENEMY_LOOP     0x8004A704u /* per-frame update of records 2..7 */
#define FN_MELEE_HIT      0x8001C35Cu /* player melee hit test, records 0..7 */
#define FN_HUD            0x800620BCu /* per-frame HUD: P1 left, P2 right */
#define FN_CONTINUE       0x80061E6Cu /* (p) continue prompt of a downed player */
#define FN_CAM_FOCUS      0x80047600u /* camera focus: living P1 / P2 or their midpoint */
#define FN_PLAYER_OUT     0x800196A4u /* (p) 1 = player p out (type -1 or health < 1) */
#define FN_PAST_X_LO      0x800472A8u /* area tests: living P1 / P2 past a line; */
#define FN_PAST_X_HI      0x8004732Cu /* dead players do not block */
#define FN_PAST_Z_LO      0x800473B0u
#define FN_PAST_Z_HI      0x80047434u
#define FN_LIFE_SOUND     0x800149ECu /* extra life / continue jingle */
#define FN_SEPARATE       0x8001B2C4u /* (r) push record r out of others */
#define G_GAME_OVER       0x80093DD4u /* 0 = playing; set when both players are out */
#define G_LIVES           0x80093D50u /* shared lives / credits */
#define G_LEVEL_END       0x800941B8u /* level-end sequence counter (0 = playing) */
#define G_LEVEL_ID        0x80093D58u /* level being played (DAT_80093d58) */
#define G_LOADED_GAME     0x80094000u /* byte: 1 = this game was loaded from the memory card */
#define RO_HEALTH_A       0x44u       /* health copies the new game sets with +0x42 */
#define RO_HEALTH_B       0x46u
#define RO_HEALTH_MAX     0x48u
#define G_FRAME           0x80093DD0u
#define G_CONTINUE_TEXT   0x800924ECu /* char*: text after the X glyph in "]%s" */
#define G_SEPARATE_COUNT  0x80093D54u
#define G_FLASH           0x80094190u
#define RO_DOWN           0x77u       /* byte: set when a fighter's KO has played out */
#define RO_TALLY          0x8Au       /* s16: level-end bonus units left (x 250) */
#define RO_TALLY0         0x8Cu       /* s16: bonus units at the start of the tally */
#define RESPAWN_HEALTH    0x17Cu

/* The level loop FUN_80014a34's weapon gift and Start pause (both for
 * p < players there; P3 gets them in wrap_pad_sample / wrap_exit_line). */
#define FN_RAND           0x8006D348u /* libc rand() */
#define FN_OBJ_NEW        0x800167C4u /* (kind, x, 1, z, [sp+0x10] 0) -> object index or -1 */
#define FN_PAUSE          0x8005DBBCu /* pause menu (own frame loop): P2's if bit 1 of G_PAUSE, else P1's */
#define FN_PAUSE_END      0x8005E138u /* first address after it */
#define G_GIFT            0x80093D64u /* 1: the last level was left over the exit line (not level 0xD) */
#define G_GIFT_TABLE      0x8007A114u /* s16[4] weapon kinds: 499, 504, 500, 501 */
#define G_PAUSE           0x80093DD8u /* byte: 1 << p player p paused, 4 << p its pad gone */
#define G_PAD_TYPE        0x80094114u /* byte: type of the last pad read (4 digital, 0xFF none) */
#define G_ATTRACT         0x80093D1Cu /* attract demo index, -1 in a game */
#define G_PAUSED_1P_TEXT  0x800924E4u /* char*: "PAUSED 1P" (FUN_80058c14 draws it at y 0x73) */
#define G_PAUSED_2P_TEXT  0x800924E8u /* char*: "PAUSED 2P" */
#define RO_HAND_B         0x7Du       /* byte: object held (kind 504) */
#define RO_HAND           0x7Eu       /* byte: object held (other kinds) */
#define RA_LOOP_TOP       0x80014E00u /* FUN_8005f23c at the top of the level loop */
#define RA_EXIT_LINE_PLAY 0x800153D8u /* FUN_8001886c while the level is played */
#define WEAPON_B_KIND     0x1F8u

/* Fighter loading (per-character init PTR_8007a0cc[type], e.g. 0x80013A98):
 * the fighter's CHAR/<NAME>.WAD = ADT (moves) + DUMMY.DAT + ACH (model). */
#define FN_LOAD_MODEL     0x80060110u /* (ach name, type, texture slot): model -> model heap */
#define FN_LOAD_MOVES     0x8001721Cu /* (adt name, type): move table DAT_800b3be8[type] */
#define FN_SET_ANIM       0x80019F00u /* (record, move table, state) */
#define G_WAD_BASE        0x80094818u /* current WAD: entry offsets are relative to it */
#define G_WAD_DIR         0x80094038u /* current WAD directory */
#define G_WAD_ENTRY_SIZE  0x80094570u
#define G_WAD_SIZE        0x800947D0u
#define G_MODEL_HEAP      0x80094844u
#define G_LOAD_BAR        0x80093566u /* s16: > 0 draws a loading bar per lookup */
#define G_MOVE_TABLES     0x800B3BE8u /* u32 per type: move table */
#define FIGHTER_WAD_MAX   0x40000u    /* largest fighter WAD: ALANA.WAD 255,596 bytes */
#define FIGHTER_MODEL_MAX 0x4000u     /* largest model: ALANA.ACH 7,984 bytes */
static const char* const FIGHTER_NAME[4] = {"HAWK", "MACE", "SMASHER", "ALANA"};

/* Fighter select (front end FUN_8004fbcc, menu state pointer 0x800927EC). */
#define FN_SELECT         0x80050798u /* select state: P1/P2 cursors, confirm */
#define FN_MAIN_MENU      0x80050270u /* main menu state */
#define FN_SLOT_FREE      0x8004F4B8u /* (slot, cursors, n) -> 0 if a cursor holds slot */
#define FN_LINEUP         0x80058E24u /* (cursors, n): draws the 4 fighters, lights cursors */
#define FN_TITLE_TEXT     0x80061ADCu /* (text, y): centred text */
#define FN_SND_MOVE       0x8004F468u
#define FN_SND_CONFIRM    0x8004F490u
#define FN_SOUND          0x80056670u /* (id, volume, pitch) */
#define G_CURSORS         0x8009277Cu /* u32[2]: P1 / P2 cursor slot 0..3 (= line-up record) */
#define G_SEL_PLAYERS     0x80092784u /* players chosen in the menu (1 / 2) */
#define G_SEL_DONE        0x80092788u /* 1 = all confirmed: leave the front end */
#define FN_FRONT_END      0x8004FBCCu /* title -> menus -> select; returns when a game starts */
#define FN_CUTSCENE       0x80015720u /* FUN_80015720(n): in-engine cutscene n, between levels */
#define FN_SEL_CAMERA     0x8004FA10u /* select camera to the cursors' fighters */
#define FN_MENU_PAD       0x8005F5ACu /* (port) -> buttons, active-high */
#define G_MENU_STATE      0x800927ECu /* front-end state function pointer */
#define G_SEL_MASK        0x8009272Cu /* bit p: player p confirmed */
#define G_PADS            0x8009400Cu /* pads connected (1 / 2) */
#define G_TWO_P_MODE      0x80093D5Cu /* 1 after "2 PLAYER GAME", 0 after "1 PLAYER GAME" */
#define G_MENU_PREV       0x80092790u /* u32[2]: last buttons seen by the menus, per player */
#define G_PAD_PORT        0x80094288u /* u32[2]: port of player p */
#define G_SEL_CONFIRMED   0x8009272Cu /* bit p = player p confirmed */
#define G_LINEUP          0x800927A4u /* u32[4]: fighter type per slot */
#define G_LINEUP_PHASE    0x80093580u /* pulse phase of an unconfirmed cursor */
#define G_SINE            0x800802D0u /* s16 sine table */
#define RO_LIGHT          0x9Du       /* 3 bytes: line-up model brightness offset */
/* Ring markers under the cursor fighters: TARGET.MOD (P1, red) and
 * TARGETG.MOD (P2, green) from the front-end models; TARGETW.MOD (white)
 * is only in the level model WADs, e.g. MODFILES/MODELS11.WAD. */
#define FN_LOAD_OBJ       0x8005FF2Cu /* (mod name, model index, texture slot) in place */
#define FN_MTX_PUSH       0x80010040u
#define FN_MTX_TRANSLATE  0x800100C4u /* (x, y, z) */
#define FN_MTX_ROT_X      0x800101CCu
#define FN_MTX_ROT_Y      0x8001033Cu
#define FN_DRAW_MODEL     0x80067B8Cu /* (model, 0, 0) */
#define FN_MTX_POP        0x80010028u
#define G_MODELS          0x80081720u /* u32 per model index */
#define RING_MODEL        0x379u      /* TARGETW.MOD's index in the level loaders */
#define G_CAM_X           0x8008D4A4u
#define G_CAM_Y           0x8008D4A8u
#define G_CAM_Z           0x8008D4ACu
#define G_RING_SPIN       0x8009357Cu
static const uint8_t FIGHTER_SLOT[4] = {0x24, 0x25, 0x26, 0x27};
#define FN_TEXT           0x80061B7Cu /* (text, x, y) -> x after the text */
#define FN_SCORE_TEXT     0x80061D0Cu /* (score text, x, y) */
#define FN_TEXT_WIDTH     0x8005EFB8u /* (text) -> width in pixels */
#define FN_HEALTH_BAR     0x80063100u /* (y << 16 | x, record) left-aligned bar, <= 96 px */
#define FN_PLAYER_HIT     0x8001BFA0u /* thrown object hit test, records 0..7 */
#define RO_HIT_MASK       0x52u       /* u16: bit i = record i already hit by this attack */
#define OBJ_BASE          0x800B89F0u /* level objects, 128 x 0x24; +0x1C s16 = thrower */
#define OBJ_SIZE          0x24u
#define OBJ_MAX           128u
#define OBJ_THROWER       0x1Cu
#define G_OBJ_COUNT       0x8009403Cu
#define RO_AI_LEVEL       0xB8u       /* = RO_DESC2: stats row; enemies: aggression 0..4 */
#define RO_AI_STEP        0xBCu
#define G_NEAREST_IDX     0x800942E0u /* FN_NEAREST_PLAYER result index */
#define G_MAX_ATTACKERS   0x80093E18u /* active attackers allowed per player */
#define RO_LAST_TARGET    0x74u       /* byte: index this record last hit */
#define RO_HELD_BY        0x78u       /* byte: index of the record holding this one */
#define RO_HOLDING        0x7Au       /* byte: index of the record this one holds */
#define RO_ATTACKER       0x81u       /* byte: index of the last attacker */
#define RO_TGT_KIND       0xA4u       /* enemy: 1/2 = player, 4 = item */
#define RO_TGT_IDX        0xA8u       /* enemy: target index */
#define REC_ANIM_BASE     0x800BA210u /* per-record block, 8 x 0x1F8 bytes */
#define REC_ANIM_SIZE     0x1F8u

/* P2's per-player globals (index 1). During the P3 pass P3 runs as
 * player 1, so these hold P3's values; P2's are saved and put back. */
typedef struct { uint32_t addr, size; } Slot;
static const Slot P1_SLOTS[] = {
    {0x80093A14u, 4}, /* pad cache word (FUN_8005f558 for P2's port) */
    {0x80094554u, 4}, /* DAT_80094550[1]: item in reach */
    {0x80094034u, 4}, /* DAT_80094030[1]: score */
    {0x80093FFCu, 4}, /* DAT_80093ff8[1] */
    {0x8008E508u, 4}, /* DAT_8008e504[1] */
    {0x80094106u, 6}, /* action-button block [1] (0x80094100 + 6) */
    {0x80094740u, 8}, /* HUD score text [1] (FUN_8005e9e8 formats score[p]) */
};
#define SLOT_SCORE      0x10u /* offsets of P3's values in M_P3_SLOTS */
#define SLOT_ITEM       0x08u
#define SLOT_SCORE_TEXT 0x30u
#define N_SLOTS (sizeof(P1_SLOTS) / sizeof(P1_SLOTS[0]))

/* Residency guards: first words of each hooked function in GAME.EXE. The
 * boot EXE occupies the same addresses before LoadExec. */
static const uint32_t G_PAD_SAMPLE[4]  = {0x27BDFFE8u, 0xAFBF0010u, 0x3C038009u, 0x8C633D08u};
static const uint32_t G_SPAWN_POS[4]   = {0x3C028009u, 0x8C423D58u, 0x27BDFFC0u, 0xAFB10024u};
static const uint32_t G_ENEMY_COUNT[4] = {0x00002821u, 0x34040002u, 0x34070033u, 0x2406FFFFu};
static const uint32_t G_WAVES[4]       = {0x3C028009u, 0x8C423D40u, 0x27BDFFC0u, 0xAFBF003Cu};
static const uint32_t G_EXIT_LINE[4]   = {0x27BDFFE8u, 0xAFBF0010u, 0x3C06800Bu, 0x24C64318u};
static const uint32_t G_PLAYER_UPDATE[4]  = {0x27BDFEE0u, 0xAFBF011Cu, 0xAFBE0118u, 0xAFB70114u};
static const uint32_t G_NEAREST_PLAYER[4] = {0x27BDFFF0u, 0x00804021u, 0x3C038009u, 0x8C6340B0u};
static const uint32_t G_WEIGHTED_TGT[4]   = {0x27BDFFE0u, 0xAFB00010u, 0x00808021u, 0x00101100u};
static const uint32_t G_ENEMY_MELEE[4]    = {0x27BDFF68u, 0xAFA40028u, 0x8FA40028u, 0x3C07800Bu};
static const uint32_t G_ENEMY_SPECIAL[4]  = {0x27BDFFD0u, 0xAFB40020u, 0x0080A021u, 0x00141100u};
static const uint32_t G_ENEMY_WALK[4]     = {0x27BDFF90u, 0xAFBE0068u, 0x0080F021u, 0x001E1100u};
static const uint32_t G_CAM_TARGET[4]     = {0x3C028009u, 0x8C424840u, 0x3C038009u, 0x9463D490u};
static const uint32_t G_ENEMY_HIT[4]      = {0x27BDFF90u, 0xAFA40020u, 0x8FA40020u, 0xAFA50028u};
static const uint32_t G_ENEMY_LOOP[4]     = {0x3C048009u, 0x8C844798u, 0x3C028009u, 0x8C423DD0u};
static const uint32_t G_PLAYER_HIT[4]     = {0x27BDFFB0u, 0xAFA40018u, 0x00A02021u, 0x8FA50018u};
static const uint32_t G_MELEE_HIT[4]      = {0x27BDFF88u, 0xAFA40018u, 0x00A02021u, 0x8FA50018u};
static const uint32_t G_SHOT_FIND[4]      = {0x27BDFFD8u, 0xAFB20018u, 0x00809021u, 0xAFB3001Cu};
static const uint32_t G_DAMAGE[4]         = {0x00A04821u, 0x00091100u, 0x00491023u, 0x00021080u};
static const uint32_t G_BLAST_HIT[4]      = {0x27BDFFC0u, 0xAFBF003Cu, 0xAFBE0038u, 0xAFB70034u};
static const uint32_t G_HUD[4]            = {0x27BDFF80u, 0xAFBF007Cu, 0xAFB20078u, 0xAFB10074u};
static const uint32_t G_CONTINUE[4]       = {0x27BDFFB0u, 0xAFBF0048u, 0xAFB30044u, 0xAFB20040u};
static const uint32_t G_CAM_FOCUS[4]      = {0x3C02800Bu, 0x8442435Au, 0x3C03800Bu, 0x8463444Eu};
static const uint32_t G_PLAYER_OUT[4]     = {0x00041100u, 0x00441023u, 0x00021080u, 0x00441021u};
static const uint32_t G_PAST[4]           = {0x3C038009u, 0x8C6340B0u, 0x34020001u, 0x14620005u};
static const uint32_t G_MOVE_APPLY[4]     = {0x27BDFFC8u, 0xAFB20028u, 0x00809021u, 0x00121100u};
static const uint32_t G_SELECT[4]         = {0x8F8352F4u, 0x8F8252F0u, 0x27BDFF88u, 0xAFBF0074u};
static const uint32_t G_MAIN_MENU[4]      = {0x8F835374u, 0x27BDFF98u, 0xAFBF0060u, 0xAFB5005Cu};
static const uint32_t G_SLOT_FREE[4]      = {0x27BDFFF8u, 0x18C0000Au, 0x00001821u, 0x8CA20000u};
static const uint32_t G_LINEUP_DRAW[4]    = {0x27BDFFB8u, 0xAFBF0044u, 0xAFB40040u, 0xAFB3003Cu};
static const uint32_t G_NEW_GAME[4]       = {0x00002021u, 0x2407FFFFu, 0x3405017Cu, 0x34060180u};
static const uint32_t G_HISCORE[4]        = {0x27BDFFD0u, 0xAFBF0028u, 0xAFB30024u, 0xAFB20020u};
static const uint32_t G_HS_TABLE[4]       = {0x27BDFFC0u, 0xAFBF003Cu, 0xAFB20038u, 0xAFB10034u};
static const uint32_t G_MENU_INPUT[4]     = {0x27BDFFE8u, 0xAFBF0014u, 0xAFB00010u, 0x00048080u};
static const uint32_t G_CARD_SAVE[4]      = {0x27BDFEC0u, 0xAFBF013Cu, 0xAFBE0138u, 0xAFB70134u};
static const uint32_t G_CARD_LOADED[4]    = {0x3C04000Eu, 0x24842F40u, 0x3C02000Eu, 0x8C422F60u};
static const uint32_t G_FRONT_END[4]      = {0x27BDFFC8u, 0xAFB00020u, 0x3C10800Bu, 0x26104318u};
static const uint32_t G_CUTSCENE[4]       = {0x27BDFFD8u, 0xAFB00018u, 0x00808021u, 0x00101040u};

/* ---- mod memory layout ------------------------------------------------- */
#define P3_MAGIC 0x33504646u /* "FFP3" */
enum {
    M_MAGIC = 0x00,
    M_PAD_WORD = 0x04,   /* type << 16 | buttons (active-high), like 0x80093A10 */
    M_SEAT_CONN = 0x08,  /* seat connected (or test override) at last sample */
    M_SAMPLES = 0x0C,    /* number of samples taken */
    M_TEST_WORD = 0x10,  /* test override: bit 16 = on, low 16 = active-low word */
    M_ACTIVE = 0x14,     /* 1 while record 2 holds the third player */
    M_HIDDEN = 0x18,     /* bit 31 set: P3's type hidden for the clear test; low 16 = type */
    M_SPAWNS = 0x1C,     /* number of levels P3 was spawned in */
    M_PASSES = 0x20,     /* number of P3 update passes */
    M_P3_HITS = 0x24,    /* enemy hit tests that hit P3 */
    M_RETARGETS = 0x28,  /* weighted-choice targets moved to P3 */
    M_HIDE_DEPTH = 0x2C, /* nesting depth of p3_hide_push */
    M_HIDE_TYPE = 0x30,  /* P3's type while hidden */
    M_VIEW = 0x34,       /* 1 while P3 sits in slot 1 (p3_view_enter) */
    M_VIEW_T0 = 0x38,    /* types of records 0..2 after the view's move */
    M_VIEW_T1 = 0x3C,
    M_DESC = 0x40,       /* P3's model descriptor (DESC_SIZE bytes) */
    M_VIEW_T2 = 0x68,
    M_FF_HITS = 0x6C,    /* player hit tests in which P3 hit or was hit by P1/P2 */
    M_OBJ_FIX = 0x70,    /* objects thrown by P3 whose thrower was set 1 -> 2 */
    M_LIFE_AT = 0x74,    /* P3's next extra-life score (like DAT_800941b0[p]) */
    M_TALLY_INIT = 0x78, /* 1 once P3's level-end tally was set up */
    M_TALLY_FRAME = 0x7C,/* frame of P3's last tally step */
    M_P3_SLOTS = 0x80,   /* P3's values of P1_SLOTS (8 bytes per slot, 0x38) */
    M_OBJ_SNAP = 0x100,  /* object throwers before P3's pass (OBJ_MAX halfwords) */
    M_SUB = 0x200,       /* 1 while P3 stands in for downed P1 in slot 0 */
    M_CONTINUES = 0x204, /* times P3 continued */
    M_GAMEOVER_HELD = 0x208, /* game-over starts cancelled because a player stood */
    M_PROMPT = 0x210,    /* P3's continue prompt text (48 bytes) */
    M_TALLY_TEXT = 0x240,/* P3's level-end tally text (48 bytes) */
    M_FIGHTER = 0x270,   /* P3's fighter type (P3_CHOICE_NONE = twin of P2) */
    M_FIGHTER_LOADS = 0x274, /* fighter loads into enhancement memory */
    M_CHOICE = 0x278,    /* P3's choice from the select screen (0xFF none) */
    M_NAMES = 0x280,     /* "<NAME>.ACH\0" / "<NAME>.ADT\0" for the loaders (32 bytes) */
    M_SEL_ACTIVE = 0x2A0,    /* 1 while P3 has a cursor on the select screen */
    M_SEL_CURSOR = 0x2A4,    /* P3's slot 0..3 */
    M_SEL_CONFIRMED = 0x2A8,
    M_SEL_PREV = 0x2AC,      /* P3's buttons (active-high) last frame */
    M_SEL_TEXT = 0x2B0,      /* "AND <NAME>" (32 bytes) */
    M_LEASH_HOLDS = 0x2D0,   /* moves rejected by the 3-player leash */
    M_HS_STATE = 0x2E0,      /* P3's high-score entry: HS_IDLE / PENDING / ENTERING / DONE */
    M_HS_RANK = 0x2E4,       /* P3's row in the table (0..9) */
    M_HS_CUR = 0x2E8,        /* letter under the cursor (index into the charset) */
    M_HS_POS = 0x2EC,        /* letters entered (0..3) */
    M_HS_PREV = 0x2F0,       /* P3's buttons (active-high) last frame */
    M_HS_NAME = 0x2F4,       /* name being entered (8 bytes, like the game's buffer) */
    M_HS_LABEL = 0x300,      /* "CONGRATULATIONS P3!" (32 bytes) */
    M_HP_CARRY = 0x320,      /* P3's health for the next level (0 = take P2's, unless M_DOWN_CARRY) */
    M_GAME_P3 = 0x324,       /* 1 once P3 has played in this game */
    M_SAVE_PENDING = 0x328,  /* 1: a loaded save holds P3 (applied at P3's next spawn) */
    M_SAVE_TYPE = 0x32C,
    M_SAVE_HP = 0x330,
    M_SAVE_SCORE = 0x334,
    M_DOWN_CARRY = 0x338,    /* 1: P3 was down when the last level ended: the next one starts it down */
    M_GIFT = 0x340,          /* 1: the game gives this level's start weapons; P3's at the loop top */
    M_PAUSE_P3 = 0x344,      /* 1 while the pause menu is P3's (its reads of port 0 read seat 3) */
    M_PAUSE_TEXT_SAVE = 0x348, /* the game's "PAUSED 1P" pointer while P3's text replaces it */
    M_GIFTS = 0x34C,         /* weapons P3 got at a level start (for "show") */
    M_FE_AUTO = 0x350,       /* 1 from front-end entry until the main menu's first frame */
    M_DROPIN = 0x354,        /* 1: this game's players joined on the select screen (Quick Start) */
    M_P2_JOIN_PREV = 0x358,  /* P2's buttons (active-high) last select frame, drop-in */
    M_P3_JOIN_PREV = 0x35C,  /* P3's buttons last select frame, drop-in */
    M_P3_JOINED = 0x360,     /* drop-in: P3 confirmed a fighter (else P3 stays out) */
    M_HINT_TEXT = 0x370,     /* drop-in: "PLAYER n  PRESS X TO JOIN" (32 bytes) */
    M_PAUSE_TEXT = 0x390,    /* "PAUSED 3P", made from the game's "PAUSED 2P" (32 bytes) */
    M_PAUSES = 0x3B0,        /* times P3 paused the game (for "show") */
    M_TEX_LOADS = 0x3B4,     /* level texture loads that added P3's fighter slot */
    M_TEX_SKIPS = 0x3B8,     /* ... that kept the stock load (no room for P3's fighter) */
    M_TEX_ENTRIES = 0x3BC,   /* last level: texture entries with P3's fighter (estimate) */
    M_TEX_FREE = 0x3C0,      /* last level: VRAM map cells left with P3's fighter (estimate) */
    M_TEX_APPENDS = 0x3C4,   /* level texture loads that added P3's fighter in a second load (tex_append_p3) */
    M_TEX_ACC_TYPE = 0x3C8,  /* fighter type + 1 whose texture -> entry values wait in M_TEX_ACC for
                              * P3's model load (the index table had no room); 0 = none */
    M_TEX_ACC_COUNT = 0x3CC, /* ... how many */
    M_TEX_ROOM = 0x3D0,      /* last second load: free entries found for P3 (table end + unused ground) */
    M_TEX_NEED = 0x3D4,      /* ... entries P3's fighter needed */
    M_TEX_TILES = 0x3D8,     /* ... unused ground tiles it was given in VRAM (0: the stock map had room) */
    /* 0x3DC..0x3FF: free */
    M_P2_SLOTS = 0x400,  /* P2's values of P1_SLOTS during the pass (0x38) */
    M_TEX_ACC = 0x440,   /* s16[TEX_ACC_STASH]: see M_TEX_ACC_TYPE */
    M_SIZE = 0x840
};

static uint32_t rec(uint32_t i, uint32_t off) { return REC_BASE + i * REC_SIZE + off; }
static int16_t rec_type(uint32_t i) { return (int16_t)psx_mod_read_half(rec(i, RO_TYPE)); }
static int p3_present(void);
static void p3_hide_push(void);
static void p3_hide_pop(void);

static uint32_t g_mem; /* guest address of the block while this plugin is in the session's plan; else 0 */
static uint32_t g_mem_block; /* the block itself, allocated once per process */
static uint32_t g_fighter_wad;   /* enhancement memory for P3's fighter WAD */
static uint32_t g_fighter_model; /* ... and for its model */
static uint32_t g_ring;          /* one-entry WAD holding TARGETW.MOD for the select screen */
static uint8_t g_ring_src[256];  /* TARGETW.MOD as on the disc (224 bytes) */
static uint32_t g_ring_size;     /* 0 until read */
static uint8_t g_wad_buf[FIGHTER_WAD_MAX]; /* host bounce buffer for the disc read */

/* Debug command bridge (debug-server thread -> emulation thread). Test only:
 * a requested override is applied by the emulation-thread hook. */
static _Atomic uint32_t g_req_test = 0;     /* bit 31 = request pending */
static _Atomic uint32_t g_req_hp = 0;       /* bit 31 pending, bits 16..17 record, low 16 health */
static _Atomic uint32_t g_req_place = 0;    /* test only: bit 31 pending, low 2 bits record */
static _Atomic uint32_t g_req_place_xz = 0; /* ... x (s16) << 16 | z (u16) */
static _Atomic uint32_t g_req_hand = 0;     /* bit 31 pending, low 8: object in P3's hand (+0x7E) */
static _Atomic uint32_t g_req_life = 0;     /* bit 31 pending, low 31: P3's next extra-life score */
static _Atomic uint32_t g_req_score = 0;    /* bit 31 pending, low 31: P3's score */
static _Atomic uint32_t g_dbg_pad_word = 0; /* mirrors for "show" */
static _Atomic uint32_t g_dbg_samples = 0;
static _Atomic uint32_t g_dbg_conn = 0;
static _Atomic uint32_t g_dbg_active = 0;
static _Atomic uint32_t g_dbg_spawns = 0;
static _Atomic uint32_t g_dbg_passes = 0;
static _Atomic uint32_t g_dbg_p3_hits = 0;
static _Atomic uint32_t g_dbg_weapon_views = 0; /* weapon / special attack passes run for P3 */
static _Atomic uint32_t g_dbg_weapon_p3 = 0;    /* ... of which hit P3 */
static _Atomic uint32_t g_dbg_hs[4];            /* high-score entry: state, rank, cur, pos */
static _Atomic uint32_t g_dbg_retargets = 0;
static _Atomic uint32_t g_dbg_ff_hits = 0;
static _Atomic uint32_t g_dbg_obj_fix = 0;
static _Atomic uint32_t g_dbg_continues = 0;
static _Atomic uint32_t g_dbg_go_held = 0;
static _Atomic uint32_t g_dbg_fighter = 0;
static _Atomic uint32_t g_dbg_leash = 0;
static _Atomic uint32_t g_dbg_held[3];          /* spawns held for a free record: waves, pod, drop-off */

static void zero_guest(uint32_t addr, uint32_t size) {
    for (uint32_t o = 0; o < size; o += 4) psx_mod_write_word(addr + o, 0);
}

/* Runs at every session start: the first boot, and again for each rematch
 * from the launcher (start_mod_session). Mod guest memory is NOT reset
 * between sessions (PSX_MOD_SESSION_RESOURCES is off) and only grows, so
 * allocating here every time moved every address after a rematch: a PC that
 * rematched and one that was started fresh then wrote different pointers
 * (the WAD base, P3's model) into game RAM and the match drifted apart.
 * Allocate once per process (only this plugin allocates mod memory, so the
 * addresses are the same on every PC) and clear the contents each session,
 * so a rematch starts from the same bytes as a fresh start. */
static void p3_activate(void) {
    if (!g_mem_block) g_mem_block = psx_mod_alloc_guest_memory(M_SIZE, 16);
    if (!g_mem_block) {
        psx_mod_counter_add("ff.p3.alloc_failed", 1);
        return;
    }
    g_mem = g_mem_block;
    zero_guest(g_mem, M_SIZE);
    psx_mod_write_word(g_mem + M_MAGIC, P3_MAGIC);
    psx_mod_write_word(g_mem + M_CHOICE, 0xFFu);
    if (!g_fighter_wad) g_fighter_wad = psx_mod_alloc_guest_memory(FIGHTER_WAD_MAX, 16);
    else zero_guest(g_fighter_wad, FIGHTER_WAD_MAX);
    if (!g_fighter_model) g_fighter_model = psx_mod_alloc_guest_memory(FIGHTER_MODEL_MAX, 16);
    else zero_guest(g_fighter_model, FIGHTER_MODEL_MAX);
    if (!g_ring) g_ring = psx_mod_alloc_guest_memory(0x140, 16);
    else zero_guest(g_ring, 0x140);
    if (!g_fighter_wad || !g_fighter_model) psx_mod_counter_add("ff.p3.fighter_alloc_failed", 1);
    psx_mod_counter_add("ff.p3.activated", 1);
}

/* Before every session's plan, also one without this plugin: the hooks stay
 * registered, so they must go inert (g_mem 0, as on a fresh start) until
 * p3_activate runs for a plan that has the third player. Host state only. */
static void p3_session_reset(void) {
    g_mem = 0;
}

/* Seat-2 buttons (active-low) and whether a device is there. */
static int p3_read_seat(uint16_t* raw) {
    uint32_t test = psx_mod_read_word(g_mem + M_TEST_WORD);
    if (test & 0x10000u) {
        *raw = (uint16_t)(test & 0xFFFFu);
        return 1;
    }
#if defined(PSX_MAX_PLAYERS) && PSX_MAX_PLAYERS > P3_SEAT
    if (!sio_get_pad_connected(P3_SEAT)) {
        *raw = 0xFFFFu;
        return 0;
    }
    *raw = sio_get_pad_buttons_slot(P3_SEAT);
    return 1;
#else
    *raw = 0xFFFFu;
    return 0;
#endif
}

/* Wrap of FUN_8005f23c: the original fills the P1/P2 pad caches; then P3's
 * word is sampled from seat 2 at the same point of the frame. */
/* Apply a pending debug-command request (emulation thread). */
static int p3_in_view(void);
static void p3_format_score(void);
static void p3_apply_requests(void) {
    uint32_t req = atomic_exchange(&g_req_test, 0);
    if (req & 0x80000000u) psx_mod_write_word(g_mem + M_TEST_WORD, req & 0x1FFFFu);
    /* Test only: set a player's health, never inside a record move. */
    if (!p3_in_view() && !psx_mod_read_word(g_mem + M_SUB)) {
        uint32_t hand = atomic_exchange(&g_req_hand, 0);
        if (hand & 0x80000000u) psx_mod_write_byte(rec(P3_REC, 0x7Eu), (uint8_t)hand);
        uint32_t life = atomic_exchange(&g_req_life, 0);
        if (life & 0x80000000u) psx_mod_write_word(g_mem + M_LIFE_AT, life & 0x7FFFFFFFu);
        uint32_t sc = atomic_exchange(&g_req_score, 0);
        if (sc & 0x80000000u) {
            psx_mod_write_word(g_mem + M_P3_SLOTS + SLOT_SCORE, sc & 0x7FFFFFFFu);
            p3_format_score();
        }
        uint32_t h = atomic_exchange(&g_req_hp, 0);
        if (h & 0x80000000u) {
            uint32_t r = (h >> 16) & 3u;
            if (r <= 2) psx_mod_write_half(REC_BASE + r * REC_SIZE + RO_HEALTH, (uint16_t)(h & 0xFFFFu));
        }
        uint32_t pl = atomic_exchange(&g_req_place, 0);
        if (pl & 0x80000000u) {
            uint32_t r = pl & 3u, xz = atomic_load(&g_req_place_xz);
            if (r <= 2) {
                psx_mod_write_word(rec(r, RO_X), (uint32_t)((int32_t)(int16_t)(xz >> 16) * 65536));
                psx_mod_write_word(rec(r, RO_Z), (uint32_t)((int32_t)(int16_t)(xz & 0xFFFFu) * 65536));
            }
        }
    }
}

static void p3_loop_top(struct CPUState* cpu, uint32_t ra);
static void p3_pause_end(void);

static int wrap_pad_sample(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    func_override_call_original(cpu);
    if (!g_mem) return 1;
    p3_apply_requests();
    if (ra == RA_LOOP_TOP) p3_loop_top(cpu, ra);
    uint16_t raw;
    int conn = p3_read_seat(&raw);
    /* Same encoding as the game's cache: pad type 4 (digital) or 0xFF (none)
     * in bits 16..23, buttons active-high in the low half. */
    uint32_t word = conn ? ((4u << 16) | (uint16_t)~raw) : (0xFFu << 16);
    uint32_t n = psx_mod_read_word(g_mem + M_SAMPLES) + 1;
    psx_mod_write_word(g_mem + M_PAD_WORD, word);
    psx_mod_write_word(g_mem + M_SEAT_CONN, (uint32_t)conn);
    psx_mod_write_word(g_mem + M_SAMPLES, n);
    atomic_store(&g_dbg_pad_word, word);
    atomic_store(&g_dbg_conn, (uint32_t)conn);
    atomic_store(&g_dbg_samples, n);
    atomic_store(&g_dbg_active, (uint32_t)p3_present());
    atomic_store(&g_dbg_spawns, psx_mod_read_word(g_mem + M_SPAWNS));
    atomic_store(&g_dbg_passes, psx_mod_read_word(g_mem + M_PASSES));
    atomic_store(&g_dbg_p3_hits, psx_mod_read_word(g_mem + M_P3_HITS));
    atomic_store(&g_dbg_retargets, psx_mod_read_word(g_mem + M_RETARGETS));
    atomic_store(&g_dbg_ff_hits, psx_mod_read_word(g_mem + M_FF_HITS));
    atomic_store(&g_dbg_obj_fix, psx_mod_read_word(g_mem + M_OBJ_FIX));
    atomic_store(&g_dbg_continues, psx_mod_read_word(g_mem + M_CONTINUES));
    atomic_store(&g_dbg_go_held, psx_mod_read_word(g_mem + M_GAMEOVER_HELD));
    atomic_store(&g_dbg_leash, psx_mod_read_word(g_mem + M_LEASH_HOLDS));
    atomic_store(&g_dbg_fighter, psx_mod_read_word(g_mem + M_FIGHTER) |
                                 (psx_mod_read_word(g_mem + M_FIGHTER_LOADS) << 8));
    return 1;
}

/* ---- M2: spawn ----------------------------------------------------------
 * P3 starts as a twin of P2's fighter: only two characters' data fit in the
 * level heap (DAT_800947f0 is ~80 KB below the top of 2 MB RAM after setup),
 * so a third distinct fighter needs extra RAM (later milestone). */
static int p3_in_view(void);
static int p3_present(void) {
    if (!g_mem || !psx_mod_read_word(g_mem + M_ACTIVE)) return 0;
    if (p3_in_view()) return 0;
    return rec_type(P3_REC) != -1 || psx_mod_read_word(g_mem + M_HIDE_DEPTH) != 0;
}

/* P3's HUD score text, formatted like FUN_8005e9e8: six characters, the
 * score in tens with leading blanks and a final '0'. */
static void p3_format_score(void) {
    uint32_t v = psx_mod_read_word(g_mem + M_P3_SLOTS + SLOT_SCORE);
    static const uint32_t DIV[5] = {100000u, 10000u, 1000u, 100u, 10u};
    int seen = 0;
    for (uint32_t k = 0; k < 5; k++) {
        uint32_t d = (v / DIV[k]) % 10u;
        if (k == 0) d = v / DIV[0];
        if (d) seen = 1;
        psx_mod_write_byte(g_mem + M_P3_SLOTS + SLOT_SCORE_TEXT + k, (uint8_t)(seen ? '0' + d : ' '));
    }
    psx_mod_write_byte(g_mem + M_P3_SLOTS + SLOT_SCORE_TEXT + 5, '0');
    psx_mod_write_byte(g_mem + M_P3_SLOTS + SLOT_SCORE_TEXT + 6, 0);
}

static uint32_t guest_call(struct CPUState* cpu, uint32_t fn, uint32_t a0, uint32_t a1,
                           uint32_t a2, uint32_t ra);
static uint32_t tex_acc_swap_in(uint32_t type);
static void tex_acc_swap_out(uint32_t type, uint32_t n);

static void put_cstr(uint32_t dst, const char* str) {
    uint32_t k = 0;
    for (; str[k]; k++) psx_mod_write_byte(dst + k, (uint8_t)str[k]);
    psx_mod_write_byte(dst + k, 0);
}

/* Load fighter `type` for P3 the way its per-character init does, but with
 * the WAD and the model in enhancement memory instead of the level heap
 * (which has no room for a third fighter's ~250 KB of moves): the WAD comes
 * from the disc (psx_mod_read_disc_file: no guest CD traffic), the game's
 * own FUN_80060110 / FUN_8001721c then set up DAT_80081720[type] and the
 * move table DAT_800b3be8[type] with the "current WAD" and the model heap
 * pointed at enhancement memory; those globals are put back afterwards.
 * Every level: the model loader converts its source and the copy's texture
 * indices depend on the level. Returns 1 on success. */
static int p3_load_fighter(struct CPUState* cpu, uint32_t type, uint32_t ra) {
    if (type > 3 || !g_fighter_wad || !g_fighter_model) return 0;
    char path[32];
    snprintf(path, sizeof(path), "CHAR/%s.WAD", FIGHTER_NAME[type]);
    uint32_t size = 0;
    if (!psx_mod_read_disc_file(path, g_wad_buf, sizeof(g_wad_buf), &size) || size < 64) {
        psx_mod_counter_add("ff.p3.fighter_read_failed", 1);
        return 0;
    }
    uint32_t o = 0;
    for (; o + 4 <= size; o += 4) {
        uint32_t w = (uint32_t)g_wad_buf[o] | ((uint32_t)g_wad_buf[o + 1] << 8) |
                     ((uint32_t)g_wad_buf[o + 2] << 16) | ((uint32_t)g_wad_buf[o + 3] << 24);
        psx_mod_write_word(g_fighter_wad + o, w);
    }
    for (; o < size; o++) psx_mod_write_byte(g_fighter_wad + o, g_wad_buf[o]);
    char name[16];
    snprintf(name, sizeof(name), "%s.ACH", FIGHTER_NAME[type]);
    put_cstr(g_mem + M_NAMES, name);
    snprintf(name, sizeof(name), "%s.ADT", FIGHTER_NAME[type]);
    put_cstr(g_mem + M_NAMES + 16, name);
    uint32_t base = psx_mod_read_word(G_WAD_BASE), dir = psx_mod_read_word(G_WAD_DIR);
    uint32_t esz = psx_mod_read_word(G_WAD_ENTRY_SIZE), wsz = psx_mod_read_word(G_WAD_SIZE);
    uint32_t heap = psx_mod_read_word(G_MODEL_HEAP);
    uint16_t bar = psx_mod_read_half(G_LOAD_BAR);
    psx_mod_write_word(G_WAD_BASE, g_fighter_wad);
    psx_mod_write_word(G_WAD_DIR, g_fighter_wad);
    psx_mod_write_word(G_WAD_SIZE, size);
    psx_mod_write_word(G_MODEL_HEAP, g_fighter_model);
    psx_mod_write_half(G_LOAD_BAR, 0);
    const uint32_t acc_n = tex_acc_swap_in(type);
    guest_call(cpu, FN_LOAD_MODEL, g_mem + M_NAMES, type, FIGHTER_SLOT[type], ra);
    tex_acc_swap_out(type, acc_n);
    uint32_t used = psx_mod_read_word(G_MODEL_HEAP) - g_fighter_model;
    guest_call(cpu, FN_LOAD_MOVES, g_mem + M_NAMES + 16, type, 0, ra);
    psx_mod_write_word(G_WAD_BASE, base);
    psx_mod_write_word(G_WAD_DIR, dir);
    psx_mod_write_word(G_WAD_ENTRY_SIZE, esz);
    psx_mod_write_word(G_WAD_SIZE, wsz);
    psx_mod_write_word(G_MODEL_HEAP, heap);
    psx_mod_write_half(G_LOAD_BAR, bar);
    if (used > FIGHTER_MODEL_MAX) psx_mod_counter_add("ff.p3.fighter_model_overflow", 1);
    psx_mod_write_word(g_mem + M_FIGHTER_LOADS, psx_mod_read_word(g_mem + M_FIGHTER_LOADS) + 1);
    return 1;
}

/* P3's fighter: choice `c` if it is free, else the first fighter neither
 * P1 nor P2 plays. */
static uint32_t p3_pick_type(uint32_t c) {
    int16_t t0 = rec_type(0), t1 = rec_type(1);
    if (c <= 3 && (int16_t)c != t0 && (int16_t)c != t1) return c;
    for (uint32_t t = 0; t < 4; t++)
        if ((int16_t)t != t0 && (int16_t)t != t1) return t;
    return (uint32_t)(uint16_t)t1;
}

/* P3's fighter from its select-screen choice. */
static uint32_t p3_pick_fighter(void) { return p3_pick_type(psx_mod_read_word(g_mem + M_CHOICE)); }

/* ---- P3's fighter textures -------------------------------------------------
 * The level setup's texture loader FUN_80060394(0) (called from FUN_80014a34,
 * return 0x80014AEC) loads the fighter texture slots 0x24..0x28 (HAWK, MACE,
 * SMASHER, ALANA, HAWK2) only for the types of records 0 and 1; with a
 * nonzero argument (the fighter select) it loads every slot that has a name.
 * A skipped slot keeps its texture base DAT_800af418[slot] from the last
 * screen that loaded it. P3's model is set up later (wrap_spawn_pos ->
 * FUN_80060110), and the model loader converts its texture indices from that
 * base, so a P3 fighter that neither P1 nor P2 plays was drawn with whatever
 * the stale base points at in this level (Alana as a scrambled "robot").
 *
 * A third fighter does not fit in every level. The loader writes one 12-byte
 * entry per texture (8x8 flat ones excepted) to the table at 0x800A8768
 * (count DAT_80094838) with no bound; 0x800ADA40 (the sound library's data,
 * then the per-level function table 0x800ADBE0) follows after 1767 entries.
 * A fighter adds 117..137 entries, and with three fighters 6 of the 25 levels went past the end and
 * crashed through the overwritten level function pointer. VRAM is the other
 * limit: the allocator FUN_80066acc hangs when no rectangle fits (map
 * 0x800E1140, 0x40 x 0x100 bytes, 1 = used).
 *
 * So, when P3 will spawn with an unplayed fighter, the level setup's call
 * measures first and widens only when it fits. The loader starts from
 * scratch at every call (allocation map, counters), so it can run again:
 *   1. no fighter slot  -> entries / free cells of the level alone;
 *   2. P3's slot only   -> the difference is P3's entries and VRAM cells;
 *   3. the stock call   -> P1's and P2's load as the game does it;
 *   4. if the stock entries + P3's entries stay below the table's end, and
 *      the free cells leave P3's cells plus a margin: P1, P2 and P3.
 * Passes 1, 2 and 4 use the load-everything mode with the fighter slots
 * nobody wants taken out of the loader's two per-slot name tables for that
 * call (a slot without a name is skipped, as in the stock mode).
 * The loader's last step converts the level's 4096-entry table at
 * 0x8009F870 in place (v = (v + DAT_800af4b2) * 3 unless v == -4), so a
 * second run would convert it again and scramble the level's own textures:
 * every pass starts from the table as the level setup left it. Everything
 * else the loader writes is assigned, not accumulated (a whole-RAM compare
 * against a single widened load found no other difference). The argument
 * is the loader's only use of that mode (Ghidra: local_58 is read once, to
 * clear the per-slot skip). Passes 1 and 2 never load more than the stock
 * call. A level where P3's fighter does not fit keeps the stock load (P3
 * drawn as before) and counts in tex_skips. */
#define FN_TEX_LOAD       0x80060394u
#define RA_TEX_LOAD_LEVEL 0x80014AECu /* the level setup's call */
#define G_TEX_NAMES_A     0x800BA040u /* char*[0x4F]: per-slot file the loader's first block reads */
#define G_TEX_NAMES_B     0x800BBE38u /* char*[0x4F]: per-slot .PDT (textures) */
#define G_TEX_ENTRIES     0x80094838u /* entries the loader wrote to 0x800A8768 */
#define TEX_ENTRY_ROOM    1767u       /* (0x800ADA40 - 0x800A8768) / 12 */
#define TEX_ENTRY_SPARE   8u
#define G_VRAM_MAP        0x800E1140u
#define VRAM_MAP_SIZE     0x4000u
#define VRAM_SPARE_CELLS  256u        /* packing is first-fit: keep room beyond P3's cells */
#define G_TEX_LEVEL_MAP   0x8009F870u /* s16[0x1000]: converted in place by the loader's last step */
#define TEX_LEVEL_MAP_SIZE 0x2000u
#define FIGHTER_TEX_SLOT0 0x24u       /* slot 0x24 + fighter type (0..4, 4 = HAWK2) */
#define FIGHTER_TEX_SLOTS 5u
static const uint32_t G_TEX_LOAD[4] = {0x27BDFF90u, 0xAFBF006Cu, 0xAFBE0068u, 0xAFB70064u};

/* The fighter P3 will play in the level being set up, or -1 when P3 will not
 * spawn: wrap_spawn_pos's checks and pick, made before its record copy (a
 * loaded game's saved fighter included). */
static int p3_coming_type(void) {
    if (psx_mod_read_word(G_PLAYERS) != 2 || psx_mod_read_word(G_DEMO) != 0) return -1;
    p3_apply_requests();
    uint16_t raw;
    if (!p3_read_seat(&raw)) return -1;
    if (psx_mod_read_word(g_mem + M_DROPIN) && !psx_mod_read_word(g_mem + M_P3_JOINED)) return -1;
    if (rec_type(1) == -1) return -1;
    uint32_t c = psx_mod_read_word(g_mem + M_CHOICE);
    if (psx_mod_read_word(g_mem + M_SAVE_PENDING) && psx_mod_read_byte(G_LOADED_GAME))
        c = psx_mod_read_word(g_mem + M_SAVE_TYPE);
    return (int)p3_pick_type(c);
}

static uint32_t vram_free_cells(void) {
    uint32_t n = 0;
    for (uint32_t o = 0; o < VRAM_MAP_SIZE; o += 4) {
        uint32_t w = psx_mod_read_word(G_VRAM_MAP + o);
        n += ((w & 0xFFu) == 0) + ((w & 0xFF00u) == 0) + ((w & 0xFF0000u) == 0) + ((w >> 24) == 0);
    }
    return n;
}

/* One run of the original loader in load-everything mode with only the
 * fighter slots in `keep` (bit = type) named; the arguments as the caller
 * gave them, except the mode. */
/* The level map as the level setup left it, for the duration of one
 * wrap_tex_load call (host scratch; nothing spans frames). */
static uint32_t g_tex_level_map[TEX_LEVEL_MAP_SIZE / 4];

static void tex_map_save(void) {
    for (uint32_t o = 0; o < TEX_LEVEL_MAP_SIZE; o += 4) g_tex_level_map[o / 4] = psx_mod_read_word(G_TEX_LEVEL_MAP + o);
}

static void tex_map_restore(void) {
    for (uint32_t o = 0; o < TEX_LEVEL_MAP_SIZE; o += 4) psx_mod_write_word(G_TEX_LEVEL_MAP + o, g_tex_level_map[o / 4]);
}

static void tex_pass(struct CPUState* cpu, const uint32_t regs[32], uint32_t keep) {
    uint32_t names_a[FIGHTER_TEX_SLOTS], names_b[FIGHTER_TEX_SLOTS];
    for (uint32_t k = 0; k < FIGHTER_TEX_SLOTS; k++) {
        const uint32_t s = 4u * (FIGHTER_TEX_SLOT0 + k);
        names_a[k] = psx_mod_read_word(G_TEX_NAMES_A + s);
        names_b[k] = psx_mod_read_word(G_TEX_NAMES_B + s);
        if (!(keep & (1u << k))) {
            psx_mod_write_word(G_TEX_NAMES_A + s, 0);
            psx_mod_write_word(G_TEX_NAMES_B + s, 0);
        }
    }
    memcpy(cpu->gpr, regs, 32 * sizeof(uint32_t));
    cpu->gpr[4] = 1; /* load-everything mode: every slot that still has a name */
    func_override_call_original(cpu);
    for (uint32_t k = 0; k < FIGHTER_TEX_SLOTS; k++) {
        const uint32_t s = 4u * (FIGHTER_TEX_SLOT0 + k);
        psx_mod_write_word(G_TEX_NAMES_A + s, names_a[k]);
        psx_mod_write_word(G_TEX_NAMES_B + s, names_b[k]);
    }
}

/* ---- P3's fighter in a level without room for one widened load -----------
 * In 7 crowded levels P1 + P2 + P3 in one load go past the entry table's end
 * (levels 5, 7, 8, 9, 11, 18) or leave too few VRAM cells for the widened
 * path's margin (level 12). There, after the stock load, P3's slot is loaded
 * by a second run of the loader with every other slot's name taken out, and
 * its results are moved into room the stock load leaves unused:
 *
 * - Entries. A fighter is 117..137 entries (Mace, Hawk, Smasher; most of its
 *   ~460 textures are 8x8 flat ones, which take none). Every model reaches an entry through the
 *   texture -> entry table DAT_8009ACC8 when it is converted (FUN_80060110
 *   writes entry * 12 as u16 into the model), and P3's model is converted
 *   after the texture load (wrap_spawn_pos -> p3_load_fighter). So P3's
 *   entries can sit anywhere in the table, one by one:
 *     1. after the stock load's last entry: the 256 ground entries at
 *        DAT_800AF4B2 plus the two the loader writes after them (read as
 *        base + 0x100 / + 0x101 at 0x80026808) end the stock table;
 *     2. in ground entries the level map does not use. The map (0x8009F870,
 *        loaded before the texture load by the level's own init, written by
 *        nothing else) holds (v + base) * 3 per cell; only its cells read
 *        ground entries (FUN_8001100c, FUN_800112a4).
 *   Measured in the 7 levels (Hawk + Mace + Smasher, the largest three): 153
 *   to 345 free entries for P3's 137.
 * - Texture indices. If the stock count + P3's still fit in the 4000-entry
 *   table (0x8009CC08, the per-level function table, follows it), P3's
 *   values go after the stock ones and P3's slot base points there. If not,
 *   they wait in M_TEX_ACC and are put in for the one model conversion that
 *   reads them (tex_acc_swap_in / _out): no other code reads a fighter slot's
 *   values in a level (Ghidra: the model loaders, and the front end's slide
 *   show for slots 1..5).
 * - VRAM. The loader clears the allocation map first (FUN_80066EF4); during
 *   the second run that clear is followed by the stock load's map
 *   (wrap_vram_reset), so P3's textures and palettes go into cells the stock
 *   load left free. Only the loader allocates VRAM (Ghidra: FUN_80066ACC and
 *   FUN_80066D14 have no other caller), and its allocator hangs when nothing
 *   fits, so P3's whole slot is first run through a copy of the allocator
 *   (tex_sim_*) on a copy of the map; the second run only starts when every
 *   texture fits. The copy must give the guest's own result: a difference
 *   counts in ff.p3.tex_sim_mismatch. When the stock map has no room (levels
 *   7 and 12 leave only 5 rows), the ground tiles the level map never uses
 *   are offered too (tex_free_ground_tiles) and the check runs again.
 * - Everything else the second run writes is put back as the stock load left
 *   it: the entry table, the index table, the slot bases, the counters, the
 *   level map (converted once more by the run), the palette copies at
 *   0x800B4AB8 (fighter textures are 4-bit with their own CLUTs; a P3 entry
 *   of another depth stops the second load).
 * If anything does not fit, the stock load stays and tex_skips counts it. */
#define G_TEX_TABLE      0x800A8768u /* 12-byte entries */
#define G_TEX_ACC        0x8009ACC8u /* s16[4000]: texture -> entry index */
#define TEX_ACC_ROOM     4000u
#define TEX_ACC_STASH    512u        /* M_TEX_ACC: (M_SIZE - M_TEX_ACC) / 2 */
#define G_TEX_COUNT      0x8009440Cu /* textures the loader counted */
#define G_TEX_FLAT       0x80093A58u /* ... 8x8 flat ones */
#define G_TEX_BASES      0x800AF418u /* s16[0x4F]: first texture of each slot */
#define TEX_SLOTS        0x4Fu
#define G_GROUND_BASE    0x800AF4B2u /* s16: first ground entry */
#define GROUND_ENTRIES   256u
#define GROUND_EXTRA     2u          /* written after them, not counted */
#define G_VRAM_ROWS      0x80094208u /* s16[0x40]: used cells per map row */
#define VRAM_ROWS_SIZE   0x80u
#define VRAM_ROW_FULL    0x100
#define G_CLUT_STAGE     0x800B4AB8u /* 16 rows of 0x200: palettes for VRAM y 0x1F0.. */
#define CLUT_STAGE_SIZE  0x2000u
#define G_TEX_CURSOR     0x800947CCu /* s16 x, s16 y: the loader's last rectangle */
#define G_WAD_FILE_SIZE  0x80094570u /* size of the last file FUN_800611F8 found */
#define G_WAD_DIRECTORY  0x80094038u /* -> big-endian count, then 20-byte entries */
#define G_WAD_DATA       0x80094818u /* file offsets are relative to this */
#define FN_VRAM_RESET    0x80066EF4u
static const uint32_t G_VRAM_RESET[4] = {0x27BDFFD8u, 0xAFBF0024u, 0xAFB40020u, 0xAFB3001Cu};

/* The stock load's state, for the duration of one tex_append_p3 call. */
static uint32_t g_tx_table[TEX_ENTRY_ROOM * 3];
static uint16_t g_tx_acc[TEX_ACC_ROOM];
static uint16_t g_tx_bases[TEX_SLOTS];
static uint32_t g_tx_names[2][TEX_SLOTS];
static uint32_t g_tx_stage[CLUT_STAGE_SIZE / 4];
static uint8_t g_tx_vram[VRAM_MAP_SIZE];         /* the map the second run starts from */
static int16_t g_tx_rows[VRAM_ROWS_SIZE / 2];
static uint8_t g_tx_vram_stock[VRAM_MAP_SIZE];   /* ... and the stock load's, unchanged */
static int16_t g_tx_rows_stock[VRAM_ROWS_SIZE / 2];
static uint8_t g_sim_vram[VRAM_MAP_SIZE];
static int16_t g_sim_rows[VRAM_ROWS_SIZE / 2];
static uint16_t g_tx_hole[GROUND_ENTRIES + TEX_ENTRY_ROOM];
static uint32_t g_tx_p3_entry[TEX_ENTRY_ROOM * 3]; /* the second run's entries */
static uint16_t g_tx_p3_acc[TEX_ACC_STASH];        /* ... and texture -> entry values */
static int g_tex_keep_vram; /* wrap_vram_reset: put the stock map back after the clear */

static void tex_vram_write(const uint8_t* map, const int16_t* rows) {
    for (uint32_t o = 0; o < VRAM_MAP_SIZE; o += 4)
        psx_mod_write_word(G_VRAM_MAP + o, (uint32_t)map[o] | (uint32_t)map[o + 1] << 8 |
                                               (uint32_t)map[o + 2] << 16 | (uint32_t)map[o + 3] << 24);
    for (uint32_t r = 0; r < VRAM_ROWS_SIZE / 2; r++) psx_mod_write_half(G_VRAM_ROWS + 2 * r, (uint16_t)rows[r]);
}

/* The level's 64 ground tiles (32 x 32, 8-bit) fill VRAM x 768..895,
 * y 0..255 (FUN_80063900 loads them once per level, rect 0x80093A78; the
 * allocator map keeps cells 0xC0..0xDF of rows 0..0x1F reserved for them).
 * Tile t is at x 768 + 16 * (t & 7), y 32 * (t >> 3): map cells
 * 0xC0 + 4 * (t & 7) .. + 3, rows 4 * (t >> 3) .. + 3. Only the ground
 * entries read them (tpage 0x300, the loader's last loop; the game makes
 * that page nowhere else), entry base + 4t .. 4t + 3. A tile none of whose
 * four entries a map cell uses is never drawn in this level, so its cells
 * can take P3's textures. Frees them in the second run's map; returns how
 * many tiles. */
static uint32_t tex_free_ground_tiles(const uint8_t used[GROUND_ENTRIES]) {
    uint32_t tiles = 0;
    for (uint32_t t = 0; t < GROUND_ENTRIES / 4; t++) {
        if (used[4 * t] || used[4 * t + 1] || used[4 * t + 2] || used[4 * t + 3]) continue;
        const uint32_t x = 0xC0 + 4 * (t & 7), y = 4 * (t >> 3);
        int reserved = 1;
        for (uint32_t r = y; r < y + 4; r++)
            for (uint32_t c = x; c < x + 4; c++)
                if (!g_tx_vram[r * 0x100 + c]) reserved = 0;
        if (!reserved) continue;
        for (uint32_t r = y; r < y + 4; r++) {
            memset(&g_tx_vram[r * 0x100 + x], 0, 4);
            g_tx_rows[r] = (int16_t)(g_tx_rows[r] - 4);
        }
        tiles++;
    }
    return tiles;
}

static uint32_t bswap32(uint32_t v) {
    return (v >> 24) | (v << 24) | ((v >> 8) & 0xFF00u) | ((v & 0xFF00u) << 8);
}

/* The stage WAD's file `name_ptr` (a guest string): FUN_800611F8's lookup
 * without its loading-bar frames. */
static int wad_find(uint32_t name_ptr, uint32_t* data, uint32_t* size) {
    if (!name_ptr) return 0;
    uint8_t want[16] = {0};
    for (uint32_t k = 0; k < 15; k++) {
        want[k] = psx_mod_read_byte(name_ptr + k);
        if (!want[k]) break;
    }
    const uint32_t dir = psx_mod_read_word(G_WAD_DIRECTORY);
    const uint32_t count = bswap32(psx_mod_read_word(dir));
    for (uint32_t e = 0, p = dir + 4; e < count && e < 4096u; e++, p += 20) {
        uint32_t k = 0;
        while (k < 12 && psx_mod_read_byte(p + k) == want[k]) k++;
        if (k == 12) {
            *size = bswap32(psx_mod_read_word(p + 12));
            *data = psx_mod_read_word(G_WAD_DATA) + bswap32(psx_mod_read_word(p + 16));
            return 1;
        }
    }
    return 0;
}

static int tex_sim_fits(int x, int row, int cw, int ch) {
    for (int r = row; r < row + ch; r++) {
        if (g_sim_rows[r] == VRAM_ROW_FULL) return 0;
        for (int c = x; c < x + cw; c++)
            if (g_sim_vram[r * 0x100 + c]) return 0;
    }
    return 1;
}

/* FUN_80066ACC on the copy: first fit, by 32-row band, row, texture page. */
static int tex_sim_rect(int w, int h, int depth) {
    const int tw = 0x10 << (depth & 0x1F);
    if (depth == 0) w >>= 1;
    if (depth == 2) w <<= 1;
    int cw = (w + 7) >> 3, ch = (h + 7) >> 3;
    if (cw > 0x20) cw = 0x20;
    if (ch > 0x20) ch = 0x20;
    for (int band = 0; band < 0x40; band += 0x20)
        for (int row = band; row < band + 0x21 - ch; row++) {
            if (g_sim_rows[row] == VRAM_ROW_FULL) continue;
            for (int x0 = 0; x0 < 0x100; x0 += tw)
                for (int x = x0; x < x0 + tw - cw + 1; x++)
                    if (tex_sim_fits(x, row, cw, ch)) {
                        for (int r = row; r < row + ch; r++) {
                            memset(&g_sim_vram[r * 0x100 + x], 1, (size_t)cw);
                            g_sim_rows[r] = (int16_t)(g_sim_rows[r] + cw);
                        }
                        return 1;
                    }
        }
    return 0;
}

/* FUN_80066D14 on the copy: 4 cells (8 palettes) in rows 0x20..0x3F. */
static int tex_sim_clut(void) {
    for (int row = 0x20; row < 0x40; row++) {
        if (g_sim_rows[row] == VRAM_ROW_FULL) continue;
        for (int x = 0; x < 0x100; x += 4) {
            uint8_t* c = &g_sim_vram[row * 0x100 + x];
            if (!c[0] && !c[1] && !c[2] && !c[3]) {
                memset(c, 1, 4);
                g_sim_rows[row] = (int16_t)(g_sim_rows[row] + 4);
                return 1;
            }
        }
    }
    return 0;
}

/* Walk slot `slot`'s .PDT as the loader does (one call starts with no
 * palette block) and allocate on the copy. Returns 1 when every texture
 * fits; *entries / *textures as the loader will count them. */
/* Debug only (host side, for "show"): the first texture the copy could not
 * place in the last check: texture number << 16 | w / 8 << 8 | h / 8 << 2 |
 * depth, bit 31 for a palette block. */
static _Atomic uint32_t g_dbg_tex_fail = 0;

static int tex_sim_slot(uint32_t slot, uint32_t* entries, uint32_t* textures) {
    uint32_t data = 0, size = 0;
    atomic_store(&g_dbg_tex_fail, 0);
    if (!wad_find(psx_mod_read_word(G_TEX_NAMES_B + 4u * slot), &data, &size)) return 0;
    uint32_t p = data, n = 0, t = 0;
    int clut_left = 0;
    while (p < data + size) {
        const int w = (int16_t)psx_mod_read_half(p), h = (int16_t)psx_mod_read_half(p + 2);
        const int depth = (int8_t)psx_mod_read_byte(p + 5);
        if (w <= 0 || h <= 0 || w > 0x101 || h > 0x101 || depth < 0 || depth > 2) return 0;
        const uint32_t fail = t << 16 | (uint32_t)(w >> 3 & 0x3F) << 8 | (uint32_t)(h >> 3 & 0x3F) << 2 | (uint32_t)depth;
        int bytes = depth == 0 ? ((w * h) >> 1) + 0x20 : depth == 1 ? w * h : w * h * 2;
        uint32_t rounded = (uint32_t)(bytes + 0x1F) & ~0x1Fu, next = p + 0x20;
        if (!(w == 8 && h == 8)) {
            if (depth == 0) {
                if (clut_left == 0) {
                    if (!tex_sim_clut()) {
                        atomic_store(&g_dbg_tex_fail, fail | 0x80000000u);
                        return 0;
                    }
                    clut_left = 8;
                }
                clut_left--;
                next = p + 0x40;
                rounded -= 0x20;
            }
            if (!tex_sim_rect(w, h, depth)) {
                atomic_store(&g_dbg_tex_fail, fail);
                return 0;
            }
            next += rounded;
            n++;
        }
        t++;
        p = next;
    }
    *entries = n;
    *textures = t;
    return 1;
}

/* Wrap of FUN_80066EF4 (the loader's first step: clear the VRAM allocation
 * map, mark the frame buffers). During tex_append_p3's run the stock load's
 * map follows the clear. */
static int wrap_vram_reset(struct CPUState* cpu) {
    func_override_call_original(cpu);
    if (g_tex_keep_vram) tex_vram_write(g_tx_vram, g_tx_rows);
    return 1;
}

static void tex_put_back(int with_vram, uint32_t e_stock, uint32_t t_stock, uint32_t flat, int16_t ground,
                         uint32_t cursor, uint32_t file_size) {
    for (uint32_t k = 0; k < TEX_ENTRY_ROOM * 3; k++) psx_mod_write_word(G_TEX_TABLE + 4 * k, g_tx_table[k]);
    for (uint32_t k = 0; k < TEX_ACC_ROOM; k++) psx_mod_write_half(G_TEX_ACC + 2 * k, g_tx_acc[k]);
    for (uint32_t k = 0; k < TEX_SLOTS; k++) psx_mod_write_half(G_TEX_BASES + 2 * k, g_tx_bases[k]);
    for (uint32_t k = 0; k < CLUT_STAGE_SIZE / 4; k++) psx_mod_write_word(G_CLUT_STAGE + 4 * k, g_tx_stage[k]);
    tex_map_restore();
    psx_mod_write_word(G_TEX_ENTRIES, e_stock);
    psx_mod_write_word(G_TEX_COUNT, t_stock);
    psx_mod_write_word(G_TEX_FLAT, flat);
    psx_mod_write_half(G_GROUND_BASE, (uint16_t)ground);
    psx_mod_write_word(G_TEX_CURSOR, cursor);
    psx_mod_write_word(G_WAD_FILE_SIZE, file_size);
    if (with_vram) tex_vram_write(g_tx_vram_stock, g_tx_rows_stock);
}

/* Right after the stock load (P1's and P2's fighters): load P3's fighter
 * `want` in a second run, see the block comment above. Returns 1 when P3's
 * slot is in place. */
static int tex_append_p3(struct CPUState* cpu, const uint32_t regs[32], uint32_t want) {
    const uint32_t slot = FIGHTER_TEX_SLOT0 + want;
    const uint32_t e_stock = psx_mod_read_word(G_TEX_ENTRIES), t_stock = psx_mod_read_word(G_TEX_COUNT);
    const uint32_t flat = psx_mod_read_word(G_TEX_FLAT), cursor = psx_mod_read_word(G_TEX_CURSOR);
    const uint32_t file_size = psx_mod_read_word(G_WAD_FILE_SIZE);
    const int16_t ground = (int16_t)psx_mod_read_half(G_GROUND_BASE);
    if (ground < 0 || e_stock != (uint32_t)ground + GROUND_ENTRIES ||
        e_stock + GROUND_EXTRA > TEX_ENTRY_ROOM || t_stock > TEX_ACC_ROOM) {
        psx_mod_counter_add("ff.p3.tex_append_odd_stock", 1);
        return 0;
    }
    /* Free entries: after the stock table, then ground entries no map cell uses. */
    uint8_t used[GROUND_ENTRIES] = {0};
    tex_map_save(); /* the stock load's converted map */
    for (uint32_t k = 0; k < TEX_LEVEL_MAP_SIZE / 2; k++) {
        const int16_t v = (int16_t)(k & 1 ? g_tex_level_map[k / 2] >> 16 : g_tex_level_map[k / 2] & 0xFFFFu);
        if (v == -4) continue;
        const int32_t i = v / 3 - ground;
        if (v < 0 || v % 3 || i < 0 || i >= (int32_t)GROUND_ENTRIES) {
            psx_mod_counter_add("ff.p3.tex_append_odd_map", 1);
            return 0;
        }
        used[i] = 1;
    }
    uint32_t room = 0;
    for (uint32_t e = e_stock + GROUND_EXTRA; e < TEX_ENTRY_ROOM; e++) g_tx_hole[room++] = (uint16_t)e;
    for (uint32_t i = 0; i < GROUND_ENTRIES; i++)
        if (!used[i]) g_tx_hole[room++] = (uint16_t)(ground + (int32_t)i);
    /* P3's slot through a copy of the allocator, on the stock load's map. */
    for (uint32_t o = 0; o < VRAM_MAP_SIZE; o += 4) {
        const uint32_t w = psx_mod_read_word(G_VRAM_MAP + o);
        for (uint32_t b = 0; b < 4; b++) g_tx_vram[o + b] = (uint8_t)(w >> (8 * b));
    }
    for (uint32_t r = 0; r < VRAM_ROWS_SIZE / 2; r++) g_tx_rows[r] = (int16_t)psx_mod_read_half(G_VRAM_ROWS + 2 * r);
    memcpy(g_tx_vram_stock, g_tx_vram, sizeof(g_tx_vram_stock));
    memcpy(g_tx_rows_stock, g_tx_rows, sizeof(g_tx_rows_stock));
    memcpy(g_sim_vram, g_tx_vram, sizeof(g_sim_vram));
    memcpy(g_sim_rows, g_tx_rows, sizeof(g_sim_rows));
    uint32_t need = 0, tex = 0, tiles = 0;
    int fits = tex_sim_slot(slot, &need, &tex);
    if (!fits) {
        /* No room on the stock map: also offer the ground tiles no map cell
         * uses, then check again on that map. */
        tiles = tex_free_ground_tiles(used);
        if (tiles) {
            memcpy(g_sim_vram, g_tx_vram, sizeof(g_sim_vram));
            memcpy(g_sim_rows, g_tx_rows, sizeof(g_sim_rows));
            fits = tex_sim_slot(slot, &need, &tex);
        }
    }
    psx_mod_write_word(g_mem + M_TEX_ROOM, room);
    psx_mod_write_word(g_mem + M_TEX_NEED, need);
    psx_mod_write_word(g_mem + M_TEX_TILES, fits ? tiles : 0u);
    const int acc_after = t_stock + tex <= TEX_ACC_ROOM;
    if (!fits || need == 0 || need > room || need > TEX_ENTRY_ROOM || tex > TEX_ACC_STASH) {
        psx_mod_counter_add(fits ? "ff.p3.tex_append_no_entries" : "ff.p3.tex_append_no_vram", 1);
        return 0;
    }
    /* The stock state, then the second run with P3's slot alone. */
    for (uint32_t k = 0; k < TEX_ENTRY_ROOM * 3; k++) g_tx_table[k] = psx_mod_read_word(G_TEX_TABLE + 4 * k);
    for (uint32_t k = 0; k < TEX_ACC_ROOM; k++) g_tx_acc[k] = psx_mod_read_half(G_TEX_ACC + 2 * k);
    for (uint32_t k = 0; k < TEX_SLOTS; k++) g_tx_bases[k] = psx_mod_read_half(G_TEX_BASES + 2 * k);
    for (uint32_t k = 0; k < CLUT_STAGE_SIZE / 4; k++) g_tx_stage[k] = psx_mod_read_word(G_CLUT_STAGE + 4 * k);
    for (uint32_t k = 0; k < TEX_SLOTS; k++) {
        g_tx_names[0][k] = psx_mod_read_word(G_TEX_NAMES_A + 4 * k);
        g_tx_names[1][k] = psx_mod_read_word(G_TEX_NAMES_B + 4 * k);
        if (k != slot) {
            psx_mod_write_word(G_TEX_NAMES_A + 4 * k, 0);
            psx_mod_write_word(G_TEX_NAMES_B + 4 * k, 0);
        }
    }
    memcpy(cpu->gpr, regs, 32 * sizeof(uint32_t));
    cpu->gpr[4] = 1; /* load-everything mode: the one slot that has a name */
    g_tex_keep_vram = 1;
    func_override_call_original(cpu);
    g_tex_keep_vram = 0;
    for (uint32_t k = 0; k < TEX_SLOTS; k++) {
        psx_mod_write_word(G_TEX_NAMES_A + 4 * k, g_tx_names[0][k]);
        psx_mod_write_word(G_TEX_NAMES_B + 4 * k, g_tx_names[1][k]);
    }
    /* What the run made: entries 0..k-1 (then its own ground block), textures
     * 0..t-1 of P3's slot, P3's textures and palettes in VRAM. */
    const uint32_t k_run = (uint32_t)(int16_t)psx_mod_read_half(G_GROUND_BASE);
    const uint32_t t_run = psx_mod_read_word(G_TEX_COUNT), flat_run = psx_mod_read_word(G_TEX_FLAT);
    int ok = k_run == need && t_run == tex && (int16_t)psx_mod_read_half(G_TEX_BASES + 2 * slot) == 0;
    uint32_t* const p3_entry = g_tx_p3_entry;
    uint16_t* const p3_acc = g_tx_p3_acc;
    for (uint32_t j = 0; ok && j < need; j++) {
        for (uint32_t w = 0; w < 3; w++) p3_entry[3 * j + w] = psx_mod_read_word(G_TEX_TABLE + 12 * j + 4 * w);
        const uint32_t tpage = p3_entry[3 * j + 1] >> 16;
        if ((tpage >> 7) & 3) ok = 0; /* not 4-bit: its palette would be a copy at 0x800B4AB8 */
    }
    for (uint32_t i = 0; ok && i < tex; i++) {
        p3_acc[i] = psx_mod_read_half(G_TEX_ACC + 2 * i);
        if (p3_acc[i] > need) ok = 0;
    }
    int sim_same = 1;
    for (uint32_t o = 0; ok && o < VRAM_MAP_SIZE && sim_same; o += 4) {
        const uint32_t w = psx_mod_read_word(G_VRAM_MAP + o);
        for (uint32_t b = 0; b < 4; b++)
            if (g_sim_vram[o + b] != (uint8_t)(w >> (8 * b))) sim_same = 0;
    }
    for (uint32_t r = 0; ok && r < VRAM_ROWS_SIZE / 2; r++)
        if ((int16_t)psx_mod_read_half(G_VRAM_ROWS + 2 * r) != g_sim_rows[r]) sim_same = 0;
    if (ok && !sim_same) psx_mod_counter_add("ff.p3.tex_sim_mismatch", 1);
    /* Put the stock load's state back; on success keep the run's VRAM map
     * (stock + P3), else the stock one. */
    tex_put_back(!ok, e_stock, t_stock, flat + (ok ? flat_run : 0u), ground, cursor, file_size);
    if (!ok) {
        psx_mod_counter_add("ff.p3.tex_append_run_differs", 1);
        return 0;
    }
    for (uint32_t j = 0; j < need; j++)
        for (uint32_t w = 0; w < 3; w++) psx_mod_write_word(G_TEX_TABLE + 12u * g_tx_hole[j] + 4 * w, p3_entry[3 * j + w]);
    /* A flat texture points at the entry of the next one (or, last in the
     * slot, the run's first ground entry): the same after the move. */
    for (uint32_t i = 0; i < tex; i++) p3_acc[i] = p3_acc[i] < need ? g_tx_hole[p3_acc[i]] : (uint16_t)ground;
    if (acc_after) {
        for (uint32_t i = 0; i < tex; i++) psx_mod_write_half(G_TEX_ACC + 2 * (t_stock + i), p3_acc[i]);
        psx_mod_write_half(G_TEX_BASES + 2 * slot, (uint16_t)t_stock);
        psx_mod_write_word(G_TEX_COUNT, t_stock + tex);
    } else {
        for (uint32_t i = 0; i < tex; i++) psx_mod_write_half(g_mem + M_TEX_ACC + 2 * i, p3_acc[i]);
        psx_mod_write_word(g_mem + M_TEX_ACC_COUNT, tex);
        psx_mod_write_word(g_mem + M_TEX_ACC_TYPE, want + 1);
        psx_mod_write_half(G_TEX_BASES + 2 * slot, 0);
        psx_mod_counter_add("ff.p3.tex_acc_stashed", 1);
    }
    return 1;
}

/* Around P3's model conversion: when P3's texture -> entry values wait in
 * M_TEX_ACC, put them at the start of the index table with P3's slot base at
 * 0 for that one call, then the level's values back. Returns how many. */
static uint16_t g_acc_keep[TEX_ACC_STASH];

static uint32_t tex_acc_swap_in(uint32_t type) {
    if (!g_mem || psx_mod_read_word(g_mem + M_TEX_ACC_TYPE) != type + 1) return 0;
    const uint32_t n = psx_mod_read_word(g_mem + M_TEX_ACC_COUNT);
    if (n == 0 || n > TEX_ACC_STASH) return 0;
    for (uint32_t i = 0; i < n; i++) {
        g_acc_keep[i] = psx_mod_read_half(G_TEX_ACC + 2 * i);
        psx_mod_write_half(G_TEX_ACC + 2 * i, psx_mod_read_half(g_mem + M_TEX_ACC + 2 * i));
    }
    psx_mod_write_half(G_TEX_BASES + 2 * (FIGHTER_TEX_SLOT0 + type), 0);
    return n;
}

static void tex_acc_swap_out(uint32_t type, uint32_t n) {
    (void)type;
    for (uint32_t i = 0; i < n; i++) psx_mod_write_half(G_TEX_ACC + 2 * i, g_acc_keep[i]);
}

/* DEBUG ONLY (host-side, never set in play or netplay): "ff.p3 texmode N"
 * 0 = measure first (the shipped path), 1 = one load with P3's slot added,
 * 2 = the stock load only. Used to compare guest state between the paths. */
static _Atomic int g_dbg_tex_mode = 0;

static int wrap_tex_load(struct CPUState* cpu) {
    const int level = g_mem && cpu->gpr[31] == RA_TEX_LOAD_LEVEL && cpu->gpr[4] == 0;
    if (level) psx_mod_write_word(g_mem + M_TEX_ACC_TYPE, 0); /* nothing waits from an earlier level */
    const int want = level ? p3_coming_type() : -1;
    const int t0 = rec_type(0), t1 = rec_type(1);
    if (want < 0 || want == t0 || want == t1 || want >= (int)FIGHTER_TEX_SLOTS) {
        func_override_call_original(cpu);
        return 1;
    }
    uint32_t regs[32];
    memcpy(regs, cpu->gpr, sizeof(regs));
    const int dbg = atomic_load(&g_dbg_tex_mode);
    if (dbg == 2) { func_override_call_original(cpu); return 1; }
    if (dbg == 1) {
        tex_pass(cpu, regs, (1u << want) | (t0 >= 0 && t0 < 5 ? 1u << t0 : 0u) | (t1 >= 0 && t1 < 5 ? 1u << t1 : 0u));
        return 1;
    }
    tex_map_save();
    tex_pass(cpu, regs, 0u);
    const uint32_t e_none = psx_mod_read_word(G_TEX_ENTRIES), f_none = vram_free_cells();
    tex_map_restore();
    tex_pass(cpu, regs, 1u << want);
    const uint32_t e_p3 = psx_mod_read_word(G_TEX_ENTRIES), f_p3 = vram_free_cells();
    tex_map_restore();
    memcpy(cpu->gpr, regs, sizeof(regs));
    func_override_call_original(cpu); /* the stock load: P1's and P2's fighters */
    const uint32_t e_stock = psx_mod_read_word(G_TEX_ENTRIES), f_stock = vram_free_cells();
    const uint32_t p3_entries = e_p3 > e_none ? e_p3 - e_none : 0u;
    const uint32_t p3_cells = f_none > f_p3 ? f_none - f_p3 : 0u;
    psx_mod_write_word(g_mem + M_TEX_ENTRIES, e_stock + p3_entries);
    psx_mod_write_word(g_mem + M_TEX_FREE, f_stock > p3_cells ? f_stock - p3_cells : 0u);
    if (e_stock + p3_entries + TEX_ENTRY_SPARE > TEX_ENTRY_ROOM || f_stock < p3_cells + VRAM_SPARE_CELLS) {
        /* No room for one widened load: P3's slot in a second load, into the
         * room the stock load leaves (tex_append_p3). */
        if (tex_append_p3(cpu, regs, (uint32_t)want)) {
            psx_mod_write_word(g_mem + M_TEX_APPENDS, psx_mod_read_word(g_mem + M_TEX_APPENDS) + 1);
            psx_mod_counter_add("ff.p3.tex_append", 1);
            return 1;
        }
        psx_mod_write_word(g_mem + M_TEX_SKIPS, psx_mod_read_word(g_mem + M_TEX_SKIPS) + 1);
        psx_mod_counter_add("ff.p3.tex_no_room", 1);
        return 1; /* the stock load stays */
    }
    uint32_t keep = (1u << want);
    if (t0 >= 0 && t0 < (int)FIGHTER_TEX_SLOTS) keep |= 1u << t0;
    if (t1 >= 0 && t1 < (int)FIGHTER_TEX_SLOTS) keep |= 1u << t1;
    tex_map_restore();
    tex_pass(cpu, regs, keep);
    psx_mod_write_word(g_mem + M_TEX_LOADS, psx_mod_read_word(g_mem + M_TEX_LOADS) + 1);
    psx_mod_counter_add("ff.p3.tex_slot", 1);
    return 1;
}

/* Wrap of FUN_80014808 (last step of level setup: player start positions).
 * After it, P2's record is complete for this level; record 2 is still free
 * (FUN_80013848 set its type to -1) and the wave script has not run yet. */
static int wrap_spawn_pos(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    func_override_call_original(cpu);
    if (!g_mem) return 1;
    psx_mod_write_word(g_mem + M_ACTIVE, 0);
    psx_mod_write_word(g_mem + M_HIDDEN, 0);
    psx_mod_write_word(g_mem + M_HIDE_DEPTH, 0);
    psx_mod_write_word(g_mem + M_VIEW, 0);
    psx_mod_write_word(g_mem + M_SUB, 0);
    psx_mod_write_word(g_mem + M_TALLY_INIT, 0);
    psx_mod_write_word(g_mem + M_GIFT, 0);
    p3_pause_end();
    psx_mod_write_word(g_mem + M_SEL_ACTIVE, 0); /* the select is over (its choice stays) */
    if (psx_mod_read_word(G_PLAYERS) != 2) { psx_mod_counter_add("ff.p3.skip_not_2p", 1); return 1; }
    if (psx_mod_read_word(G_DEMO) != 0) { psx_mod_counter_add("ff.p3.skip_demo", 1); return 1; }
    /* The game's pad sampler only runs inside the level loop, which starts
     * after this call, so check seat 3 here directly. */
    p3_apply_requests();
    uint16_t raw_now;
    if (!p3_read_seat(&raw_now)) { psx_mod_counter_add("ff.p3.skip_no_seat", 1); return 1; }
    if (psx_mod_read_word(g_mem + M_DROPIN) && !psx_mod_read_word(g_mem + M_P3_JOINED)) {
        psx_mod_counter_add("ff.p3.skip_not_joined", 1);
        return 1;
    }
    if (rec_type(1) == -1) { psx_mod_counter_add("ff.p3.skip_no_p2", 1); return 1; }
    if (rec_type(P3_REC) != -1) { psx_mod_counter_add("ff.p3.skip_rec2_busy", 1); return 1; }
    /* A game loaded from the memory card: P3's fighter, health and score as
     * saved (wrap_card_save / wrap_card_loaded). */
    if (psx_mod_read_word(g_mem + M_SAVE_PENDING) && psx_mod_read_byte(G_LOADED_GAME)) {
        uint32_t sc = psx_mod_read_word(g_mem + M_SAVE_SCORE);
        psx_mod_write_word(g_mem + M_CHOICE, psx_mod_read_word(g_mem + M_SAVE_TYPE));
        psx_mod_write_word(g_mem + M_P3_SLOTS + SLOT_SCORE, sc);
        psx_mod_write_word(g_mem + M_LIFE_AT, (sc / 50000u + 1u) * 50000u);
        uint32_t shp = psx_mod_read_word(g_mem + M_SAVE_HP);
        psx_mod_write_word(g_mem + M_HP_CARRY, shp);
        psx_mod_write_word(g_mem + M_DOWN_CARRY, shp == 0 ? 1u : 0u); /* saved down, like P1 / P2 */
        psx_mod_write_word(g_mem + M_SAVE_PENDING, 0);
        psx_mod_counter_add("ff.p3.save_restored", 1);
    }
    /* Record 2 <- P2's freshly initialised record. */
    for (uint32_t o = 0; o < REC_SIZE; o += 4)
        psx_mod_write_word(rec(P3_REC, o), psx_mod_read_word(rec(1, o)));
    int16_t type = rec_type(1);
    uint32_t want = p3_pick_fighter();
    if ((int16_t)want != type) {
        uint32_t saved[32];
        memcpy(saved, cpu->gpr, sizeof(saved));
        if (p3_load_fighter(cpu, want, ra)) type = (int16_t)want;
        memcpy(cpu->gpr, saved, sizeof(saved));
    }
    psx_mod_write_word(g_mem + M_FIGHTER, (uint32_t)(uint16_t)type);
    /* Own model descriptor: P2's, with the strength / defence of P3's fighter. */
    for (uint32_t o = 0; o < DESC_SIZE; o += 4)
        psx_mod_write_word(g_mem + M_DESC + o, psx_mod_read_word(P2_DESC + o));
    psx_mod_write_byte(g_mem + M_DESC + 5, (uint8_t)psx_mod_read_word(STAT_STRENGTH + 4u * (uint32_t)type));
    psx_mod_write_byte(g_mem + M_DESC + 7, (uint8_t)psx_mod_read_word(STAT_DEFENCE + 4u * (uint32_t)type));
    psx_mod_write_word(rec(P3_REC, RO_DESC), g_mem + M_DESC);
    psx_mod_write_word(rec(P3_REC, RO_DESC2), 0);
    psx_mod_write_word(rec(P3_REC, RO_CHAR), psx_mod_read_word(CHAR_DATA + 4u * (uint32_t)type));
    psx_mod_write_half(rec(P3_REC, RO_TYPE), (uint16_t)type);
    /* Start between P1 and P2 (they stand at the level start point +/- 0xC0). */
    int32_t x = (int32_t)(((int64_t)(int32_t)psx_mod_read_word(rec(0, RO_X)) +
                           (int32_t)psx_mod_read_word(rec(1, RO_X))) / 2);
    int32_t z = (int32_t)(((int64_t)(int32_t)psx_mod_read_word(rec(0, RO_Z)) +
                           (int32_t)psx_mod_read_word(rec(1, RO_Z))) / 2);
    psx_mod_write_word(rec(P3_REC, RO_X), (uint32_t)x);
    psx_mod_write_word(rec(P3_REC, RO_Z), (uint32_t)z);
    if (type != rec_type(1)) {
        /* Its own moves from the first frame (the copy still points at P2's). */
        uint32_t saved[32];
        memcpy(saved, cpu->gpr, sizeof(saved));
        guest_call(cpu, FN_SET_ANIM, rec(P3_REC, 0),
                   psx_mod_read_word(G_MOVE_TABLES + 4u * (uint32_t)type), 0x45, ra);
        memcpy(cpu->gpr, saved, sizeof(saved));
    }
    /* P3's per-player globals for this level: like the game's level setup
     * (FUN_80018318 sets the players' item-in-reach to -1); action block 0. */
    for (uint32_t o = 0; o < 0x38; o += 4)
        if (o != SLOT_SCORE) psx_mod_write_word(g_mem + M_P3_SLOTS + o, 0); /* keep score */
    psx_mod_write_word(g_mem + M_P3_SLOTS + SLOT_ITEM, 0xFFFFFFFFu);
    p3_format_score();
    if (psx_mod_read_word(g_mem + M_LIFE_AT) == 0) psx_mod_write_word(g_mem + M_LIFE_AT, 50000);
    /* P3's own health from the last level (the game carries P1's / P2's):
     * down at the level end -> down now (health 0), as P1 / P2 stay down. */
    uint32_t carry = psx_mod_read_word(g_mem + M_HP_CARRY);
    int down = psx_mod_read_word(g_mem + M_DOWN_CARRY) != 0;
    if (down || (carry > 0 && carry < 0x8000u)) {
        uint16_t hp = down ? 0u : (uint16_t)carry;
        psx_mod_write_half(rec(P3_REC, RO_HEALTH), hp);
        psx_mod_write_half(rec(P3_REC, RO_HEALTH_A), hp);
        psx_mod_write_half(rec(P3_REC, RO_HEALTH_B), hp);
    }
    /* The down flag as FUN_80013848 sets it for every record at the level
     * start (0x3E when health < 1, else 0): the copy carried P2's, and a
     * flag set with health left skips the whole player update (frozen,
     * and no continue prompt). */
    psx_mod_write_byte(rec(P3_REC, RO_DOWN), (int16_t)psx_mod_read_half(rec(P3_REC, RO_HEALTH)) < 1 ? 0x3Eu : 0u);
    psx_mod_write_word(g_mem + M_GAME_P3, 1);
    /* The level loop gives P1 / P2 a weapon after this call when the last
     * level was left over its exit line; P3 gets its own at the loop top. */
    psx_mod_write_word(g_mem + M_GIFT, psx_mod_read_word(G_GIFT) == 1 ? 1u : 0u);
    psx_mod_write_word(g_mem + M_ACTIVE, 1);
    psx_mod_write_word(g_mem + M_SPAWNS, psx_mod_read_word(g_mem + M_SPAWNS) + 1);
    psx_mod_counter_add("ff.p3.spawned", 1);
    return 1;
}

/* Wrap of FUN_8004be28 (enemies alive in records 2..7, compared with the wave
 * script's limit). It counts P3 while P3 has health. When P3 is down, its
 * record is still occupied, so it must still count: otherwise the script
 * could spawn one enemy more than there are free records, and the spawner
 * writes a failed allocation (-1) to the memory before record 0. */
static int wrap_enemy_count(struct CPUState* cpu) {
    func_override_call_original(cpu);
    if (p3_present() && (int16_t)psx_mod_read_half(rec(P3_REC, RO_HEALTH)) < 1)
        cpu->gpr[2] += 1;
    return 1;
}

/* ---- Spawns into full records (a bug of the original game) -------------
 * The enemy allocator FUN_8004af18 returns the first record 2..7 of type -1,
 * or -1 when all six are taken. None of its seven callers (GAME.EXE has no
 * other `jal 0x8004af18`) checks: the wave script FUN_8004b228, the drop pod
 * FUN_8002efdc and the drop-off vehicle FUN_80049e6c use -1 as a record
 * index and write over the 0xF4 bytes before record 0; the wave script then
 * runs the animation decoder FUN_80011a50 with a null table entry and the
 * game dies (FAIL-FAST unknown dispatch, ra 0x8004BD4C). Seen on level 0x11,
 * with 2 players as well: drop pods add guards past the wave's limit, so the
 * script's `count == limit` wait never fires and it spawns until the records
 * run out. P3 takes one of the six records, which makes it likelier.
 * Each wrapper below holds the call for one frame when it would spawn and no
 * record is free, so the spawn happens on the first frame a record frees up;
 * with a free record the original runs unchanged. Reads guest state only, so
 * every netplay peer decides the same. */
#define FN_DROP_POD     0x8002EFDCu /* (object) one type 0x2F enemy once DAT_800941a0 < 0x300 */
#define FN_DROP_OFF     0x80049E6Cu /* (record) vehicle: an enemy at timer 0xD / 0x10 / 0x1C / 0x1F */
#define G_POD_HEIGHT    0x800941A0u
#define G_WAVE_D40      0x80093D40u /* level 6 sequence counter, run before the pause test */
#define G_WAVE_LEFT     0x80093DBCu /* enemies of the wave still to beat */
#define G_WAVE_TO_SPAWN 0x80093DC0u
#define G_WAVE_TIMER    0x80093DC4u /* frames to the next spawn; spawns when it runs 1 -> 0 */
#define G_WAVE_PAUSE    0x80093DD9u
#define G_WAVE_SCRIPT   0x800946B8u /* pointer into the wave script (s16 words) */
#define RO_TIMER        0x04u
#define RO_STATE        0x0Cu
#define RO_SPEED        0x4Cu
static const uint32_t G_DROP_POD[4] = {0x27BDFFB0u, 0xAFBF004Cu, 0xAFB40048u, 0xAFB30044u};
static const uint32_t G_DROP_OFF[4] = {0x27BDFF50u, 0x00041100u, 0x00441023u, 0x00021080u};

/* Records 2..7 FUN_8004af18 could hand out (type -1); record 2 is not free
 * while P3 is hidden (level-clear test, or p3_hide_push around a call). */
static uint32_t free_records(void) {
    int p3_hidden = g_mem && ((psx_mod_read_word(g_mem + M_HIDDEN) & 0x80000000u) ||
                              psx_mod_read_word(g_mem + M_HIDE_DEPTH) != 0);
    uint32_t n = 0;
    for (uint32_t i = 2; i < 8; i++)
        if (rec_type(i) == -1 && !(i == P3_REC && p3_hidden)) n++;
    return n;
}

/* Records this call of the wave script would allocate (FUN_8004b228's own
 * tests in order; the `count == limit` test is left to the original, which
 * returns without spawning when it holds): 2 for types 0x2C..0x2E (they bring
 * a type 0x33 partner), 1 for the others, 0 if it would not spawn. */
static uint32_t waves_spawn_need(void) {
    if ((int32_t)psx_mod_read_word(G_WAVE_D40) > 0) return 0; /* keep the level 6 sequence's timing */
    if (psx_mod_read_byte(G_WAVE_PAUSE)) return 0;
    if (psx_mod_read_word(G_WAVE_LEFT) == 0) return 0;
    if ((int32_t)psx_mod_read_word(G_WAVE_TO_SPAWN) < 1) return 0;
    if (psx_mod_read_word(G_WAVE_TIMER) != 1) return 0;
    int16_t type = (int16_t)psx_mod_read_half(psx_mod_read_word(G_WAVE_SCRIPT));
    return (type >= 0x2C && type <= 0x2E) ? 2u : 1u;
}

/* Wrap of FUN_8002efdc (drop pod): it spawns while DAT_800941a0 < 0x300. */
static int wrap_drop_pod(struct CPUState* cpu) {
    (void)cpu;
    if ((int32_t)psx_mod_read_word(G_POD_HEIGHT) < 0x300 && free_records() == 0) {
        atomic_fetch_add(&g_dbg_held[1], 1);
        return 1;
    }
    return 0;
}

/* Wrap of FUN_80049e6c(record): in state 0 its speed (+0x4C) drops by 8 a
 * call and, at 0, its timer (+4) steps; it spawns when the stepped timer is
 * 0xD, 0x10, 0x1C or 0x1F. */
static int wrap_drop_off(struct CPUState* cpu) {
    uint32_t i = cpu->gpr[4];
    if (i < 2 || i > 7 || psx_mod_read_word(rec(i, RO_STATE)) != 0) return 0;
    int32_t speed = (int16_t)psx_mod_read_half(rec(i, RO_SPEED)) - 8;
    int32_t t = (int32_t)psx_mod_read_word(rec(i, RO_TIMER)) + (speed <= 0 ? 1 : 0);
    if ((t == 0x0D || t == 0x10 || t == 0x1C || t == 0x1F) && free_records() == 0) {
        atomic_fetch_add(&g_dbg_held[2], 1);
        return 1;
    }
    return 0;
}

/* Wrap of FUN_8004b228 (wave script). A spawn with no free record is held
 * (see above). When it returns -1 the level loop
 * checks records 2..7 for live enemies and would see P3 as one, so the level
 * would never clear. Hide P3 for that test; FN_EXIT_LINE, which the loop
 * calls right after the test, puts the type back. */
static int wrap_waves(struct CPUState* cpu) {
    uint32_t need = waves_spawn_need();
    if (need && free_records() < need) {
        atomic_fetch_add(&g_dbg_held[0], 1);
        cpu->gpr[2] = 0;
        return 1;
    }
    func_override_call_original(cpu);
    if (cpu->gpr[2] == 0xFFFFFFFFu && p3_present()) {
        psx_mod_write_word(g_mem + M_HIDDEN, 0x80000000u | (uint16_t)rec_type(P3_REC));
        psx_mod_write_half(rec(P3_REC, RO_TYPE), 0xFFFFu);
    }
    return 1;
}

static void p3_unhide(void) {
    if (!g_mem) return;
    uint32_t h = psx_mod_read_word(g_mem + M_HIDDEN);
    if (h & 0x80000000u) {
        psx_mod_write_half(rec(P3_REC, RO_TYPE), (uint16_t)(h & 0xFFFFu));
        psx_mod_write_word(g_mem + M_HIDDEN, 0);
    }
}

static int wrap_exit_line(struct CPUState* cpu);
static void hold_game_over(uint32_t before);

/* ---- M3: control and combat ---------------------------------------------
 * The player update FUN_80024008 is only a loop "for p < players: update
 * record p" (nothing before or after it), and indexes several 2-entry
 * globals by p. Run as p = 2, P3 would overwrite other variables (the
 * action-button block of p = 2 overlaps auto-aim scratch values), so P3 gets
 * a second pass of the same function as player 1 in a "view": records 1 and
 * 2 trade places (with their per-record blocks), so P3 sits in slot 1 and P2
 * in record 2; P1 and P2 are typed -1 (skipped); the 2-entry globals of
 * index 1 hold P3's values; record indices stored in the records are
 * renumbered with the move. The same moves let the hit tests treat P3 as a
 * player (index < 2), never as an enemy. */
static void copy_guest(uint32_t dst, uint32_t src, uint32_t n) {
    uint32_t o = 0;
    for (; o + 4 <= n; o += 4) psx_mod_write_word(dst + o, psx_mod_read_word(src + o));
    for (; o < n; o++) psx_mod_write_byte(dst + o, psx_mod_read_byte(src + o));
}

static void swap_guest(uint32_t a, uint32_t b, uint32_t n) {
    for (uint32_t o = 0; o < n; o += 4) {
        uint32_t x = psx_mod_read_word(a + o);
        psx_mod_write_word(a + o, psx_mod_read_word(b + o));
        psx_mod_write_word(b + o, x);
    }
}

static void swap_slots_in(void) {
    uint32_t off = 0;
    for (uint32_t i = 0; i < N_SLOTS; i++) {
        copy_guest(g_mem + M_P2_SLOTS + off, P1_SLOTS[i].addr, P1_SLOTS[i].size);
        copy_guest(P1_SLOTS[i].addr, g_mem + M_P3_SLOTS + off, P1_SLOTS[i].size);
        off += 8;
    }
    /* The pad cache is not P3 state: it is this frame's seat-3 sample. */
    psx_mod_write_word(P1_SLOTS[0].addr, psx_mod_read_word(g_mem + M_PAD_WORD));
}

static void swap_slots_out(void) {
    uint32_t off = 0;
    for (uint32_t i = 0; i < N_SLOTS; i++) {
        copy_guest(g_mem + M_P3_SLOTS + off, P1_SLOTS[i].addr, P1_SLOTS[i].size);
        copy_guest(P1_SLOTS[i].addr, g_mem + M_P2_SLOTS + off, P1_SLOTS[i].size);
        off += 8;
    }
}

/* Record indices stored in the records (last target, held by, holding,
 * last attacker; an enemy's player target; the "already hit" bit mask of
 * the current attack): i and j trade values in all
 * eight records. Its own inverse, so a field the game wrote in between
 * reads back in the normal numbering (a hit P3 landed as index 1 -> 2). */
static void relabel_swap(uint32_t i, uint32_t j) {
    static const uint32_t BYTE_FIELDS[] = {RO_LAST_TARGET, RO_HELD_BY, RO_HOLDING, RO_ATTACKER};
    for (uint32_t r = 0; r < 8; r++) {
        for (uint32_t f = 0; f < sizeof(BYTE_FIELDS) / sizeof(BYTE_FIELDS[0]); f++) {
            uint8_t v = psx_mod_read_byte(rec(r, BYTE_FIELDS[f]));
            if (v == i) psx_mod_write_byte(rec(r, BYTE_FIELDS[f]), (uint8_t)j);
            else if (v == j) psx_mod_write_byte(rec(r, BYTE_FIELDS[f]), (uint8_t)i);
        }
        uint16_t m = psx_mod_read_half(rec(r, RO_HIT_MASK));
        uint16_t bi = (uint16_t)((m >> i) & 1u), bj = (uint16_t)((m >> j) & 1u);
        if (bi != bj)
            psx_mod_write_half(rec(r, RO_HIT_MASK), (uint16_t)(m ^ ((1u << i) | (1u << j))));
        uint32_t kind = psx_mod_read_word(rec(r, RO_TGT_KIND));
        uint32_t tgt = psx_mod_read_word(rec(r, RO_TGT_IDX));
        if (kind >= 1 && kind <= 3) {
            if (tgt == i) psx_mod_write_word(rec(r, RO_TGT_IDX), j);
            else if (tgt == j) psx_mod_write_word(rec(r, RO_TGT_IDX), i);
        }
    }
}

/* Records i and j (and their per-record blocks) trade places; renumbered. */
static void xswap(uint32_t i, uint32_t j) {
    swap_guest(rec(i, 0), rec(j, 0), REC_SIZE);
    swap_guest(REC_ANIM_BASE + i * REC_ANIM_SIZE, REC_ANIM_BASE + j * REC_ANIM_SIZE, REC_ANIM_SIZE);
    relabel_swap(i, j);
}

static int p3_in_view(void) { return g_mem && psx_mod_read_word(g_mem + M_VIEW) != 0; }

/* P3's type while it may be hidden from the enemy scans. */
static uint16_t p3_real_type(uint16_t now) {
    if (now == 0xFFFFu && psx_mod_read_word(g_mem + M_HIDE_DEPTH) != 0)
        return (uint16_t)psx_mod_read_word(g_mem + M_HIDE_TYPE);
    return now;
}

static void p3_view_enter(void) {
    psx_mod_write_word(g_mem + M_VIEW, 1);
    xswap(1, P3_REC); /* P3 -> slot 1, P2 -> record 2 */
    uint16_t t0 = psx_mod_read_half(rec(0, RO_TYPE));
    uint16_t t1 = psx_mod_read_half(rec(1, RO_TYPE));
    uint16_t t2 = psx_mod_read_half(rec(P3_REC, RO_TYPE));
    psx_mod_write_word(g_mem + M_VIEW_T0, t0);
    psx_mod_write_word(g_mem + M_VIEW_T1, t1);
    psx_mod_write_word(g_mem + M_VIEW_T2, t2);
    psx_mod_write_half(rec(1, RO_TYPE), p3_real_type(t1));
    psx_mod_write_half(rec(0, RO_TYPE), 0xFFFFu);
    psx_mod_write_half(rec(P3_REC, RO_TYPE), 0xFFFFu);
    swap_slots_in();
}

static void p3_view_leave(void) {
    swap_slots_out();
    psx_mod_write_half(rec(0, RO_TYPE), (uint16_t)psx_mod_read_word(g_mem + M_VIEW_T0));
    psx_mod_write_half(rec(1, RO_TYPE), (uint16_t)psx_mod_read_word(g_mem + M_VIEW_T1));
    psx_mod_write_half(rec(P3_REC, RO_TYPE), (uint16_t)psx_mod_read_word(g_mem + M_VIEW_T2));
    xswap(1, P3_REC);
    psx_mod_write_word(g_mem + M_VIEW, 0);
}

/* Objects P3 throws during its pass store thrower 1 (its slot there); put
 * its own index in them. Only throwers that changed to 1 in the pass. */
static void obj_snapshot(void) {
    for (uint32_t k = 0; k < OBJ_MAX; k++)
        psx_mod_write_half(g_mem + M_OBJ_SNAP + 2 * k,
                           psx_mod_read_half(OBJ_BASE + k * OBJ_SIZE + OBJ_THROWER));
}

static void obj_fix_throwers(void) {
    uint32_t n = psx_mod_read_word(G_OBJ_COUNT);
    if (n > OBJ_MAX) n = OBJ_MAX;
    for (uint32_t k = 0; k < n; k++) {
        uint32_t a = OBJ_BASE + k * OBJ_SIZE + OBJ_THROWER;
        if (psx_mod_read_half(a) == 1 && psx_mod_read_half(g_mem + M_OBJ_SNAP + 2 * k) != 1) {
            psx_mod_write_half(a, (uint16_t)P3_REC);
            psx_mod_write_word(g_mem + M_OBJ_FIX, psx_mod_read_word(g_mem + M_OBJ_FIX) + 1);
        }
    }
}

static int wrap_player_update(struct CPUState* cpu) {
    if (!p3_present() || rec_type(1) == -1) {
        func_override_call_original(cpu);
        return 1;
    }
    /* P1 and P2 as stock; P3 hidden so their scans of records 2..7 (grabs,
     * auto-aim) do not treat it as an enemy. Their hits on P3 come from the
     * player hit test wrap below. */
    uint32_t go = psx_mod_read_word(G_GAME_OVER);
    p3_hide_push();
    func_override_call_original(cpu);
    p3_hide_pop();
    /* Second run of the original (void, no arguments; it restores $ra and
     * the saved registers, so the entry state is valid again). A guest call
     * would re-enter this override. Only "player 1" = P3 is updated. */
    obj_snapshot();
    p3_view_enter();
    func_override_call_original(cpu);
    p3_view_leave();
    obj_fix_throwers();
    hold_game_over(go);
    psx_mod_write_word(g_mem + M_PASSES, psx_mod_read_word(g_mem + M_PASSES) + 1);
    return 1;
}

/* Enemies: the limb hit test FUN_80048dbc(enemy, limb) only tests records 0
 * and 1 (a fixed bound, not the player count) and stops at the first hit.
 * When it hit neither P1 nor P2, test P3 in the view (P1 skipped there). */
static int wrap_enemy_hit(struct CPUState* cpu) {
    uint32_t a0 = cpu->gpr[4], a1 = cpu->gpr[5];
    func_override_call_original(cpu);
    if (cpu->gpr[2] != 0 || !p3_present() || a0 < 3 || a0 > 7) return 1;
    p3_view_enter();
    cpu->gpr[4] = a0;
    cpu->gpr[5] = a1;
    func_override_call_original(cpu);
    p3_view_leave();
    if (cpu->gpr[2] != 0)
        psx_mod_write_word(g_mem + M_P3_HITS, psx_mod_read_word(g_mem + M_P3_HITS) + 1);
    return 1;
}

/* Debug only ("ff.p3 fftrace on|off"): log every second run of a player hit
 * test with what the game's test reads, to stderr. */
static _Atomic uint32_t g_fftrace = 0;
static void fftrace_slot(const char* tag, uint32_t s) {
    fprintf(stderr, " %s[t%d hp%d x%d y%d z%d inv%d st%X]", tag, rec_type(s),
            (int16_t)psx_mod_read_half(rec(s, RO_HEALTH)), (int32_t)psx_mod_read_word(rec(s, RO_X)) >> 16,
            (int32_t)psx_mod_read_word(rec(s, 0x2Cu)) >> 16, (int32_t)psx_mod_read_word(rec(s, RO_Z)) >> 16,
            (int16_t)psx_mod_read_half(rec(s, 0x50u)), psx_mod_read_word(rec(s, 0x0Cu)));
}

/* One more run of the player hit test with the saved arguments. */
static int hit_again(struct CPUState* cpu, const uint32_t a[3]) {
    int trace = atomic_load(&g_fftrace) != 0;
    if (trace) {
        fprintf(stderr, "fftrace view=%u atk=%u pt=(%d,%d,%d) mask=%X", (unsigned)p3_in_view(), (unsigned)a[0],
                (int32_t)psx_mod_read_word(a[1]) >> 16, (int32_t)psx_mod_read_word(a[1] + 4) >> 16,
                (int32_t)psx_mod_read_word(a[1] + 8) >> 16, psx_mod_read_half(rec(a[0] & 7u, 0x52u)));
        fftrace_slot("s0", 0);
        fftrace_slot("s1", 1);
        fftrace_slot("r2", 2);
    }
    cpu->gpr[4] = a[0];
    cpu->gpr[5] = a[1];
    cpu->gpr[6] = a[2];
    func_override_call_original(cpu);
    if (trace) fprintf(stderr, " -> %u\n", (unsigned)cpu->gpr[2]);
    return cpu->gpr[2] != 0;
}

/* Damage calls FUN_8001f230 so far (wrap_damage; host, emulation thread).
 * A gun shot's damage scan FUN_800322c8 returns nothing and hurts at most
 * one record, so a run of it hit someone exactly when this moved. Only
 * compared within one hook call, so it never reaches the simulation. */
static uint32_t g_damage_seq = 0;

/* One more run of the shot damage scan with the saved arguments. */
static int shot_again(struct CPUState* cpu, const uint32_t a[3]) {
    uint32_t before = g_damage_seq;
    cpu->gpr[4] = a[0];
    cpu->gpr[5] = a[1];
    cpu->gpr[6] = a[2];
    func_override_call_original(cpu);
    return g_damage_seq != before;
}

/* A run of a hit test: hit_again (melee / thrown) or shot_again (gun). */
typedef int (*HitRun)(struct CPUState* cpu, const uint32_t a[3]);

static void ff_hit(void) {
    psx_mod_write_word(g_mem + M_FF_HITS, psx_mod_read_word(g_mem + M_FF_HITS) + 1);
}

/* Inside the view P3 (slot 1) attacks: P1 (slot 0) and P2 (record 2) are
 * typed -1. Try P1, then P2 moved to slot 0, so both are player targets
 * (xswap renumbers the attacker's "already hit" bits with them). */
static int view_hit_players(struct CPUState* cpu, const uint32_t a[3], HitRun run) {
    uint16_t t0 = (uint16_t)psx_mod_read_word(g_mem + M_VIEW_T0);
    uint16_t t2 = (uint16_t)psx_mod_read_word(g_mem + M_VIEW_T2);
    if (t0 != 0xFFFFu) {
        psx_mod_write_half(rec(0, RO_TYPE), t0);
        int hit = run(cpu, a);
        psx_mod_write_half(rec(0, RO_TYPE), 0xFFFFu);
        if (hit) { ff_hit(); return 1; }
    }
    if (t2 != 0xFFFFu) {
        xswap(0, P3_REC); /* P2 -> slot 0, P1 -> record 2 (both typed -1) */
        psx_mod_write_half(rec(0, RO_TYPE), t2);
        int hit = run(cpu, a);
        psx_mod_write_half(rec(0, RO_TYPE), 0xFFFFu);
        xswap(0, P3_REC);
        if (hit) { ff_hit(); return 1; }
    }
    return 0;
}

/* ---- Friendly fire off (Quick Start option "friendly_fire") ---------------
 * Every damage a player deals goes through one of these tests (Ghidra, all
 * callers of the damage routine FUN_8001f230 and its two wrappers): melee FUN_8001c35c (skips players only when
 * DAT_80093d44 == 0), thrown objects FUN_8001bfa0, and the blast / shot
 * tests FUN_80031f64 and FUN_800322c8 (owner = the object's s16 at +0x1C).
 * Those scans skip a record typed -1, so with friendly fire off a player's
 * attack runs with the other players typed -1 for that call only. The
 * held-weapon swing FUN_8001d10c scans records 2..7 only (P3 is hidden or
 * in its own pass then): never a player. Attacks by enemies and by the
 * scenery (owner -1) are unchanged. */
int ff_qs_friendly_fire(void); /* quick_start.c */
#define FN_BLAST_HIT_A    0x80031F64u /* (x, z, object): blast around a point */
#define FN_BLAST_HIT_B    0x800322C8u /* (x, z, object): blast / where a shot stopped */
#define FN_SHOT_FIND      0x80019BA8u /* (x, z, shot): first record a shot reaches */
#define OBJ_OWNER         0x1Cu       /* s16: record that owns the object */

static int p3_present(void);
static int is_player_rec(int32_t r) { return r == 0 || r == 1 || (r == (int32_t)P3_REC && p3_present()); }

typedef struct { uint16_t t[2]; int p3; } OtherPlayers;
static void others_hide(uint32_t attacker, OtherPlayers* o) {
    for (uint32_t i = 0; i < 2; i++) {
        o->t[i] = psx_mod_read_half(rec(i, RO_TYPE));
        if (i != attacker) psx_mod_write_half(rec(i, RO_TYPE), 0xFFFFu);
    }
    o->p3 = attacker != P3_REC && p3_present();
    if (o->p3) p3_hide_push();
}
static void others_show(uint32_t attacker, const OtherPlayers* o) {
    if (o->p3) p3_hide_pop();
    for (uint32_t i = 0; i < 2; i++)
        if (i != attacker) psx_mod_write_half(rec(i, RO_TYPE), o->t[i]);
}

/* Run the original with the other players hidden from attacker a. */
static void call_without_others(struct CPUState* cpu, uint32_t a) {
    OtherPlayers o;
    others_hide(a, &o);
    func_override_call_original(cpu);
    others_show(a, &o);
}

/* Debug counters only (host side, nothing written to the game): damage
 * FUN_8001f230(attacker, target, base) -- every damage path ends there --
 * dealt by a player to another player (friendly fire) and to anything else.
 * By record, not type (a player hidden as type -1 still counts as one).
 * Runs the original unchanged. */
#define FN_DAMAGE         0x8001F230u
static _Atomic uint32_t g_dbg_pvp = 0;
static _Atomic uint32_t g_dbg_pve = 0;
static _Atomic uint32_t g_dbg_pblast = 0; /* blast tests of a player's object */
static _Atomic uint32_t g_dbg_pshot = 0;  /* shot lookups of a player's shot */
static int player_slot(int32_t r) {
    return r == 0 || r == 1 || (r == (int32_t)P3_REC && (p3_present() || p3_in_view()));
}
static int wrap_damage(struct CPUState* cpu) {
    g_damage_seq++;
    int32_t a = (int32_t)cpu->gpr[4], t = (int32_t)cpu->gpr[5];
    if (t >= 0 && t < 8 && a != t && player_slot(a)) {
        if (player_slot(t)) atomic_fetch_add(&g_dbg_pvp, 1);
        else atomic_fetch_add(&g_dbg_pve, 1);
    }
    return 0;
}

static int wrap_ff_blast(struct CPUState* cpu) {
    int32_t owner = (int16_t)psx_mod_read_half(cpu->gpr[6] + OBJ_OWNER);
    if (!is_player_rec(owner)) return 0;
    atomic_fetch_add(&g_dbg_pblast, 1);
    if (ff_qs_friendly_fire()) return 0;
    call_without_others(cpu, (uint32_t)owner);
    return 1;
}

/* A gun shot (FUN_8002fd90) stops at the first record FUN_80019ba8(x, z,
 * shot) finds -- any type; it skips the shooter (shot +0x1C), records that
 * are down / held / holding, and health < 1 -- and FUN_800322c8 then deals
 * the damage there. With friendly fire off the other players are skipped
 * here too (health 0 for this call only; the lookup only reads), so a shot
 * passes through them to the enemy behind. Players are in records 0, 1 and
 * 2 while P3 is in record 2 or in its own pass (P2 waits in record 2). */
static void shot_trace(int32_t owner, uint32_t x, uint32_t z, int32_t found) {
    fprintf(stderr, "shotfind owner=%d pt=(%d,%d) ->%d", owner, (int32_t)x >> 16, (int32_t)z >> 16, found);
    for (uint32_t i = 0; i < 8; i++)
        fprintf(stderr, " r%u[t%d hp%d x%d z%d st%X]", i, rec_type(i), (int16_t)psx_mod_read_half(rec(i, RO_HEALTH)),
                (int32_t)psx_mod_read_word(rec(i, RO_X)) >> 16, (int32_t)psx_mod_read_word(rec(i, RO_Z)) >> 16,
                psx_mod_read_word(rec(i, 0x0Cu)));
    fprintf(stderr, "\n");
}

static int wrap_ff_shot_find(struct CPUState* cpu) {
    int32_t owner = (int16_t)psx_mod_read_half(cpu->gpr[6] + OBJ_OWNER);
    int with_p3 = p3_in_view() || p3_present();
    if (!(owner == 0 || owner == 1 || (owner == (int32_t)P3_REC && with_p3))) return 0;
    atomic_fetch_add(&g_dbg_pshot, 1);
    const int ff_on = ff_qs_friendly_fire();
    const int trace = atomic_load(&g_fftrace) != 0; /* debug: "ff.p3 fftrace on" */
    if (ff_on && !trace) return 0;
    uint32_t x = cpu->gpr[4], z = cpu->gpr[5];
    uint32_t n = with_p3 ? 3u : 2u;
    uint16_t hp[3] = {0, 0, 0};
    if (!ff_on)
        for (uint32_t i = 0; i < n; i++)
            if ((int32_t)i != owner) {
                hp[i] = psx_mod_read_half(rec(i, RO_HEALTH));
                psx_mod_write_half(rec(i, RO_HEALTH), 0);
            }
    func_override_call_original(cpu);
    if (!ff_on)
        for (uint32_t i = 0; i < n; i++)
            if ((int32_t)i != owner) psx_mod_write_half(rec(i, RO_HEALTH), hp[i]);
    if (trace) shot_trace(owner, x, z, (int32_t)cpu->gpr[2]);
    return 1;
}

/* Hit tests of players' attacks: melee FUN_8001c35c(attacker, point) and
 * thrown objects FUN_8001bfa0(thrower, point, object). Both scan records
 * 0..7 (players hit each other; melee only when DAT_80093d44 != 0) and stop
 * at the first hit; what follows a hit depends on the index (< 2 = player;
 * >= 2 = enemy KO count, combo score, enemy HUD). So P3 is never tested as
 * record 2: it is moved into a player slot that is not the attacker's. The
 * players are tested before the enemies (see wrap_player_hit_body). */
static int wrap_player_hit_body(struct CPUState* cpu, const uint32_t a[3]);
static int wrap_player_hit(struct CPUState* cpu) {
    uint32_t a[3] = {cpu->gpr[4], cpu->gpr[5], cpu->gpr[6]};
    int trace = atomic_load(&g_fftrace) != 0;
    if (trace)
        fprintf(stderr, "ffcall view=%u atk=%u pt=(%d,%d)\n", (unsigned)p3_in_view(), (unsigned)a[0],
                (int32_t)psx_mod_read_word(a[1]) >> 16, (int32_t)psx_mod_read_word(a[1] + 8) >> 16);
    int r = wrap_player_hit_body(cpu, a);
    if (trace) fprintf(stderr, "ffcall end -> %u\n", (unsigned)cpu->gpr[2]);
    return r;
}

/* Records 3..7 (the enemies; record 2 is a player in these tests) typed -1
 * for a players-only run of a hit test, and back. */
typedef struct { uint16_t t[5]; } EnemyTypes;
static void enemies_hide(EnemyTypes* e) {
    for (uint32_t i = 0; i < 5; i++) {
        e->t[i] = psx_mod_read_half(rec(3 + i, RO_TYPE));
        psx_mod_write_half(rec(3 + i, RO_TYPE), 0xFFFFu);
    }
}
static void enemies_show(const EnemyTypes* e) {
    for (uint32_t i = 0; i < 5; i++) psx_mod_write_half(rec(3 + i, RO_TYPE), e->t[i]);
}

/* P3 (record 2, maybe hidden) as the target of attacker 0 / 1's attack a:
 * P3 into the player slot the attacker does not use; whoever was there
 * waits in record 2, typed -1. */
static int hit_p3_as_player(struct CPUState* cpu, const uint32_t a[3], uint32_t attacker, HitRun run) {
    uint16_t now = psx_mod_read_half(rec(P3_REC, RO_TYPE));
    uint16_t real = p3_real_type(now);
    if (real == 0xFFFFu) return 0;
    uint32_t k = (attacker == 1) ? 0u : 1u;
    xswap(k, P3_REC);
    uint16_t t_other = psx_mod_read_half(rec(P3_REC, RO_TYPE));
    psx_mod_write_half(rec(k, RO_TYPE), real);
    psx_mod_write_half(rec(P3_REC, RO_TYPE), 0xFFFFu);
    int hit = run(cpu, a);
    psx_mod_write_half(rec(P3_REC, RO_TYPE), t_other);
    psx_mod_write_half(rec(k, RO_TYPE), now);
    xswap(k, P3_REC);
    if (hit) ff_hit();
    return hit;
}

/* The stock test scans records 0..7 after the objects and the first hit
 * wins, so a swing that reaches a player and an enemy hits the player.
 * Keep that order with P3: every run first with the enemies hidden (the
 * players; objects still come first inside the test), then -- if nothing was
 * hit -- once more for the enemies. */
static int wrap_player_hit_body(struct CPUState* cpu, const uint32_t a[3]) {
    const int ff_on = ff_qs_friendly_fire();
    if (!g_mem || !psx_mod_read_word(g_mem + M_ACTIVE) ||
        (psx_mod_read_word(g_mem + M_HIDDEN) & 0x80000000u)) {
        if (!ff_on && (a[0] == 0 || a[0] == 1)) call_without_others(cpu, a[0]);
        else func_override_call_original(cpu);
        return 1;
    }
    EnemyTypes e;
    if (p3_in_view()) {
        if (a[0] != 1) {
            func_override_call_original(cpu);
            return 1;
        }
        /* P3 attacks from slot 1: P1, then P2, then the enemies (in the
         * view P1 and P2 are typed -1, so the enemy run cannot reach them). */
        int hit = 0;
        if (ff_on) {
            enemies_hide(&e);
            hit = view_hit_players(cpu, a, hit_again);
            enemies_show(&e);
        }
        if (!hit) hit_again(cpu, a);
        return 1;
    }
    if (a[0] == P3_REC) {
        /* An object P3 threw: test it as P3's attack, in the view. */
        uint32_t v[3] = {1, a[1], a[2]};
        p3_view_enter();
        int hit = 0;
        if (ff_on) {
            enemies_hide(&e);
            hit = view_hit_players(cpu, v, hit_again);
            enemies_show(&e);
        }
        if (!hit) hit_again(cpu, v);
        p3_view_leave();
        return 1;
    }
    /* Friendly fire off: a player's attack reaches the enemies only. */
    if (!ff_on && (a[0] == 0 || a[0] == 1)) {
        call_without_others(cpu, a[0]);
        return 1;
    }
    /* P1 / P2 attack: the other of them, then P3, then the enemies. */
    enemies_hide(&e);
    p3_hide_push();
    int hit = hit_again(cpu, a);
    p3_hide_pop();
    if (!hit) hit = hit_p3_as_player(cpu, a, a[0], hit_again);
    enemies_show(&e);
    if (hit) return 1;
    p3_hide_push();
    hit_again(cpu, a);
    p3_hide_pop();
    return 1;
}

/* Friendly fire on, gun shots: a player's shot stops at the first
 * record the lookup finds -- a friend typed -1 for this pass included (P3
 * during P1's / P2's update; P1 and P2 during P3's) -- and the damage scan
 * FUN_800322c8 then skips type -1, so the shot stopped at the friend and
 * hurt nobody. For guns that scan hurts the first record in index order
 * within 0x40 and returns (Ghidra), so run it in the same order as the
 * melee test: the players in their own slots, then the hidden friend in a
 * player slot (never as record 2: that index takes the enemy paths), then
 * the enemies. Other objects' blasts hurt every record in reach; they keep
 * wrap_ff_blast (friendly fire off). */
static int p3_hidden_now(void);
static int is_gun_kind(uint32_t k) { return k - 499u < 5u || k - 0x1F8u < 2u || k == 0x203u; }

static int wrap_ff_blast_b(struct CPUState* cpu) {
    const uint32_t a[3] = {cpu->gpr[4], cpu->gpr[5], cpu->gpr[6]};
    int32_t owner = (int16_t)psx_mod_read_half(a[2] + OBJ_OWNER);
    if (g_mem && psx_mod_read_word(g_mem + M_ACTIVE) && ff_qs_friendly_fire() &&
        is_gun_kind(psx_mod_read_word(a[2]))) {
        EnemyTypes e;
        if (p3_in_view() && owner == 1) {          /* P3 shot, from slot 1 */
            atomic_fetch_add(&g_dbg_pblast, 1);
            enemies_hide(&e);
            int hit = view_hit_players(cpu, a, shot_again);
            enemies_show(&e);
            if (!hit) shot_again(cpu, a);
            return 1;
        }
        if (!p3_in_view() && (owner == 0 || owner == 1) && p3_hidden_now()) { /* P1 / P2 shot */
            atomic_fetch_add(&g_dbg_pblast, 1);
            enemies_hide(&e);
            int hit = shot_again(cpu, a);
            if (!hit) hit = hit_p3_as_player(cpu, a, (uint32_t)owner, shot_again);
            enemies_show(&e);
            if (!hit) shot_again(cpu, a);
            return 1;
        }
    }
    return wrap_ff_blast(cpu);
}

/* Enemies: the nearest-player chooser loops p < players; let it see P3. */
static int wrap_nearest_player(struct CPUState* cpu) {
    if (!p3_present()) {
        func_override_call_original(cpu);
        return 1;
    }
    /* Its player loop tests health only, its "other enemy" loop the type:
     * count 3 + hidden = P3 is a player there and not an enemy. */
    uint32_t n = psx_mod_read_word(G_PLAYERS);
    psx_mod_write_word(G_PLAYERS, 3);
    p3_hide_push();
    func_override_call_original(cpu);
    p3_hide_pop();
    psx_mod_write_word(G_PLAYERS, n);
    return 1;
}

static uint32_t approx_dist(uint32_t a, uint32_t b) {
    int32_t dx = (int32_t)psx_mod_read_word(rec(a, RO_X)) - (int32_t)psx_mod_read_word(rec(b, RO_X));
    int32_t dz = (int32_t)psx_mod_read_word(rec(a, RO_Z)) - (int32_t)psx_mod_read_word(rec(b, RO_Z));
    uint32_t ux = (uint32_t)(dx < 0 ? -dx : dx), uz = (uint32_t)(dz < 0 ? -dz : dz);
    return ux > uz ? ux + (uz >> 1) : uz + (ux >> 1); /* same metric as the game */
}

/* The player branch of FUN_80048844(e, p): an enemy that targets player p is
 * an active attacker (kind 1) unless DAT_80093e18 other live enemies already
 * attack p (kind 1 or 3); then it waits (kind 2). The game's function cannot
 * be asked for p = 2: p >= 2 means item p - 2 there. */
static int p3_attackers_full(uint32_t e) {
    int32_t limit = (int32_t)psx_mod_read_word(G_MAX_ATTACKERS);
    int32_t n = 0;
    for (uint32_t i = 2; i < 8; i++) {
        if (i == e || i == P3_REC) continue;
        if (rec_type(i) == -1 || (int16_t)psx_mod_read_half(rec(i, RO_HEALTH)) < 1) continue;
        uint32_t k = psx_mod_read_word(rec(i, RO_TGT_KIND));
        if ((k == 1 || k == 3) && psx_mod_read_word(rec(i, RO_TGT_IDX)) == P3_REC) n++;
        if (n >= limit) return 1;
    }
    return 0;
}

/* Enemies: the weighted chooser has exactly two player entries. After it
 * picked a player, prefer P3 when P3 is alive and nearer; the active/waiting
 * role is then decided for P3 as the chooser does for P1/P2. */
static int wrap_weighted_target(struct CPUState* cpu) {
    uint32_t e = cpu->gpr[4];
    func_override_call_original(cpu);
    if (!p3_present() || e < 3 || e > 7) return 1;
    uint32_t kind = psx_mod_read_word(rec(e, RO_TGT_KIND));
    int32_t tgt = (int32_t)psx_mod_read_word(rec(e, RO_TGT_IDX));
    if ((kind != 1 && kind != 2) || tgt < 0 || tgt > 1) return 1;
    if ((int16_t)psx_mod_read_half(rec(P3_REC, RO_HEALTH)) < 1) return 1;
    if (approx_dist(e, P3_REC) < approx_dist(e, (uint32_t)tgt)) {
        psx_mod_write_word(rec(e, RO_TGT_IDX), P3_REC);
        psx_mod_write_word(rec(e, RO_TGT_KIND), p3_attackers_full(e) ? 2u : 1u);
        psx_mod_write_word(g_mem + M_RETARGETS, psx_mod_read_word(g_mem + M_RETARGETS) + 1);
    }
    return 1;
}

/* ---- P3 and the game's two record groups --------------------------------
 * The game treats records 0..players-1 as players and 2..7 as enemies.
 * Code that scans 2..7 as enemies must not see P3 (P1/P2 could punch or grab
 * it, enemies would steer around it as a fellow enemy): P3 is hidden there
 * by setting its type to -1 (the scans skip such records). Code that loops
 * over the players must see it: the player count is 3 there. */
static void p3_hide_push(void) {
    uint32_t d = psx_mod_read_word(g_mem + M_HIDE_DEPTH);
    if (d == 0) {
        psx_mod_write_word(g_mem + M_HIDE_TYPE, psx_mod_read_half(rec(P3_REC, RO_TYPE)));
        psx_mod_write_half(rec(P3_REC, RO_TYPE), 0xFFFFu);
    }
    psx_mod_write_word(g_mem + M_HIDE_DEPTH, d + 1);
}

static void p3_hide_pop(void) {
    uint32_t d = psx_mod_read_word(g_mem + M_HIDE_DEPTH);
    if (d == 0) return;
    if (d == 1)
        psx_mod_write_half(rec(P3_REC, RO_TYPE), (uint16_t)psx_mod_read_word(g_mem + M_HIDE_TYPE));
    psx_mod_write_word(g_mem + M_HIDE_DEPTH, d - 1);
}

static int p3_hidden_now(void) { return g_mem && psx_mod_read_word(g_mem + M_HIDE_DEPTH) != 0; }

/* Run the original with P3 hidden from enemy-side record scans. */
static int wrap_hide_p3(struct CPUState* cpu) {
    if (!p3_present() && !p3_hidden_now()) {
        func_override_call_original(cpu);
        return 1;
    }
    p3_hide_push();
    func_override_call_original(cpu);
    p3_hide_pop();
    return 1;
}

/* Run the original with the player count at 3 (P3 counts as a player). */
static int wrap_count3(struct CPUState* cpu) {
    /* Enemy weapon / special attacks FUN_8004930c / FUN_8004db94 loop over
     * players p < players (no type test) with a per-attack hit mask. Run them
     * for P1/P2, then once more in the view, where P3 is player 1 (P1 was
     * tested already: hit -> its mask bit is set, missed -> missed again), so
     * a hit on P3 follows the player rules (damage scaling, KO). */
    uint32_t a[4] = {cpu->gpr[4], cpu->gpr[5], cpu->gpr[6], cpu->gpr[7]};
    func_override_call_original(cpu);
    if (!p3_present()) return 1;
    uint32_t v0 = cpu->gpr[2];
    p3_view_enter();
    cpu->gpr[4] = a[0];
    cpu->gpr[5] = a[1];
    cpu->gpr[6] = a[2];
    cpu->gpr[7] = a[3];
    func_override_call_original(cpu);
    p3_view_leave();
    atomic_fetch_add(&g_dbg_weapon_views, 1);
    if (cpu->gpr[2] != 0) {
        psx_mod_write_word(g_mem + M_P3_HITS, psx_mod_read_word(g_mem + M_P3_HITS) + 1);
        atomic_fetch_add(&g_dbg_weapon_p3, 1);
    }
    cpu->gpr[2] |= v0;
    return 1;
}

/* Enemies: the enemy loop FUN_8004a704 raises +0xB8 of every live record
 * 2..7 up to 4 over time (enemy aggression) and clears +0xBC. For a fighter
 * +0xB8 is the row of the descriptor that holds its strength (byte 5) and
 * defence (byte 7) -- row 4 of a player descriptor is zero, which made P3's
 * hits and the hits it took all do the minimum damage. Keep P3's values. */
static int wrap_enemy_loop(struct CPUState* cpu) {
    if (!p3_present()) {
        func_override_call_original(cpu);
        return 1;
    }
    uint32_t lvl = psx_mod_read_word(rec(P3_REC, RO_AI_LEVEL));
    uint32_t step = psx_mod_read_word(rec(P3_REC, RO_AI_STEP));
    func_override_call_original(cpu);
    if (!g_mem || !psx_mod_read_word(g_mem + M_ACTIVE)) return 1;
    psx_mod_write_word(rec(P3_REC, RO_AI_LEVEL), lvl);
    psx_mod_write_word(rec(P3_REC, RO_AI_STEP), step);
    return 1;
}

/* ---- Downed players, game over, P3 alone ---------------------------------
 * Stock rules: a downed player gets the continue prompt while the other one
 * stands and lives are left; the game ends when both players are down. With
 * P3 "the other one" may be P3, and the game ends when all three are down.
 * While P1 and P2 are both down and P3 stands, the 2-player checks (camera
 * focus, area lines, exit line) see P3 in slot 0. */
static int hp_of(uint32_t r) { return (int16_t)psx_mod_read_half(rec(r, RO_HEALTH)); }
static int p3_alive(void) { return p3_present() && hp_of(P3_REC) > 0; }
static int only_p3_alive(void) { return p3_alive() && hp_of(0) < 1 && hp_of(1) < 1; }
static int someone_alive(void) { return hp_of(0) > 0 || hp_of(1) > 0 || p3_alive(); }

/* P3 (record 2, maybe hidden) into slot k with its real type; returns the
 * type its record had, for sub_leave. */
static uint16_t sub_enter(uint32_t k) {
    uint16_t now = psx_mod_read_half(rec(P3_REC, RO_TYPE));
    xswap(k, P3_REC);
    psx_mod_write_half(rec(k, RO_TYPE), p3_real_type(now));
    if (k == 0) psx_mod_write_word(g_mem + M_SUB, 1);
    return now;
}

static void sub_leave(uint32_t k, uint16_t type_before) {
    psx_mod_write_half(rec(k, RO_TYPE), type_before);
    xswap(k, P3_REC);
    if (k == 0) psx_mod_write_word(g_mem + M_SUB, 0);
}

/* Game over is started (0 -> nonzero) by the player update and the exit
 * line test when records 0 and 1 are both down; undo it while anyone stands. */
static void hold_game_over(uint32_t before) {
    if (before == 0 && psx_mod_read_word(G_GAME_OVER) != 0 && someone_alive()) {
        psx_mod_write_word(G_GAME_OVER, 0);
        psx_mod_write_word(g_mem + M_GAMEOVER_HELD, psx_mod_read_word(g_mem + M_GAMEOVER_HELD) + 1);
    }
}

/* Camera target: P3 kept out of its enemy scan (wrap_hide_p3), and in slot
 * 0 while it stands alone. */
static int wrap_cam_target(struct CPUState* cpu) {
    if (g_mem && only_p3_alive() && !psx_mod_read_word(g_mem + M_SUB)) {
        uint16_t t = sub_enter(0);
        func_override_call_original(cpu);
        sub_leave(0, t);
        return 1;
    }
    return wrap_hide_p3(cpu);
}

/* "Is player p out": the level loop skips the camera when both are; P1 is
 * reported in while P3 stands alone (the camera then sees P3 in slot 0). */
static int wrap_player_out(struct CPUState* cpu) {
    if (g_mem && cpu->gpr[4] == 0 && only_p3_alive() && !psx_mod_read_word(g_mem + M_SUB)) {
        cpu->gpr[2] = 0;
        return 1;
    }
    func_override_call_original(cpu);
    return 1;
}

/* Continue prompt of P1 / P2 (FUN_80061e6c(p), from the HUD): it needs "the
 * other player" (1 - p) standing and puts the continuing player at it.
 * When that one is down and P3 stands, P3 is the other player. */
static int wrap_continue(struct CPUState* cpu) {
    uint32_t p = cpu->gpr[4];
    if (!g_mem || p > 1 || !p3_alive() || hp_of(1u - p) > 0 || p3_in_view()) {
        func_override_call_original(cpu);
        return 1;
    }
    uint32_t a0 = cpu->gpr[4];
    uint16_t t = sub_enter(1u - p);
    cpu->gpr[4] = a0;
    func_override_call_original(cpu);
    sub_leave(1u - p, t);
    return 1;
}

/* ---- Camera and spacing for three ----------------------------------------
 * Stock (FUN_80047600): focus = the living player, or the midpoint of P1
 * and P2 with the pull-back DAT_80094840 = their distance (max(|dx|,|dz|)
 * + min/2). With P3: focus = the centre of the living players, pull-back =
 * the largest such distance between two of them. The leash (FUN_8001aef4,
 * movement of record r) keeps P1 and P2 within 0x400 in X and Z of each
 * other unless the move brings them closer or the other is down; P3 had
 * none. Now every player keeps that rule against every other standing one,
 * so the three stay on one screen. */
#define FN_MOVE_APPLY     0x8001AEF4u
#define G_CAM_FOCUS_X     0x80094004u
#define G_CAM_FOCUS_Z     0x80094008u
#define G_CAM_SPREAD      0x80094840u
#define LEASH             0x4000000   /* 0x400 units, 16.16 */

static int32_t pos_x(uint32_t r) { return (int32_t)psx_mod_read_word(rec(r, RO_X)); }
static int32_t pos_z(uint32_t r) { return (int32_t)psx_mod_read_word(rec(r, RO_Z)); }

static int32_t approx_units(int32_t ax, int32_t az, int32_t bx, int32_t bz) {
    int32_t dx = (ax >> 16) - (bx >> 16), dz = (az >> 16) - (bz >> 16);
    if (dx < 0) dx = -dx;
    if (dz < 0) dz = -dz;
    return dz < dx ? dx + (dz >> 1) : dz + (dx >> 1); /* FUN_800163f4 */
}

static int wrap_cam_focus(struct CPUState* cpu) {
    if (!p3_alive()) {
        func_override_call_original(cpu);
        return 1;
    }
    uint32_t live[3];
    uint32_t n = 0;
    for (uint32_t r = 0; r < 3; r++)
        if (hp_of(r) > 0) live[n++] = r;
    psx_mod_write_word(G_CAM_SPREAD, 0);
    if (n == 1) {
        psx_mod_write_word(G_CAM_FOCUS_X, (uint32_t)pos_x(live[0]));
        psx_mod_write_word(G_CAM_FOCUS_Z, (uint32_t)pos_z(live[0]));
        return 1;
    }
    int32_t sx = 0, sz = 0, spread = 0;
    for (uint32_t k = 0; k < n; k++) {
        sx += pos_x(live[k]) >> 16;
        sz += pos_z(live[k]) >> 16;
        for (uint32_t m = k + 1; m < n; m++) {
            int32_t d = approx_units(pos_x(live[k]), pos_z(live[k]), pos_x(live[m]), pos_z(live[m]));
            if (d > spread) spread = d;
        }
    }
    psx_mod_write_word(G_CAM_FOCUS_X, (uint32_t)(int32_t)(((int64_t)sx * 0x10000) / (int32_t)n));
    psx_mod_write_word(G_CAM_FOCUS_Z, (uint32_t)(int32_t)(((int64_t)sz * 0x10000) / (int32_t)n));
    psx_mod_write_word(G_CAM_SPREAD, (uint32_t)spread);
    return 1;
}

static int64_t dist2_units(int32_t ax, int32_t az, int32_t bx, int32_t bz) {
    int64_t dx = (ax >> 16) - (bx >> 16), dz = (az >> 16) - (bz >> 16);
    return dx * dx + dz * dz;
}

static int wrap_move_apply(struct CPUState* cpu) {
    uint32_t r = cpu->gpr[4];
    if (r > 2 || !p3_present() || psx_mod_read_word(G_PLAYERS) != 2) {
        func_override_call_original(cpu);
        return 1;
    }
    int32_t ox = pos_x(r), oz = pos_z(r);
    func_override_call_original(cpu);
    int32_t nx = pos_x(r), nz = pos_z(r);
    if (nx == ox && nz == oz) return 1;
    for (uint32_t q = 0; q < 3; q++) {
        if (q == r) continue;
        if (r < 2 && q == 1u - r) continue;         /* the game's own check */
        if (q == P3_REC && !p3_alive()) continue;
        if (hp_of(q) < 1) continue;                 /* a downed player does not hold */
        int32_t qx = pos_x(q), qz = pos_z(q);
        if (dist2_units(nx, nz, qx, qz) < dist2_units(ox, oz, qx, qz)) continue; /* closer */
        uint32_t dx = (uint32_t)(nx - qx) + (uint32_t)(LEASH - 1);
        uint32_t dz = (uint32_t)(nz - qz) + (uint32_t)(LEASH - 1);
        if (dx < (uint32_t)(2 * LEASH - 1) && dz < (uint32_t)(2 * LEASH - 1)) continue;
        psx_mod_write_word(rec(r, RO_X), (uint32_t)ox); /* rejected, like the game */
        psx_mod_write_word(rec(r, RO_Z), (uint32_t)oz);
        psx_mod_write_word(g_mem + M_LEASH_HOLDS, psx_mod_read_word(g_mem + M_LEASH_HOLDS) + 1);
        return 1;
    }
    return 1;
}

/* Area lines ("every standing player is past X / Z"): P3 too. */
static int wrap_past_line(struct CPUState* cpu) {
    if (!p3_alive() || psx_mod_read_word(g_mem + M_SUB)) {
        func_override_call_original(cpu);
        return 1;
    }
    uint32_t a0 = cpu->gpr[4];
    if (!only_p3_alive()) {
        func_override_call_original(cpu);
        if (cpu->gpr[2] == 0) return 1;
    }
    cpu->gpr[4] = a0;
    uint16_t t = sub_enter(0);
    func_override_call_original(cpu);
    sub_leave(0, t);
    cpu->gpr[2] = cpu->gpr[2] != 0;
    return 1;
}

static void p3_tally_step(struct CPUState* cpu, uint32_t ra);
static void p3_pause_check(uint32_t exit_v0);

/* Exit line test FUN_8001886c (each frame after the level-clear test): put
 * P3's type back after the clear test, test with P3 in slot 0 while it
 * stands alone, keep the game running while anyone stands, and run P3's
 * level-end tally step. */
static int wrap_exit_line(struct CPUState* cpu) {
    p3_unhide();
    uint32_t ra = cpu->gpr[31];
    uint32_t go = psx_mod_read_word(G_GAME_OVER);
    uint32_t a0 = cpu->gpr[4];
    if (g_mem && only_p3_alive() && !psx_mod_read_word(g_mem + M_SUB)) {
        uint16_t t = sub_enter(0);
        func_override_call_original(cpu);
        sub_leave(0, t);
    } else {
        func_override_call_original(cpu);
        /* Past the exit line: every standing player, P3 included. */
        if (cpu->gpr[2] != 0 && g_mem && p3_alive() && !psx_mod_read_word(g_mem + M_SUB)) {
            cpu->gpr[4] = a0;
            uint16_t t = sub_enter(0);
            func_override_call_original(cpu);
            sub_leave(0, t);
            cpu->gpr[2] = cpu->gpr[2] != 0;
        }
    }
    if (g_mem && psx_mod_read_word(g_mem + M_ACTIVE)) {
        hold_game_over(go);
        uint32_t v0 = cpu->gpr[2];
        p3_tally_step(cpu, ra);
        cpu->gpr[2] = v0;
        if (ra == RA_EXIT_LINE_PLAY) p3_pause_check(v0);
    }
    return 1;
}

/* ---- Next-level weapon gift and the Start pause ---------------------------
 *
 * Both are in the level loop FUN_80014a34 for p < players only.
 * Gift: when the last level was left over its exit line (DAT_80093d64 = 1,
 * not after level 0xD), the loop's setup gives each player a weapon after
 * the start positions: rand() for every player, kind DAT_8007a114[rand & 3]
 * (499, 504, 500, 501), and only a standing one (health > 0) gets it:
 * FUN_800167c4(kind, x, 1, z, 0) makes the object, held in +0x7D for kind
 * 504 (+0x7E = -1), else in +0x7E (+0x7D = -1). P3 gets the same at the
 * first loop top (FUN_8005f23c is the first call after that block), so in
 * the same frame, after P1 and P2.
 * Pause: after the exit-line test (0x800153D0) and only while the level is
 * played (frame > 0x20, no game-over count, no attract demo, no level end),
 * each player whose health is not 0 is read with FUN_8005f5ac(port): START
 * sets bit p of DAT_80093dd8, and a set byte runs the pause menu
 * FUN_8005dbbc (CONTINUE / QUIT; QUIT YES leaves the level). The menu reads
 * only its owner's port: P2's when bit 1 is set, else P1's. P3's START sets
 * bit 0 at the end of that exit-line test (the game's checks follow it in
 * the same frame); while M_PAUSE_P3 is set the menu's reads come from seat 3
 * (the netplay-synchronised input) and "PAUSED 1P" reads "PAUSED 3P". All of
 * it is per call: no host callback stays on the stack across the menu's
 * frames. The loop top after the menu (or the next level start) ends it.
 * The read redirect lives in Quick Start's wrap of FUN_8005f5ac (one
 * override per function): without Quick Start, P3's START does not pause. */
static uint32_t guest_call4(struct CPUState* cpu, uint32_t fn, uint32_t a0, uint32_t a1,
                            uint32_t a2, uint32_t a3, uint32_t stack0, uint32_t ra) {
    cpu->gpr[29] -= 0x18;                          /* own frame: argument 5 at sp + 0x10 */
    psx_mod_write_word(cpu->gpr[29] + 0x10, stack0);
    cpu->gpr[7] = a3;
    uint32_t v0 = guest_call(cpu, fn, a0, a1, a2, ra);
    cpu->gpr[29] += 0x18;
    return v0;
}

static void p3_gift(struct CPUState* cpu, uint32_t ra) {
    uint32_t saved[32];
    memcpy(saved, cpu->gpr, sizeof(saved));
    uint32_t r = guest_call(cpu, FN_RAND, 0, 0, 0, ra);
    int16_t kind = (int16_t)psx_mod_read_half(G_GIFT_TABLE + (r & 3u) * 2u);
    if ((int16_t)psx_mod_read_half(rec(P3_REC, RO_HEALTH)) > 0) {
        uint32_t obj = guest_call4(cpu, FN_OBJ_NEW, (uint32_t)(int32_t)kind,
                                   psx_mod_read_word(rec(P3_REC, RO_X)), 1,
                                   psx_mod_read_word(rec(P3_REC, RO_Z)), 0, ra);
        if ((uint16_t)kind == WEAPON_B_KIND) {
            psx_mod_write_byte(rec(P3_REC, RO_HAND_B), (uint8_t)obj);
            psx_mod_write_byte(rec(P3_REC, RO_HAND), 0xFFu);
        } else {
            psx_mod_write_byte(rec(P3_REC, RO_HAND_B), 0xFFu);
            psx_mod_write_byte(rec(P3_REC, RO_HAND), (uint8_t)obj);
        }
        psx_mod_write_word(g_mem + M_GIFTS, psx_mod_read_word(g_mem + M_GIFTS) + 1);
        psx_mod_counter_add("ff.p3.gift", 1);
    }
    memcpy(cpu->gpr, saved, sizeof(saved));
}

/* Put the game's "PAUSED 1P" pointer back when P3's pause is over. */
static void p3_pause_end(void) {
    if (!g_mem || !psx_mod_read_word(g_mem + M_PAUSE_P3)) return;
    psx_mod_write_word(G_PAUSED_1P_TEXT, psx_mod_read_word(g_mem + M_PAUSE_TEXT_SAVE));
    psx_mod_write_word(g_mem + M_PAUSE_P3, 0);
}

static void p3_loop_top(struct CPUState* cpu, uint32_t ra) {
    p3_pause_end();
    if (psx_mod_read_word(g_mem + M_GIFT)) {
        psx_mod_write_word(g_mem + M_GIFT, 0);
        if (p3_present()) p3_gift(cpu, ra);
    }
}

/* "PAUSED 3P" from the game's own "PAUSED 2P" (its language), the first
 * '2' made a '3'. */
static void p3_pause_text(void) {
    uint32_t src = psx_mod_read_word(G_PAUSED_2P_TEXT), dst = g_mem + M_PAUSE_TEXT;
    int changed = 0;
    uint32_t k = 0;
    if (src >= 0x80000000u && src < 0x80200000u) {
        for (; k < 31; k++) {
            uint8_t ch = psx_mod_read_byte(src + k);
            if (!ch) break;
            if (ch == '2' && !changed) { ch = '3'; changed = 1; }
            psx_mod_write_byte(dst + k, ch);
        }
    }
    psx_mod_write_byte(dst + k, 0);
    if (!changed) put_cstr(dst, "PAUSED 3P");
}

int ff_qs_pad_hook(void); /* quick_start.c: 1 while its FUN_8005f5ac wrap is armed */

static void p3_pause_check(uint32_t exit_v0) {
    if (exit_v0 != 0 || psx_mod_read_word(G_LEVEL_END) != 0) return; /* the level ends this frame */
    if ((int32_t)psx_mod_read_word(G_FRAME) <= 0x20 || psx_mod_read_word(G_GAME_OVER) != 0 ||
        (int32_t)psx_mod_read_word(G_ATTRACT) != -1)
        return;
    if (!p3_present() || psx_mod_read_word(g_mem + M_SUB) ||
        (int16_t)psx_mod_read_half(rec(P3_REC, RO_HEALTH)) == 0)
        return;
    uint16_t raw;
    if (!p3_read_seat(&raw) || ((uint16_t)~raw & 0x8u) == 0) return;
    if (!ff_qs_pad_hook()) {
        /* Without Quick Start nothing would let seat 3 drive the menu (it
         * would read P1's pad): P3's START does not pause then. */
        psx_mod_counter_add("ff.p3.pause_no_hook", 1);
        return;
    }
    psx_mod_write_byte(G_PAUSE, (uint8_t)(psx_mod_read_byte(G_PAUSE) | 1u));
    if (!psx_mod_read_word(g_mem + M_PAUSE_P3)) {
        p3_pause_text();
        psx_mod_write_word(g_mem + M_PAUSE_TEXT_SAVE, psx_mod_read_word(G_PAUSED_1P_TEXT));
        psx_mod_write_word(G_PAUSED_1P_TEXT, g_mem + M_PAUSE_TEXT);
        psx_mod_write_word(g_mem + M_PAUSE_P3, 1);
        psx_mod_write_word(g_mem + M_PAUSES, psx_mod_read_word(g_mem + M_PAUSES) + 1);
        psx_mod_counter_add("ff.p3.pause", 1);
    }
}

/* FUN_8005f5ac(port), called first by Quick Start's wrap of it (the
 * framework arms one override per function): inside the pause menu while
 * it is P3's, the owner's reads come from seat 3 (pad type 4, or 0xFF with
 * no device, as the original sets them) and the original does not run.
 * Returns 0 for every other call. */
int ff_p3_pad_read(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    if (!g_mem || ra < FN_PAUSE || ra >= FN_PAUSE_END || !psx_mod_read_word(g_mem + M_PAUSE_P3))
        return 0;
    p3_apply_requests(); /* test overrides (ff.p3 seat); the pad sample does not run here */
    uint16_t raw;
    int conn = p3_read_seat(&raw);
    uint16_t b = (uint16_t)~raw;
    psx_mod_write_byte(G_PAD_TYPE, conn ? 4u : 0xFFu);
    if (!conn || b == 0xFFFFu) b = 0;
    cpu->gpr[2] = (uint32_t)(int32_t)(int16_t)b; /* the original returns (int)(short) */
    return 1;
}

/* P3's ring: TARGETW.MOD put into a one-entry WAD in enhancement memory
 * and set up by the game's own object loader as model 0x379 (the front end
 * loads only the red and green rings). Done each time P3's cursor appears:
 * the loader converts the data in place, and a level reloads 0x379. */
static void be32(uint32_t a, uint32_t v) {
    psx_mod_write_byte(a, (uint8_t)(v >> 24));
    psx_mod_write_byte(a + 1, (uint8_t)(v >> 16));
    psx_mod_write_byte(a + 2, (uint8_t)(v >> 8));
    psx_mod_write_byte(a + 3, (uint8_t)v);
}

/* Ring colours (the models differ only in the colour of their 9 faces:
 * TARGET.MOD red, TARGETG.MOD green, TARGETW.MOD white). P3's is yellow,
 * on the select screen too: white is hard to see on light floors. */
#define RING_FACES        9u
#define RING_RGB_OFF      0x7Cu
#define RING_RGB_STRIDE   12u
static const uint8_t RING_RGB[3][3] = {{248, 0, 0}, {0, 248, 0}, {248, 216, 0}};

static int ring_src_ready(void);

static void ring_put(uint32_t wad, int colour) {
    be32(wad, 1);
    put_cstr(wad + 4, "TARGETW.MOD");
    be32(wad + 16, g_ring_size);
    be32(wad + 20, 24);
    for (uint32_t k = 0; k < g_ring_size; k++) psx_mod_write_byte(wad + 24 + k, g_ring_src[k]);
    if (colour >= 0 && g_ring_size >= RING_RGB_OFF + (RING_FACES - 1u) * RING_RGB_STRIDE + 3u)
        for (uint32_t f = 0; f < RING_FACES; f++)
            for (uint32_t ch = 0; ch < 3; ch++)
                psx_mod_write_byte(wad + 24 + RING_RGB_OFF + f * RING_RGB_STRIDE + ch, RING_RGB[colour][ch]);
}

static void p3_load_ring(struct CPUState* cpu, uint32_t ra) {
    if (!g_ring) return;
    if (!ring_src_ready()) return;
    ring_put(g_ring, 2);
    put_cstr(g_mem + M_NAMES, "TARGETW.MOD");
    uint32_t base = psx_mod_read_word(G_WAD_BASE), dir = psx_mod_read_word(G_WAD_DIR);
    uint32_t esz = psx_mod_read_word(G_WAD_ENTRY_SIZE);
    uint16_t bar = psx_mod_read_half(G_LOAD_BAR);
    psx_mod_write_word(G_WAD_BASE, g_ring);
    psx_mod_write_word(G_WAD_DIR, g_ring);
    psx_mod_write_half(G_LOAD_BAR, 0);
    guest_call(cpu, FN_LOAD_OBJ, g_mem + M_NAMES, RING_MODEL, 0, ra);
    psx_mod_write_word(G_WAD_BASE, base);
    psx_mod_write_word(G_WAD_DIR, dir);
    psx_mod_write_word(G_WAD_ENTRY_SIZE, esz);
    psx_mod_write_half(G_LOAD_BAR, bar);
}

/* TARGETW.MOD from a level model WAD, read once (no guest CD traffic). */
static int ring_src_ready(void) {
    if (!g_ring_size) {
        uint32_t size = 0;
        if (!psx_mod_read_disc_file("MODFILES/MODELS11.WAD", g_wad_buf, sizeof(g_wad_buf), &size) ||
            size < 4)
            return 0;
        uint32_t n = ((uint32_t)g_wad_buf[0] << 24) | ((uint32_t)g_wad_buf[1] << 16) |
                     ((uint32_t)g_wad_buf[2] << 8) | g_wad_buf[3];
        for (uint32_t k = 0; k < n && 4 + k * 20 + 20 <= size; k++) {
            const uint8_t* e = g_wad_buf + 4 + k * 20;
            if (memcmp(e, "TARGETW.MOD", 12) != 0) continue;
            uint32_t len = ((uint32_t)e[12] << 24) | ((uint32_t)e[13] << 16) | ((uint32_t)e[14] << 8) | e[15];
            uint32_t off = ((uint32_t)e[16] << 24) | ((uint32_t)e[17] << 16) | ((uint32_t)e[18] << 8) | e[19];
            if (len == 0 || len > sizeof(g_ring_src) || off > size || len > size - off) return 0;
            memcpy(g_ring_src, g_wad_buf + off, len);
            g_ring_size = len;
        }
        if (!g_ring_size) return 0;
    }
    return 1;
}

/* The ring under line-up record `slot`, drawn as FUN_80058e24 draws P1's. */
static void p3_draw_ring(struct CPUState* cpu, uint32_t slot, uint32_t ra) {
    uint32_t model = psx_mod_read_word(G_MODELS + 4u * RING_MODEL);
    if (model < g_ring || model >= g_ring + 0x140) return; /* not our ring */
    int32_t spin = (int32_t)(psx_mod_read_word(G_RING_SPIN) << 21) >> 16;
    guest_call(cpu, FN_MTX_PUSH, 0, 0, 0, ra);
    guest_call(cpu, FN_MTX_TRANSLATE, (uint32_t)(pos_x(slot) - (int32_t)psx_mod_read_word(G_CAM_X)),
               (uint32_t)(-(int32_t)psx_mod_read_word(G_CAM_Y)),
               (uint32_t)(pos_z(slot) - (int32_t)psx_mod_read_word(G_CAM_Z)), ra);
    guest_call(cpu, FN_MTX_ROT_X, 0x8000, 0, 0, ra);
    guest_call(cpu, FN_MTX_ROT_Y, (uint32_t)spin, 0, 0, ra);
    guest_call(cpu, FN_DRAW_MODEL, model, 0, 0, ra);
    guest_call(cpu, FN_MTX_POP, 0, 0, 0, ra);
}

/* ---- Fighter select -------------------------------------------------------
 * The select state FUN_80050798 runs P1's / P2's cursors (left / right to a
 * slot no other cursor holds, X confirms) and leaves the front end when both
 * have confirmed; then record p's type = line-up[cursor p]. P3 gets a third
 * cursor here, driven by seat 3 (the netplay-synced seat input): left /
 * right, X to confirm, triangle to change its mind. P1 / P2 skip P3's slot,
 * the front end waits for P3, and P3's fighter is loaded at level start
 * (p3_pick_fighter). Only in 2-player games with a device on seat 3. */
static int sel_slot_taken(uint32_t slot) {
    return psx_mod_read_word(G_CURSORS) == slot || psx_mod_read_word(G_CURSORS + 4) == slot;
}

/* ---- Straight to fighter select (Quick Start option) ---------------------
 * The front end FUN_8004fbcc runs the title, then the main menu state
 * FUN_80050270 ("1 PLAYER GAME / 2 PLAYER GAME / OPTIONS"), whose choice sets
 * the players (DAT_80092784 1 / 2, DAT_80093d5c 0 / 1), switches the state to
 * the select FUN_80050798 and moves the camera (FUN_8004fa10). With the
 * option, the menu's first frame of each front end makes that choice itself:
 * online (every peer the same) 2 players and P3 as before; offline 1 player,
 * and P2 / P3 join on the select screen with X (drop-in) and leave with
 * TRIANGLE before they confirm. TRIANGLE of P1 still goes back to the menu,
 * which then works as on the PS1 (options, memory card). */
int ff_qs_straight_to_select(void); /* quick_start.c */

/* Out of a level: P3 is no longer in record 2. M_ACTIVE was cleared only at
 * the next level's spawn, so p3_present() stayed true in the cutscenes
 * (their actor loop FUN_8001d640 -> FUN_8001aef4 met the 3-player leash and
 * the area-line swap) and on the front end (the line-up FUN_80050cc8 moves
 * records 0..3 through FUN_8001aef4; record 2 is a line-up fighter there).
 * P3's own values (score, health carry, choice) stay. */
static void p3_leave_level(void) {
    p3_pause_end(); /* QUIT from P3's pause menu leaves the level without a loop top */
    psx_mod_write_word(g_mem + M_GIFT, 0);
    psx_mod_write_word(g_mem + M_ACTIVE, 0);
    psx_mod_write_word(g_mem + M_SEL_ACTIVE, 0);
    atomic_store(&g_dbg_active, 0); /* "show" mirror (else updated only by the level's pad sample) */
}

/* Front-end entry: declined (the original runs at once), only marks it. */
static int wrap_front_end(struct CPUState* cpu) {
    (void)cpu;
    if (g_mem) {
        p3_leave_level();
        psx_mod_write_word(g_mem + M_FE_AUTO, 1);
        psx_mod_write_word(g_mem + M_DROPIN, 0);
        psx_mod_write_word(g_mem + M_P3_JOINED, 0);
    }
    return 0;
}

/* Cutscene entry (main() plays them only after a level loop returned):
 * declined, only clears P3's in-level state. */
static int wrap_cutscene(struct CPUState* cpu) {
    (void)cpu;
    if (g_mem) p3_leave_level();
    return 0;
}

static int wrap_main_menu(struct CPUState* cpu) {
    if (g_mem) psx_mod_write_word(g_mem + M_SEL_ACTIVE, 0);
    if (g_mem && psx_mod_read_word(g_mem + M_FE_AUTO)) {
        psx_mod_write_word(g_mem + M_FE_AUTO, 0);
        int32_t pads = (int32_t)psx_mod_read_word(G_PADS);
        if (ff_qs_straight_to_select() && pads >= 1) {
            uint32_t two = (psx_mod_netplay_is_active() && pads >= 2) ? 1u : 0u;
            psx_mod_write_word(G_PLAYERS, (uint32_t)pads);
            psx_mod_write_word(G_SEL_PLAYERS, two ? 2u : 1u);
            psx_mod_write_word(G_TWO_P_MODE, two);
            psx_mod_write_word(G_SEL_MASK, 0);
            psx_mod_write_word(g_mem + M_DROPIN, two ? 0u : 1u);
            psx_mod_write_word(g_mem + M_P2_JOIN_PREV, 0xFFFFu); /* no edge on the first frame */
            psx_mod_write_word(g_mem + M_P3_JOIN_PREV, 0xFFFFu);
            psx_mod_write_word(G_MENU_STATE, FN_SELECT);
            guest_call(cpu, FN_SEL_CAMERA, 0, 0, 0, cpu->gpr[31]);
            psx_mod_counter_add("ff.p3.straight_to_select", 1);
            return 1;
        }
    }
    /* The menu itself: the players choose as on the PS1. */
    if (g_mem) psx_mod_write_word(g_mem + M_DROPIN, 0);
    func_override_call_original(cpu);
    return 1;
}

/* A player's menu buttons (active-high) as FUN_8005f5ac(port) returns them,
 * read from the pad driver's buffer without calling it: that function also
 * counts per port (the Select+Start soft-reset hold DAT_80093a18[port], the
 * no-pad retries DAT_80093a1c / DAT_80093a20) and sets DAT_80094114, and the
 * select state calls it for P2 itself, so a second call made P2's soft reset
 * fire after ~16 frames instead of 31. Buffer (Ghidra / disassembly of
 * 0x8005F5B4..0x8005F5C8): port 0 at 0x80094748, other ports 0x80094770;
 * byte 0 = 0xFF without a pad, byte 1 >> 4 = pad type (buttons only for
 * types 4, 5, 7), halfword 2 = buttons active-low (0x0000 reads as none). */
#define G_PAD_BUF0        0x80094748u
#define G_PAD_BUF1        0x80094770u
static uint32_t menu_pad_peek(uint32_t port) {
    uint32_t buf = port ? G_PAD_BUF1 : G_PAD_BUF0;
    if (psx_mod_read_byte(buf) == 0xFFu) return 0;
    uint32_t type = (uint32_t)psx_mod_read_byte(buf + 1) >> 4;
    if (type != 4 && type != 5 && type != 7) return 0;
    uint16_t b = (uint16_t)~psx_mod_read_half(buf + 2);
    return b == 0xFFFFu ? 0u : b;
}

/* Drop-in on the select screen, before the game's select state runs: P2
 * joins with X while P1 has not confirmed, and leaves with TRIANGLE before
 * confirming. The game's own edge for P2 (DAT_80092790[1]) is set to the
 * buttons read here, so the select does not take the same press again. */
static void dropin_p2(struct CPUState* cpu, uint32_t ra) {
    if ((int32_t)psx_mod_read_word(G_PADS) < 2) return;
    uint32_t n = psx_mod_read_word(G_SEL_PLAYERS), mask = psx_mod_read_word(G_SEL_MASK);
    uint32_t now = menu_pad_peek(psx_mod_read_word(G_PAD_PORT + 4u));
    uint32_t edge = now & ~psx_mod_read_word(g_mem + M_P2_JOIN_PREV);
    psx_mod_write_word(g_mem + M_P2_JOIN_PREV, now);
    if (n == 1 && mask == 0 && (edge & 0x4000u)) {
        psx_mod_write_word(G_SEL_PLAYERS, 2);
        psx_mod_write_word(G_TWO_P_MODE, 1);
        psx_mod_write_word(G_MENU_PREV + 4u, now);
        guest_call(cpu, FN_SND_CONFIRM, 0, 0, 0, ra);
        guest_call(cpu, FN_SEL_CAMERA, 0, 0, 0, ra);
        psx_mod_counter_add("ff.p3.p2_joined", 1);
    } else if (n == 2 && !(mask & 2u) && (edge & 0x1000u)) {
        psx_mod_write_word(G_SEL_PLAYERS, 1);
        psx_mod_write_word(G_TWO_P_MODE, 0);
        psx_mod_write_word(G_MENU_PREV + 4u, now);
        psx_mod_write_word(G_SEL_MASK, mask & 1u);
        guest_call(cpu, FN_SND_MOVE, 0, 0, 0, ra);
        guest_call(cpu, FN_SEL_CAMERA, 0, 0, 0, ra);
        psx_mod_counter_add("ff.p3.p2_left", 1);
    }
}

static int wrap_slot_free(struct CPUState* cpu) {
    if (g_mem && psx_mod_read_word(g_mem + M_SEL_ACTIVE) &&
        cpu->gpr[4] == psx_mod_read_word(g_mem + M_SEL_CURSOR)) {
        cpu->gpr[2] = 0;
        return 1;
    }
    func_override_call_original(cpu);
    return 1;
}

/* Light P3's fighter like the game lights P1's / P2's: pulsing until
 * confirmed, then bright. FUN_80058e24 moves an unselected fighter's
 * brightness one step toward 0 before drawing it, hence the +-1. */
static int wrap_lineup(struct CPUState* cpu) {
    if (g_mem && psx_mod_read_word(g_mem + M_SEL_ACTIVE)) {
        uint32_t slot = psx_mod_read_word(g_mem + M_SEL_CURSOR);
        int v;
        if (psx_mod_read_word(g_mem + M_SEL_CONFIRMED)) {
            v = 0x21;
        } else {
            uint32_t ph = psx_mod_read_word(G_LINEUP_PHASE) & 0x7FFu;
            v = (int16_t)psx_mod_read_half(G_SINE + 2u * ph) >> 10;
            v += (v > 0) ? 1 : (v < 0 ? -1 : 0);
        }
        for (uint32_t k = 0; k < 3; k++) psx_mod_write_byte(rec(slot, RO_LIGHT + k), (uint8_t)(int8_t)v);
        uint32_t ra = cpu->gpr[31];
        func_override_call_original(cpu);
        uint32_t saved[32];
        memcpy(saved, cpu->gpr, sizeof(saved));
        p3_draw_ring(cpu, slot, ra);
        memcpy(cpu->gpr, saved, sizeof(saved));
        return 1;
    }
    func_override_call_original(cpu);
    return 1;
}

static int sel_step(uint32_t from, int dir) {
    for (int s = (int)from + dir; s >= 0 && s <= 3; s += dir)
        if (!sel_slot_taken((uint32_t)s)) return s;
    return -1;
}

/* Drop-in: who may join, blinking under the fighters' names in the game's
 * own centred text (the line P3's "AND <NAME>" uses once P3 is in). */
static void dropin_hint(struct CPUState* cpu, uint32_t ra, int p3_conn) {
    if (psx_mod_read_word(G_MENU_STATE) != FN_SELECT || psx_mod_read_word(G_SEL_DONE)) return;
    if (!(psx_mod_read_word(G_FRAME) & 0x10u)) return;
    const char* t = NULL;
    uint32_t n = psx_mod_read_word(G_SEL_PLAYERS);
    if (n == 1 && psx_mod_read_word(G_SEL_MASK) == 0 && (int32_t)psx_mod_read_word(G_PADS) >= 2)
        t = "PLAYER 2  PRESS X TO JOIN";
    else if (n == 2 && p3_conn && !psx_mod_read_word(g_mem + M_SEL_ACTIVE))
        t = "PLAYER 3  PRESS X TO JOIN";
    if (!t) return;
    uint32_t saved[32];
    memcpy(saved, cpu->gpr, sizeof(saved));
    put_cstr(g_mem + M_HINT_TEXT, t);
    guest_call(cpu, FN_TITLE_TEXT, g_mem + M_HINT_TEXT, 0x40, 0, ra);
    memcpy(cpu->gpr, saved, sizeof(saved));
}

static int wrap_select(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    if (!g_mem) {
        func_override_call_original(cpu);
        return 1;
    }
    p3_apply_requests();
    int dropin = (int)psx_mod_read_word(g_mem + M_DROPIN);
    if (dropin) dropin_p2(cpu, ra);
    uint16_t raw;
    int conn = p3_read_seat(&raw);
    /* P3's controller is gone (unplugged offline; a match of 3 seats forces
     * the session's pads connected, psxrecomp docs/NETPLAY.md): drop its
     * cursor, or the front end would wait for a confirm that cannot come
     * (only P1's TRIANGLE got out). P3 takes a cursor again when a device is
     * back (drop-in: with X). */
    if (!conn && psx_mod_read_word(g_mem + M_SEL_ACTIVE)) {
        psx_mod_write_word(g_mem + M_SEL_ACTIVE, 0);
        psx_mod_write_word(g_mem + M_SEL_CONFIRMED, 0);
        psx_mod_write_word(g_mem + M_CHOICE, 0xFFu);
        psx_mod_write_word(g_mem + M_P3_JOINED, 0);
        psx_mod_counter_add("ff.p3.sel_seat_lost", 1);
    }
    uint32_t p3_now = conn ? (uint16_t)~raw : 0u;
    /* Drop-in: P3 takes a cursor when it presses X (P2 must be in). */
    int p3_join = !dropin || (p3_now & ~psx_mod_read_word(g_mem + M_P3_JOIN_PREV) & 0x4000u);
    if (dropin) psx_mod_write_word(g_mem + M_P3_JOIN_PREV, p3_now);
    if (!psx_mod_read_word(g_mem + M_SEL_ACTIVE) && conn && p3_join &&
        psx_mod_read_word(G_SEL_PLAYERS) == 2) {
        int s0 = sel_step((uint32_t)-1, +1);
        if (s0 >= 0) {
            psx_mod_write_word(g_mem + M_SEL_CURSOR, (uint32_t)s0);
            psx_mod_write_word(g_mem + M_SEL_CONFIRMED, 0);
            psx_mod_write_word(g_mem + M_SEL_PREV, 0xFFFFu); /* no edge on the first frame */
            psx_mod_write_word(g_mem + M_CHOICE, 0xFFu);
            psx_mod_write_word(g_mem + M_SEL_ACTIVE, 1);
            uint32_t saved[32];
            memcpy(saved, cpu->gpr, sizeof(saved));
            p3_load_ring(cpu, ra);
            memcpy(cpu->gpr, saved, sizeof(saved));
        }
    }
    func_override_call_original(cpu);
    if (dropin) dropin_hint(cpu, ra, conn);
    if (!psx_mod_read_word(g_mem + M_SEL_ACTIVE)) return 1;
    if (psx_mod_read_word(G_SEL_PLAYERS) != 2) {
        psx_mod_write_word(g_mem + M_SEL_ACTIVE, 0);
        psx_mod_write_word(g_mem + M_CHOICE, 0xFFu);
        psx_mod_write_word(g_mem + M_P3_JOINED, 0);
        return 1;
    }
    uint32_t saved[32];
    memcpy(saved, cpu->gpr, sizeof(saved));
    uint32_t btn = conn ? (uint16_t)~raw : 0u;
    uint32_t edge = btn & ~psx_mod_read_word(g_mem + M_SEL_PREV);
    psx_mod_write_word(g_mem + M_SEL_PREV, btn);
    uint32_t cur = psx_mod_read_word(g_mem + M_SEL_CURSOR);
    int confirmed = (int)psx_mod_read_word(g_mem + M_SEL_CONFIRMED);
    if (!confirmed) {
        int dir = (edge & 0x80u) ? -1 : ((edge & 0x20u) ? +1 : 0); /* LEFT / RIGHT */
        if (dir) {
            int to = sel_step(cur, dir);
            if (to >= 0) {
                cur = (uint32_t)to;
                psx_mod_write_word(g_mem + M_SEL_CURSOR, cur);
                guest_call(cpu, FN_SND_MOVE, 0, 0, 0, ra);
            } else {
                guest_call(cpu, FN_SOUND, 5, 0xDF, 0x1F00, ra);
            }
        }
        if (edge & 0x4000u) { /* X */
            psx_mod_write_word(g_mem + M_SEL_CONFIRMED, 1);
            psx_mod_write_word(g_mem + M_CHOICE, psx_mod_read_word(G_LINEUP + 4u * cur));
            psx_mod_write_word(g_mem + M_P3_JOINED, 1);
            guest_call(cpu, FN_SND_CONFIRM, 0, 0, 0, ra);
            confirmed = 1;
        } else if (dropin && (edge & 0x1000u)) { /* drop-in: TRIANGLE before confirming = leave */
            psx_mod_write_word(g_mem + M_SEL_ACTIVE, 0);
            psx_mod_write_word(g_mem + M_CHOICE, 0xFFu);
            guest_call(cpu, FN_SND_MOVE, 0, 0, 0, ra);
            psx_mod_counter_add("ff.p3.p3_left", 1);
            memcpy(cpu->gpr, saved, sizeof(saved));
            return 1;
        }
    } else if (edge & 0x1000u) { /* triangle: choose again */
        psx_mod_write_word(g_mem + M_SEL_CONFIRMED, 0);
        psx_mod_write_word(g_mem + M_CHOICE, 0xFFu);
        psx_mod_write_word(g_mem + M_P3_JOINED, 0);
        confirmed = 0;
    }
    /* The front end leaves when P1 and P2 have confirmed: wait for P3. */
    if (!confirmed && psx_mod_read_word(G_SEL_DONE)) psx_mod_write_word(G_SEL_DONE, 0);
    /* "AND <NAME>" under the game's "<P1> AND <P2>" line, blinking until P3
     * confirms. The line-up records 0..3 carry the slots' fighters. */
    if (confirmed || (psx_mod_read_word(G_FRAME) & 8u)) {
        int32_t cur_type = rec_type(cur);
        uint32_t src = (cur_type >= 0 && cur_type <= 3)
                           ? psx_mod_read_word(CHAR_DATA + 4u * (uint32_t)cur_type) : 0u;
        put_cstr(g_mem + M_SEL_TEXT, "AND ");
        uint32_t k = 4;
        for (; k < 31; k++) {
            uint8_t ch = src ? psx_mod_read_byte(src + k - 4) : 0;
            psx_mod_write_byte(g_mem + M_SEL_TEXT + k, ch);
            if (!ch) break;
        }
        psx_mod_write_byte(g_mem + M_SEL_TEXT + 31, 0);
        guest_call(cpu, FN_TITLE_TEXT, g_mem + M_SEL_TEXT, 0x40, 0, ra);
    }
    memcpy(cpu->gpr, saved, sizeof(saved));
    return 1;
}

/* ---- HUD -----------------------------------------------------------------
 * FUN_800620bc draws P1's panel on the left (name + score at y 0x10, health
 * bar at y 0x1a, the last enemy it hit below) and P2's on the right; held
 * weapons' ammo at y 200 in the corners. P3's panel goes at the bottom
 * centre with the game's own text and bar routines (a bar is <= 96 px):
 * name + score at y 0xD8, bar at 0xE2, the last enemy it hit above. In the
 * top row a third bar between P1's and P2's read as one long bar. */
#define P3_HUD_TEXT_Y  0xD8u
#define P3_HUD_BAR_Y   0xE2u
#define P3_HUD_ENEMY_Y 0xAEu
#define P3_HUD_AMMO_Y  0xC8u   /* same row as P1's / P2's ammo (y 200) */
#define G_ITEMS        0x8009A3C8u /* objects, 0x24 each: +0 kind, +0x16 s16 ammo */
#define G_AMMO_LABEL   0x80093A60u /* "AMMO" */
static uint32_t guest_call(struct CPUState* cpu, uint32_t fn, uint32_t a0, uint32_t a1,
                           uint32_t a2, uint32_t ra) {
    cpu->gpr[4] = a0;
    cpu->gpr[5] = a1;
    cpu->gpr[6] = a2;
    func_override_guest_call(cpu, fn, ra);
    return cpu->gpr[2];
}

static void p3_continue(struct CPUState* cpu, uint32_t ra);
static void p3_draw_tally(struct CPUState* cpu, uint32_t ra);

static int wrap_hud(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    func_override_call_original(cpu);
    if (!p3_present()) return 1;
    uint32_t p3 = rec(P3_REC, 0);
    uint32_t saved[32];
    memcpy(saved, cpu->gpr, sizeof(saved));
    p3_draw_tally(cpu, ra);
    if (hp_of(P3_REC) < 1) {
        p3_continue(cpu, ra);
        memcpy(cpu->gpr, saved, sizeof(saved));
        return 1;
    }
    uint32_t name = psx_mod_read_word(rec(P3_REC, RO_CHAR));
    uint32_t score = g_mem + M_P3_SLOTS + SLOT_SCORE_TEXT;
    uint32_t w = guest_call(cpu, FN_TEXT_WIDTH, name, 0, 0, ra) +
                 guest_call(cpu, FN_TEXT_WIDTH, score, 0, 0, ra);
    uint32_t x = 0xA0u - w / 2u;
    x = guest_call(cpu, FN_TEXT, name, x, P3_HUD_TEXT_Y, ra);
    guest_call(cpu, FN_SCORE_TEXT, score, x, P3_HUD_TEXT_Y, ra);
    guest_call(cpu, FN_HEALTH_BAR, (P3_HUD_BAR_Y << 16) | 0x70u, p3, 0, ra);
    /* Ammo of a held gun, like P1's / P2's line (the item in hand +0x7E,
     * a second one +0x7D; same kind tests as FUN_800620bc). */
    for (uint32_t h = 0; h < 2; h++) {
        int8_t item = (int8_t)psx_mod_read_byte(rec(P3_REC, h ? 0x7Du : 0x7Eu));
        if (item < 0) continue;
        uint32_t kind = psx_mod_read_word(G_ITEMS + (uint32_t)item * 0x24u);
        int gun = h ? (kind == 0x203 || kind == 0x1F8)
                    : (kind == 0x203 || kind - 0x1F9u < 2 || kind - 499u < 5);
        if (!gun) continue;
        int16_t ammo = (int16_t)psx_mod_read_half(G_ITEMS + (uint32_t)item * 0x24u + 0x16u);
        char num[8];
        snprintf(num, sizeof(num), "%d", ammo < 0 ? 0 : (ammo > 99 ? 99 : ammo));
        put_cstr(g_mem + M_TALLY_TEXT + 32, num);
        uint32_t aw = guest_call(cpu, FN_TEXT_WIDTH, G_AMMO_LABEL, 0, 0, ra) +
                      guest_call(cpu, FN_TEXT_WIDTH, g_mem + M_TALLY_TEXT + 32, 0, 0, ra);
        uint32_t ax = guest_call(cpu, FN_TEXT, G_AMMO_LABEL, 0xA0u - aw / 2u, P3_HUD_AMMO_Y, ra);
        guest_call(cpu, FN_TEXT, g_mem + M_TALLY_TEXT + 32, ax, P3_HUD_AMMO_Y, ra);
        break;
    }
    /* The enemy P3 hit last (FUN_8001b850 sets +0x74 / timer +0x76). */
    uint8_t timer = psx_mod_read_byte(rec(P3_REC, RO_LAST_TARGET + 2));
    int8_t tgt = (int8_t)psx_mod_read_byte(rec(P3_REC, RO_LAST_TARGET));
    if (timer && tgt > (int8_t)P3_REC && tgt < 8) {
        if ((int16_t)psx_mod_read_half(rec((uint32_t)tgt, RO_HEALTH)) < 1 || rec_type((uint32_t)tgt) == -1) {
            psx_mod_write_byte(rec(P3_REC, RO_LAST_TARGET + 2), 0);
        } else {
            uint32_t en = psx_mod_read_word(rec((uint32_t)tgt, RO_CHAR));
            uint32_t ew = guest_call(cpu, FN_TEXT_WIDTH, en, 0, 0, ra);
            guest_call(cpu, FN_TEXT, en, 0xA0u - ew / 2u, P3_HUD_ENEMY_Y, ra);
            guest_call(cpu, FN_HEALTH_BAR, ((P3_HUD_ENEMY_Y + 10u) << 16) | 0x70u,
                       rec((uint32_t)tgt, 0), 0, ra);
        }
    }
    memcpy(cpu->gpr, saved, sizeof(saved));
    return 1;
}

/* P3's continue prompt, like FUN_80061e6c for P1/P2: name and score at P3's
 * panel, the blinking "]<continue text>", and on P3's X: a life, health
 * 0x17C, score 0, next extra life at 50,000, placed at a standing player. */
static void p3_continue(struct CPUState* cpu, uint32_t ra) {
    if (!psx_mod_read_byte(rec(P3_REC, RO_DOWN))) return;
    if (hp_of(0) < 1 && hp_of(1) < 1) return;
    if ((int32_t)psx_mod_read_word(G_LIVES) < 1) return;
    uint32_t name = psx_mod_read_word(rec(P3_REC, RO_CHAR));
    uint32_t score = g_mem + M_P3_SLOTS + SLOT_SCORE_TEXT;
    uint32_t w = guest_call(cpu, FN_TEXT_WIDTH, name, 0, 0, ra) +
                 guest_call(cpu, FN_TEXT_WIDTH, score, 0, 0, ra);
    uint32_t x = guest_call(cpu, FN_TEXT, name, 0xA0u - w / 2u, P3_HUD_TEXT_Y, ra);
    guest_call(cpu, FN_SCORE_TEXT, score, x, P3_HUD_TEXT_Y, ra);
    /* "]" + the game's continue text (the ']' glyph is the X button). */
    uint32_t src = psx_mod_read_word(G_CONTINUE_TEXT);
    psx_mod_write_byte(g_mem + M_PROMPT, ']');
    uint32_t n = 1;
    for (; n < 47; n++) {
        uint8_t ch = src ? psx_mod_read_byte(src + n - 1) : 0;
        psx_mod_write_byte(g_mem + M_PROMPT + n, ch);
        if (!ch) break;
    }
    psx_mod_write_byte(g_mem + M_PROMPT + 47, 0);
    if (psx_mod_read_word(G_FRAME) & 8u) {
        uint32_t pw = guest_call(cpu, FN_TEXT_WIDTH, g_mem + M_PROMPT, 0, 0, ra);
        guest_call(cpu, FN_TEXT, g_mem + M_PROMPT, 0xA0u - pw / 2u, P3_HUD_BAR_Y, ra);
    }
    uint32_t pad = psx_mod_read_word(g_mem + M_PAD_WORD);
    if ((pad >> 16) == 0xFFu || !(pad & 0x4000u)) return; /* X, active-high */
    psx_mod_write_word(G_LIVES, psx_mod_read_word(G_LIVES) - 1);
    psx_mod_write_half(rec(P3_REC, RO_HEALTH), RESPAWN_HEALTH);
    psx_mod_write_byte(rec(P3_REC, RO_DOWN), 0);
    psx_mod_write_word(g_mem + M_P3_SLOTS + SLOT_SCORE, 0);
    p3_format_score();
    psx_mod_write_word(g_mem + M_LIFE_AT, 50000);
    guest_call(cpu, FN_LIFE_SOUND, 0, 0, 0, ra);
    uint32_t other = hp_of(0) > 0 ? 0u : 1u;
    psx_mod_write_word(rec(P3_REC, RO_X), psx_mod_read_word(rec(other, RO_X)));
    psx_mod_write_word(rec(P3_REC, RO_Z), psx_mod_read_word(rec(other, RO_Z)));
    psx_mod_write_word(G_SEPARATE_COUNT, 0);
    guest_call(cpu, FN_SEPARATE, P3_REC, 0, 0, ra);
    psx_mod_write_word(g_mem + M_CONTINUES, psx_mod_read_word(g_mem + M_CONTINUES) + 1);
}

/* Level-end tally (level loop, players p < 2 only): while DAT_800941b8 >
 * 0x5A, every 4th frame one bonus unit (+0x8A) becomes 250 points and an
 * extra life comes at each 50,000. The same for P3, from the exit line
 * test, which the loop calls once more after counting up. */
/* Level end, like FUN_80014a34 does for P1/P2 when DAT_800941b8 becomes 1:
 * a standing player gets a quarter of its maximum health back (all of it
 * after levels 6, 0x0D, 0x14 and 0x15), at most maximum - 4, and keeps that
 * health into the next level. P3's record is rebuilt from P2's at the next
 * level start, so its value is kept in M_HP_CARRY. */
static void p3_level_end_health(void) {
    int32_t hp = hp_of(P3_REC);
    if (hp < 1) {
        psx_mod_write_word(g_mem + M_HP_CARRY, 0);
        psx_mod_write_word(g_mem + M_DOWN_CARRY, 1);
        return;
    }
    psx_mod_write_word(g_mem + M_DOWN_CARRY, 0);
    int32_t max = (int16_t)psx_mod_read_half(rec(P3_REC, RO_HEALTH_MAX));
    int32_t lvl = (int32_t)psx_mod_read_word(G_LEVEL_ID);
    int32_t n = hp + (max >> 2);
    if (lvl == 6 || lvl == 0xD || (uint32_t)(lvl - 0x14) < 2u) n = max;
    if (n > max - 4) n = max - 4;
    if (n < 1) n = hp;
    psx_mod_write_half(rec(P3_REC, RO_HEALTH), (uint16_t)n);
    psx_mod_write_word(g_mem + M_HP_CARRY, (uint32_t)n);
}

static void p3_tally_step(struct CPUState* cpu, uint32_t ra) {
    uint32_t b8 = psx_mod_read_word(G_LEVEL_END);
    if (b8 == 0) {
        psx_mod_write_word(g_mem + M_TALLY_INIT, 0);
        return;
    }
    if (!psx_mod_read_word(g_mem + M_TALLY_INIT)) {
        psx_mod_write_half(rec(P3_REC, RO_TALLY0), psx_mod_read_half(rec(P3_REC, RO_TALLY)));
        psx_mod_write_word(g_mem + M_TALLY_INIT, 1);
        p3_level_end_health();
    }
    /* After the refill, P3's health as it is: the game keeps P1's / P2's
     * actual health into the next level, so a continue (or a knock-out)
     * during the level-end sequence counts. */
    int32_t now = hp_of(P3_REC);
    psx_mod_write_word(g_mem + M_DOWN_CARRY, now < 1 ? 1u : 0u);
    psx_mod_write_word(g_mem + M_HP_CARRY, now < 1 ? 0u : (uint32_t)now);
    uint32_t frame = psx_mod_read_word(G_FRAME);
    if (psx_mod_read_word(g_mem + M_TALLY_FRAME) == frame) return; /* once a frame */
    psx_mod_write_word(g_mem + M_TALLY_FRAME, frame);
    uint32_t t = b8 - 1;
    if (t <= 0x5Au || (t & 3u) || hp_of(P3_REC) < 1) return;
    int16_t left = (int16_t)psx_mod_read_half(rec(P3_REC, RO_TALLY));
    uint32_t sc = psx_mod_read_word(g_mem + M_P3_SLOTS + SLOT_SCORE);
    if (left > 0) {
        psx_mod_write_half(rec(P3_REC, RO_TALLY), (uint16_t)(left - 1));
        sc += 250;
        psx_mod_write_word(g_mem + M_P3_SLOTS + SLOT_SCORE, sc);
        p3_format_score();
    }
    if (psx_mod_read_word(g_mem + M_LIFE_AT) < sc) {
        psx_mod_write_word(G_LIVES, psx_mod_read_word(G_LIVES) + 1);
        psx_mod_write_word(g_mem + M_LIFE_AT, sc + 50000);
        guest_call(cpu, FN_LIFE_SOUND, 0, 0, 0, ra);
        psx_mod_write_word(G_FLASH, 4);
    }
}

/* P3's line of the level-end tally, centred below P1's / P2's. */
static void p3_draw_tally(struct CPUState* cpu, uint32_t ra) {
    if (psx_mod_read_word(G_LEVEL_END) <= 0xFu || hp_of(P3_REC) < 1) return;
    char buf[48];
    int16_t left = (int16_t)psx_mod_read_half(rec(P3_REC, RO_TALLY));
    int16_t start = (int16_t)psx_mod_read_half(rec(P3_REC, RO_TALLY0));
    int len = snprintf(buf, sizeof(buf), "%d x 250 = %d", (int)left, (int)start * 250);
    if (len < 0) return;
    for (int k = 0; k <= len && k < 47; k++)
        psx_mod_write_byte(g_mem + M_TALLY_TEXT + (uint32_t)k, (uint8_t)buf[k]);
    uint32_t w = guest_call(cpu, FN_TEXT_WIDTH, g_mem + M_TALLY_TEXT, 0, 0, ra);
    guest_call(cpu, FN_TEXT, g_mem + M_TALLY_TEXT, 0xA0u - w / 2u, 0x8C, ra);
}

/* ---- New game and the high-score table ---------------------------------
 *
 * FUN_80014498 starts a new game (also a continue after game over): P1/P2
 * scores 0, next extra life 50,000. Do the same for P3, or its score would
 * carry into the next game.
 *
 * FUN_8006ab80(score P1, score P2) ends a game: for each top-ten score
 * FUN_8006aa70 inserts it (default name "???") and FUN_8006adf8 runs the name
 * entry (its own frame loop), then a loop shows the table until a player
 * presses triangle. P3 gets its entry inside that table loop, one frame per
 * call, so no host callback stays on the stack across frames (savestates and
 * netplay rollback need them unwound): while P3 enters its name, the table
 * draw FUN_8006a968 draws the game's entry screen FUN_8006a870 instead,
 * "CONGRATULATIONS P3!", and the table loop's buttons read nothing. The
 * controls are the game's: left / right choose a letter, X takes it, '#'
 * deletes, '<' ends; three letters, then '<'. */
#define FN_NEW_GAME       0x80014498u
#define FN_HISCORE        0x8006AB80u
#define FN_HS_TABLE       0x8006A968u
#define FN_HS_ENTRY_DRAW  0x8006A870u
#define FN_HS_RANK        0x8006AA70u
#define FN_MENU_INPUT     0x80068114u
#define FN_SND_MOVE       0x8004F468u /* cursor move */
#define FN_SND_SELECT     0x8004F490u /* select */
#define FN_SND            0x80056670u /* (id, volume, pitch) */
#define RA_HS_TABLE       0x8006AD3Cu /* FUN_8006a968 in the table loop */
#define RA_HS_INPUT_P1    0x8006AD44u /* FUN_80068114(0) there */
#define RA_HS_INPUT_P2    0x8006AD68u /* FUN_80068114(1) there */
#define G_HS_LABEL_P2     0x80092534u /* -> "CONGRATULATIONS P2!" */
#define G_HS_ROWS         0x80093E28u /* 10 rows of 12: u32 score, name[4], u32 flags */
#define G_HS_DEFAULT_NAME 0x80093C6Cu /* "???" */
#define G_HS_CHARSET      0x80093C70u /* '#' delete, '<' end, A-Z, 0-9, '!', '?' */
#define HS_CHARS          40
#define G_SCORE_P1        0x80094030u /* DAT_80094030[p] */
#define G_NEXT_LIFE_P1    0x800941B0u /* DAT_800941b0[p] */
enum { HS_IDLE = 0, HS_PENDING = 1, HS_ENTERING = 2, HS_DONE = 3 };

static int wrap_new_game(struct CPUState* cpu) {
    func_override_call_original(cpu);
    if (!g_mem) return 1;
    psx_mod_write_word(g_mem + M_P3_SLOTS + SLOT_SCORE, 0);
    psx_mod_write_word(g_mem + M_LIFE_AT, 50000);
    psx_mod_write_word(g_mem + M_HS_STATE, HS_IDLE);
    psx_mod_write_word(g_mem + M_HP_CARRY, 0);
    psx_mod_write_word(g_mem + M_DOWN_CARRY, 0);
    psx_mod_write_word(g_mem + M_GAME_P3, 0);
    psx_mod_write_word(g_mem + M_GIFT, 0);
    p3_pause_end();
    p3_format_score();
    return 1;
}

/* ---- Memory card -----------------------------------------------------------
 * FUN_80068850 saves a game at the game's checkpoints: it fills the save
 * block's game fields at 0x800E2F40 (+0x00..+0x1C; options +0x20..+0x29,
 * the high-score table +0x2C..+0xA3, 10 rows of 12) and writes the whole
 * 8 KB block 0x800E2D40; it reads it back and compares 0xA4 bytes from
 * +0x00. The block's bytes after that are written and read with it but used
 * by nobody: P3 goes to +0xA4 (0x800E2FE4) -- 'P', fighter type, health
 * (s16), score (u32). The stock game never reads them. A loaded save
 * reaches FUN_80067bac (options / high scores out of the block) right after
 * the read; P3's bytes are taken there and applied at P3's next spawn when
 * the game was started from the save (DAT_80094000 = 1). */
#define FN_CARD_SAVE      0x80068850u
#define FN_CARD_LOADED    0x80067BACu
#define G_SAVE_KIND       0x80093D3Cu /* 0 = game save at a checkpoint */
#define G_SAVE_P3         0x800E2FE4u
#define SAVE_P3_MAGIC     0x50u        /* 'P' */

static int wrap_card_save(struct CPUState* cpu) {
    (void)cpu;
    if (!g_mem) return 0;
    if (psx_mod_read_word(G_SAVE_KIND) == 0 && psx_mod_read_word(g_mem + M_GAME_P3)) {
        uint32_t hp = psx_mod_read_word(g_mem + M_HP_CARRY);
        if (hp == 0 || hp >= 0x8000u) hp = RESPAWN_HEALTH;
        /* Down at the level end: health 0, as the game saves P1's / P2's
         * real health (FUN_80068850); loaded, it starts the level down. */
        if (psx_mod_read_word(g_mem + M_DOWN_CARRY)) hp = 0;
        psx_mod_write_byte(G_SAVE_P3 + 0, SAVE_P3_MAGIC);
        psx_mod_write_byte(G_SAVE_P3 + 1, (uint8_t)(psx_mod_read_word(g_mem + M_FIGHTER) & 0xFFu));
        psx_mod_write_half(G_SAVE_P3 + 2, (uint16_t)hp);
        psx_mod_write_word(G_SAVE_P3 + 4, psx_mod_read_word(g_mem + M_P3_SLOTS + SLOT_SCORE));
        psx_mod_counter_add("ff.p3.save_written", 1);
    } else {
        psx_mod_write_word(G_SAVE_P3, 0);
        psx_mod_write_word(G_SAVE_P3 + 4, 0);
    }
    return 0; /* the game's own save runs */
}

static int wrap_card_loaded(struct CPUState* cpu) {
    func_override_call_original(cpu);
    if (!g_mem) return 1;
    uint8_t type = psx_mod_read_byte(G_SAVE_P3 + 1);
    int ok = psx_mod_read_byte(G_SAVE_P3) == SAVE_P3_MAGIC && type <= 3u;
    psx_mod_write_word(g_mem + M_SAVE_PENDING, ok ? 1u : 0u);
    if (ok) {
        psx_mod_write_word(g_mem + M_SAVE_TYPE, type);
        psx_mod_write_word(g_mem + M_SAVE_HP, psx_mod_read_half(G_SAVE_P3 + 2));
        psx_mod_write_word(g_mem + M_SAVE_SCORE, psx_mod_read_word(G_SAVE_P3 + 4));
    }
    return 1;
}

/* Runs once at the start of FUN_8006ab80 and lets the original run. */
static int wrap_hiscore(struct CPUState* cpu) {
    (void)cpu;
    if (!g_mem) return 0;
    uint32_t score = psx_mod_read_word(g_mem + M_P3_SLOTS + SLOT_SCORE);
    int took_part = psx_mod_read_word(g_mem + M_SPAWNS) != 0;
    psx_mod_write_word(g_mem + M_HS_STATE, (took_part && score) ? HS_PENDING : HS_IDLE);
    return 0;
}

/* P3's buttons (active-high) straight from its seat, as the select screen
 * reads them: the per-frame pad sample does not run on this screen. */
static uint32_t hs_buttons(void) {
    uint16_t raw;
    if (!p3_read_seat(&raw)) return 0;
    return (uint16_t)~raw;
}

/* One frame of P3's name entry, the steps of FUN_8006adf8. */
static void hs_entry_step(struct CPUState* cpu, uint32_t ra) {
    uint32_t score = psx_mod_read_word(g_mem + M_P3_SLOTS + SLOT_SCORE);
    int32_t cur = (int32_t)psx_mod_read_word(g_mem + M_HS_CUR);
    int32_t pos = (int32_t)psx_mod_read_word(g_mem + M_HS_POS);
    uint32_t name = g_mem + M_HS_NAME;
    psx_mod_write_byte(name + (uint32_t)pos, psx_mod_read_byte(G_HS_CHARSET + (uint32_t)cur));
    psx_mod_write_byte(name + (uint32_t)pos + 1u, 0);
    uint32_t label = psx_mod_read_word(G_HS_LABEL_P2);
    psx_mod_write_word(G_HS_LABEL_P2, g_mem + M_HS_LABEL);
    guest_call(cpu, FN_HS_ENTRY_DRAW, 2, score, name, ra);
    psx_mod_write_word(G_HS_LABEL_P2, label);

    uint32_t now = hs_buttons();
    uint32_t pressed = (now ^ psx_mod_read_word(g_mem + M_HS_PREV)) & now;
    psx_mod_write_word(g_mem + M_HS_PREV, now);
    if (pressed & 0x80u) { cur--; guest_call(cpu, FN_SND_MOVE, 0, 0, 0, ra); }
    if (pressed & 0x20u) { cur++; guest_call(cpu, FN_SND_MOVE, 0, 0, 0, ra); }
    if (pos < 3) {
        if (cur < 0) cur += HS_CHARS;
        else if (cur >= HS_CHARS) cur -= HS_CHARS;
    } else if (cur < 0) {
        cur += 2;
    } else if (cur > 1) {
        cur -= 2;
    }
    if (pressed & 0x4000u) {
        if (cur == 0) {                                   /* '#': delete */
            if (pos < 1) {
                guest_call(cpu, FN_SND, 5, 0xDF, 0x1F00, ra);
            } else {
                pos--;
                guest_call(cpu, FN_SND_SELECT, 0, 0, 0, ra);
            }
        } else if (cur == 1) {                            /* '<': done */
            guest_call(cpu, FN_SND_SELECT, 0, 0, 0, ra);
            uint32_t row = G_HS_ROWS + psx_mod_read_word(g_mem + M_HS_RANK) * 12u + 4u;
            if (pos == 0) {
                psx_mod_write_word(row, psx_mod_read_word(G_HS_DEFAULT_NAME));
            } else {
                for (int32_t k = 0; k < 4; k++)
                    psx_mod_write_byte(row + (uint32_t)k,
                                       k < pos ? psx_mod_read_byte(name + (uint32_t)k) : 0);
            }
            psx_mod_write_word(g_mem + M_HS_STATE, HS_DONE);
        } else {                                          /* a letter */
            pos++;
            if (pos == 3) cur = 1;
            guest_call(cpu, FN_SND_SELECT, 0, 0, 0, ra);
        }
    }
    psx_mod_write_word(g_mem + M_HS_CUR, (uint32_t)cur);
    psx_mod_write_word(g_mem + M_HS_POS, (uint32_t)pos);
    atomic_store(&g_dbg_hs[0], psx_mod_read_word(g_mem + M_HS_STATE));
    atomic_store(&g_dbg_hs[1], psx_mod_read_word(g_mem + M_HS_RANK));
    atomic_store(&g_dbg_hs[2], (uint32_t)cur);
    atomic_store(&g_dbg_hs[3], (uint32_t)pos);
}

static int wrap_hs_table(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    uint32_t state = g_mem ? psx_mod_read_word(g_mem + M_HS_STATE) : HS_IDLE;
    if (ra != RA_HS_TABLE || (state != HS_PENDING && state != HS_ENTERING)) {
        func_override_call_original(cpu);
        return 1;
    }
    p3_apply_requests(); /* test overrides (ff.p3 seat) */
    uint16_t seat_raw;
    if (!p3_read_seat(&seat_raw)) {
        /* P3's controller is gone (unplugged offline): P1 / P2 read nothing
         * while P3 enters a name, so waiting would lock the table. Keep the
         * default name for an entry already in the table and finish. */
        if (state == HS_ENTERING)
            psx_mod_write_word(G_HS_ROWS + psx_mod_read_word(g_mem + M_HS_RANK) * 12u + 4u,
                               psx_mod_read_word(G_HS_DEFAULT_NAME));
        psx_mod_write_word(g_mem + M_HS_STATE, HS_DONE);
        atomic_store(&g_dbg_hs[0], HS_DONE);
        psx_mod_counter_add("ff.p3.hs_no_seat", 1);
        func_override_call_original(cpu);
        return 1;
    }
    uint32_t saved[32];
    memcpy(saved, cpu->gpr, sizeof(saved));
    if (state == HS_PENDING) {
        uint32_t score = psx_mod_read_word(g_mem + M_P3_SLOTS + SLOT_SCORE);
        int32_t rank = (int32_t)guest_call(cpu, FN_HS_RANK, score, 0, 0, ra);
        if (rank < 0 || rank > 9) {
            psx_mod_write_word(g_mem + M_HS_STATE, HS_DONE);
            memcpy(cpu->gpr, saved, sizeof(saved));
            func_override_call_original(cpu);
            return 1;
        }
        psx_mod_write_word(g_mem + M_HS_RANK, (uint32_t)rank);
        psx_mod_write_word(g_mem + M_HS_CUR, 2);
        psx_mod_write_word(g_mem + M_HS_POS, 0);
        psx_mod_write_word(g_mem + M_HS_NAME, 0);
        psx_mod_write_word(g_mem + M_HS_NAME + 4, 0);
        psx_mod_write_word(g_mem + M_HS_PREV, hs_buttons()); /* a held button is not a press */
        put_cstr(g_mem + M_HS_LABEL, "CONGRATULATIONS P3!");
        psx_mod_write_word(g_mem + M_HS_STATE, HS_ENTERING);
    }
    hs_entry_step(cpu, ra);
    memcpy(cpu->gpr, saved, sizeof(saved));
    return 1;
}

/* While P3 enters its name the table loop's own buttons read nothing, so
 * nobody can leave the table before P3 is done. */
static int wrap_menu_input(struct CPUState* cpu) {
    uint32_t ra = cpu->gpr[31];
    if (g_mem && (ra == RA_HS_INPUT_P1 || ra == RA_HS_INPUT_P2) &&
        psx_mod_read_word(g_mem + M_HS_STATE) == HS_ENTERING) {
        cpu->gpr[2] = 0;
        return 1;
    }
    return 0;
}

/* Debug command "ff.p3": "show" | "seat <hex active-low word>" | "seat off"
 * | "hp <record 0..2> <health>" | "hand <object | -1>" | "life <score>"
 * (P3's next extra-life score) | "score <n>" | "hs" (high-score entry state)
 * (test only; local, so never in a match). */
static int cmd_p3(const char* args, char* reply, int cap) {
    if (args && strcmp(args, "hs") == 0)
        return snprintf(reply, (size_t)cap, "state=%u rank=%u cur=%u pos=%u",
                        (unsigned)atomic_load(&g_dbg_hs[0]), (unsigned)atomic_load(&g_dbg_hs[1]),
                        (unsigned)atomic_load(&g_dbg_hs[2]), (unsigned)atomic_load(&g_dbg_hs[3]));
    if (args && strncmp(args, "texmode ", 8) == 0) { /* debug only: see g_dbg_tex_mode */
        atomic_store(&g_dbg_tex_mode, atoi(args + 8));
        return snprintf(reply, (size_t)cap, "texmode=%d", atomic_load(&g_dbg_tex_mode));
    }
    if (args && strncmp(args, "place ", 6) == 0) { /* test only: "place <record 0..2> <x> <z>" */
        char* end = NULL;
        unsigned long r = strtoul(args + 6, &end, 10);
        long x = end ? strtol(end, &end, 10) : 0;
        long z = end ? strtol(end, NULL, 10) : 0;
        if (r > 2) return snprintf(reply, (size_t)cap, "bad record");
        atomic_store(&g_req_place_xz, ((uint32_t)(uint16_t)(int16_t)x << 16) | (uint16_t)(int16_t)z);
        atomic_store(&g_req_place, 0x80000000u | (uint32_t)r);
        return snprintf(reply, (size_t)cap, "ok");
    }
    if (args && strncmp(args, "fftrace ", 8) == 0) { /* debug only: log player hit tests */
        atomic_store(&g_fftrace, strcmp(args + 8, "on") == 0 ? 1u : 0u);
        return snprintf(reply, (size_t)cap, "fftrace %s", atomic_load(&g_fftrace) ? "on" : "off");
    }
    if (args && strncmp(args, "score ", 6) == 0) { /* test only: P3's score */
        unsigned long v = strtoul(args + 6, NULL, 10);
        atomic_store(&g_req_score, 0x80000000u | ((uint32_t)v & 0x7FFFFFFFu));
        return snprintf(reply, (size_t)cap, "ok");
    }
    if (args && strncmp(args, "life ", 5) == 0) {
        unsigned long v = strtoul(args + 5, NULL, 10);
        atomic_store(&g_req_life, 0x80000000u | ((uint32_t)v & 0x7FFFFFFFu));
        return snprintf(reply, (size_t)cap, "ok");
    }
    if (args && strncmp(args, "hand ", 5) == 0) { /* test only: HUD ammo line */
        long v = strtol(args + 5, NULL, 10);
        atomic_store(&g_req_hand, 0x80000000u | ((uint32_t)v & 0xFFu));
        return snprintf(reply, (size_t)cap, "ok");
    }
    if (args && strncmp(args, "hp ", 3) == 0) {
        char* end = NULL;
        unsigned long r = strtoul(args + 3, &end, 10);
        long v = end ? strtol(end, NULL, 10) : 0;
        if (r > 2) return snprintf(reply, (size_t)cap, "bad record");
        atomic_store(&g_req_hp, 0x80000000u | ((uint32_t)r << 16) | ((uint32_t)v & 0xFFFFu));
        return snprintf(reply, (size_t)cap, "ok");
    }
    if (args && strncmp(args, "seat ", 5) == 0) {
        const char* v = args + 5;
        if (strcmp(v, "off") == 0) {
            atomic_store(&g_req_test, 0x80000000u);
        } else {
            uint32_t w = (uint32_t)strtoul(v, NULL, 16) & 0xFFFFu;
            atomic_store(&g_req_test, 0x80000000u | 0x10000u | w);
        }
        return snprintf(reply, (size_t)cap, "ok");
    }
    return snprintf(reply, (size_t)cap,
                    "mem=0x%08X seat=%u conn=%u pad_word=0x%08X samples=%u active=%u "
                    "spawns=%u passes=%u p3_hits=%u retargets=%u ff_hits=%u obj_fix=%u "
                    "continues=%u gameover_held=%u fighter=%u loads=%u leash=%u weapon_views=%u weapon_p3=%u "
                    "held_waves=%u held_pod=%u held_dropoff=%u sel_active=%u dropin=%u p3_joined=%u max_seats=%d "
                    "pvp=%u pve=%u pblast=%u pshot=%u gifts=%u pauses=%u pause_p3=%u tex_loads=%u "
                    "tex_skips=%u tex_entries=%u tex_free=%u tex_appends=%u tex_room=%u tex_need=%u "
                    "tex_acc_wait=%u tex_fail=0x%08X tex_tiles=%u",
                    g_mem, (unsigned)P3_SEAT, (unsigned)atomic_load(&g_dbg_conn),
                    (unsigned)atomic_load(&g_dbg_pad_word),
                    (unsigned)atomic_load(&g_dbg_samples),
                    (unsigned)atomic_load(&g_dbg_active),
                    (unsigned)atomic_load(&g_dbg_spawns),
                    (unsigned)atomic_load(&g_dbg_passes),
                    (unsigned)atomic_load(&g_dbg_p3_hits),
                    (unsigned)atomic_load(&g_dbg_retargets),
                    (unsigned)atomic_load(&g_dbg_ff_hits),
                    (unsigned)atomic_load(&g_dbg_obj_fix),
                    (unsigned)atomic_load(&g_dbg_continues),
                    (unsigned)atomic_load(&g_dbg_go_held),
                    (unsigned)(atomic_load(&g_dbg_fighter) & 0xFFu),
                    (unsigned)(atomic_load(&g_dbg_fighter) >> 8),
                    (unsigned)atomic_load(&g_dbg_leash),
                    (unsigned)atomic_load(&g_dbg_weapon_views),
                    (unsigned)atomic_load(&g_dbg_weapon_p3),
                    (unsigned)atomic_load(&g_dbg_held[0]),
                    (unsigned)atomic_load(&g_dbg_held[1]),
                    (unsigned)atomic_load(&g_dbg_held[2]),
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_SEL_ACTIVE) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_DROPIN) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_P3_JOINED) : 0u,
#if defined(PSX_MAX_PLAYERS)
                    (int)PSX_MAX_PLAYERS
#else
                    -1
#endif
    ,
                    (unsigned)atomic_load(&g_dbg_pvp), (unsigned)atomic_load(&g_dbg_pve),
                    (unsigned)atomic_load(&g_dbg_pblast), (unsigned)atomic_load(&g_dbg_pshot),
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_GIFTS) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_PAUSES) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_PAUSE_P3) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_LOADS) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_SKIPS) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_ENTRIES) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_FREE) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_APPENDS) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_ROOM) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_NEED) : 0u,
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_ACC_TYPE) : 0u,
                    (unsigned)atomic_load(&g_dbg_tex_fail),
                    g_mem ? (unsigned)psx_mod_read_word(g_mem + M_TEX_TILES) : 0u);
}

/* ---- Netplay ---------------------------------------------------------------
 * The plugin changes the shared simulation, so a netplay match runs it on
 * every peer through this executable-owned profile (offline package choices
 * are not imported). All of its state is in mod memory, so rollback and
 * savestates carry it; the digest covers the fixed state block (P3's
 * per-player values, counters, select cursor, chosen fighter) so a peer
 * that drifts is caught early. Raise the compatibility id whenever the
 * plugin's simulation or state layout changes. */
static uint32_t p3_state_digest(void) {
    if (!g_mem) return 0;
    uint32_t h = 2166136261u; /* FNV-1a over 32-bit words */
    for (uint32_t o = 0; o < M_SIZE; o += 4) {
        if (o == M_TEST_WORD) continue; /* test override: host-local */
        h = (h ^ psx_mod_read_word(g_mem + o)) * 16777619u;
    }
    return h;
}

/* The title's netplay profile. Its loading slot carries the Quick Start
 * package (quick_start.c): each peer's own "Quick start" choice decides, and
 * the choice is part of the match identity, so peers must agree. /2: the
 * loading slot was added. /3: P3's high-score entry and new-game reset.
 * /4: spawns held while no enemy record is free (crash fix); hits on and by
 * P3 test players before enemies, as the stock hit test does.
 * /5: straight to fighter select (Quick Start option; online 2P with all).
 * /6: no player rings in a level any more (the select screen keeps its
 * rings); the draw and its guest memory changed, so /5 builds must not
 * meet these. (The unmerged framework-v0.3.7 branch also says /6: it must
 * take a new number when it is merged.)
 * /7: the plugin state after a rematch (one allocation, cleared every
 * session), session resets, P3's high-score entry without a seat, the
 * camera centroid in 64 bits.
 * /8: friendly fire off (Quick Start option): players' melee, thrown
 * objects, blasts and shots skip the other players.
 * /9: friendly fire on, gun shots between P3 and the others hurt; P3's
 * select cursor dropped when its controller goes; P3's in-level
 * state cleared at cutscenes and the front end; drop-in reads P2's menu
 * buttons without a second FUN_8005f5ac call.
 * /10: P3's down state at a level start as the game sets it:
 * not frozen after a downed P2, and still down (no free revive) after it
 * was down at the level end or in a saved game.
 * /11: P3 gets the next-level weapon gift and pauses with START (its own
 * "PAUSED 3P" menu, driven by seat 3), as P1 / P2 do.
 * /12: the level's texture load includes P3's fighter when neither P1 nor P2
 * plays it and it fits (VRAM and the texture tables change).
 * host_profiles: the host-profile catalog a newer framework requires before
 * any match (ff_host_profiles.c); absent on frameworks without that API. */
#if defined(PSX_MOD_NETPLAY_HOST_CATALOG_API) && PSX_MOD_NETPLAY_HOST_CATALOG_API >= 2
int ff_host_profiles(const PSXModNetplayHostStartup *in, PSXHostProfileCatalog *out);
#endif
static const PSXModNetplayProfile P3_NETPLAY_PROFILE = {
    .plugin_id = P3_PLUGIN_ID,
    .compatibility_id = "ff.third-player/13",
    .rollback_supported = 1,
    .savestates_supported = 1,
    .skip_bios_intro = 0,
    .fixed_aspect_mask = 0,
    .widescreen_plugin_id = NULL,
    .state_digest = p3_state_digest,
    .loading_package_id = "ff.quick-start",
    .loading_feature_id = "quick-start",
    .loading_plugin_id = "ff.quick-start",
#if defined(PSX_MOD_NETPLAY_HOST_CATALOG_API) && PSX_MOD_NETPLAY_HOST_CATALOG_API >= 2
    .host_profiles = ff_host_profiles,
#endif
};

PSX_MOD_CONSTRUCTOR(register_ff_third_player) {
    (void)psx_mod_register_netplay_profile(&P3_NETPLAY_PROFILE);
    (void)psx_mod_register_activation_plugin(P3_PLUGIN_ID, p3_activate);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PAD_SAMPLE, wrap_pad_sample,
                                             G_PAD_SAMPLE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_SPAWN_POS, wrap_spawn_pos,
                                             G_SPAWN_POS, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":textures", FN_TEX_LOAD, wrap_tex_load,
                                             G_TEX_LOAD, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":vram-reset", FN_VRAM_RESET, wrap_vram_reset,
                                             G_VRAM_RESET, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_ENEMY_COUNT, wrap_enemy_count,
                                             G_ENEMY_COUNT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_WAVES, wrap_waves,
                                             G_WAVES, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":drop-pod", FN_DROP_POD, wrap_drop_pod,
                                             G_DROP_POD, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":drop-off", FN_DROP_OFF, wrap_drop_off,
                                             G_DROP_OFF, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_EXIT_LINE, wrap_exit_line,
                                             G_EXIT_LINE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PLAYER_UPDATE, wrap_player_update,
                                             G_PLAYER_UPDATE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_NEAREST_PLAYER, wrap_nearest_player,
                                             G_NEAREST_PLAYER, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_WEIGHTED_TGT, wrap_weighted_target,
                                             G_WEIGHTED_TGT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_ENEMY_MELEE, wrap_count3,
                                             G_ENEMY_MELEE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_ENEMY_SPECIAL, wrap_count3,
                                             G_ENEMY_SPECIAL, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_ENEMY_WALK, wrap_hide_p3,
                                             G_ENEMY_WALK, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_CAM_TARGET, wrap_cam_target,
                                             G_CAM_TARGET, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_CAM_FOCUS, wrap_cam_focus,
                                             G_CAM_FOCUS, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_MOVE_APPLY, wrap_move_apply,
                                             G_MOVE_APPLY, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PAST_X_LO, wrap_past_line,
                                             G_PAST, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PAST_X_HI, wrap_past_line,
                                             G_PAST, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PAST_Z_LO, wrap_past_line,
                                             G_PAST, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PAST_Z_HI, wrap_past_line,
                                             G_PAST, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PLAYER_OUT, wrap_player_out,
                                             G_PLAYER_OUT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_CONTINUE, wrap_continue,
                                             G_CONTINUE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_ENEMY_HIT, wrap_enemy_hit,
                                             G_ENEMY_HIT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_ENEMY_LOOP, wrap_enemy_loop,
                                             G_ENEMY_LOOP, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_PLAYER_HIT, wrap_player_hit,
                                             G_PLAYER_HIT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_MELEE_HIT, wrap_player_hit,
                                             G_MELEE_HIT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_HUD, wrap_hud,
                                             G_HUD, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_SELECT, wrap_select,
                                             G_SELECT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":front-end", FN_FRONT_END, wrap_front_end,
                                             G_FRONT_END, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":cutscene", FN_CUTSCENE, wrap_cutscene,
                                             G_CUTSCENE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_MAIN_MENU, wrap_main_menu,
                                             G_MAIN_MENU, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_SLOT_FREE, wrap_slot_free,
                                             G_SLOT_FREE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID, FN_LINEUP, wrap_lineup,
                                             G_LINEUP_DRAW, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":new-game", FN_NEW_GAME, wrap_new_game,
                                             G_NEW_GAME, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":hiscore", FN_HISCORE, wrap_hiscore,
                                             G_HISCORE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":hs-table", FN_HS_TABLE, wrap_hs_table,
                                             G_HS_TABLE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":menu-input", FN_MENU_INPUT, wrap_menu_input,
                                             G_MENU_INPUT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":card-save", FN_CARD_SAVE, wrap_card_save,
                                             G_CARD_SAVE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":card-loaded", FN_CARD_LOADED, wrap_card_loaded,
                                             G_CARD_LOADED, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":damage-count", FN_DAMAGE, wrap_damage,
                                             G_DAMAGE, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":ff-shot", FN_SHOT_FIND, wrap_ff_shot_find,
                                             G_SHOT_FIND, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":ff-blast-a", FN_BLAST_HIT_A, wrap_ff_blast,
                                             G_BLAST_HIT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_function_override(P3_PLUGIN_ID ":ff-blast-b", FN_BLAST_HIT_B, wrap_ff_blast_b,
                                             G_BLAST_HIT, 4, FO_CREDIT_SELF);
    (void)psx_mod_register_session_reset_plugin(P3_PLUGIN_ID, p3_session_reset);
    (void)psx_mod_register_command("ff.p3", cmd_p3);
}
