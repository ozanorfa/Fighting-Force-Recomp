/* Fighting Force netplay host-profile catalog.
 *
 * From psxrecomp b34d101f (2026-11-21) on, the framework starts a match only
 * when the title's netplay profile names host profiles it is qualified on
 * (netplay_host_startup.hpp). This is that qualification metadata. It does
 * not change the game. The framework owns CPU detection, the running
 * executable's identity (every peer must run the same file), overlay
 * sealing, the exchange between peers and the selection before tick 0.
 *
 * Two rules, each with the SHA-256 of its contract file in netplay/ as its
 * qualification (computed by CMake), so a build with a changed contract
 * cannot meet an older one:
 *   ff-openbios-x64-v1             Windows x64: matches between players' PCs.
 *   ff-openbios-arm64-loopback-v1  macOS arm64, direct loopback only: the
 *                                  developer's match tests on one Mac.
 * Every other host gets no catalog, and the framework refuses netplay there.
 *
 * On an older framework (no PSX_MOD_NETPLAY_HOST_CATALOG_API) this file is
 * empty and nothing changes. */
#include "mod_netplay.h"

#if defined(PSX_MOD_NETPLAY_HOST_CATALOG_API) && PSX_MOD_NETPLAY_HOST_CATALOG_API >= 2

#include <string.h>

#include "ff_host_profiles_contract.h"

#define FF_OPENBIOS_WORDSUM 0xC4DAEB88u

int ff_host_profiles(const PSXModNetplayHostStartup *in, PSXHostProfileCatalog *out);

static int hex_to_bytes(const char *hex, uint8_t out[32]) {
    if (!hex || strlen(hex) != 64) return 0;
    for (unsigned i = 0; i < 32; ++i) {
        unsigned value = 0;
        for (unsigned j = 0; j < 2; ++j) {
            unsigned c = (unsigned char)hex[2 * i + j];
            if (c >= '0' && c <= '9') c -= '0';
            else if (c >= 'a' && c <= 'f') c = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') c = c - 'A' + 10;
            else return 0;
            value = (value << 4) | c;
        }
        out[i] = (uint8_t)value;
    }
    return 1;
}

/* The session FF's contracts describe: 2 or 3 seats, rollback or delay
 * sync, Quick Start loading on or off, no observer, native aspect, the
 * software authority with software or OpenGL presentation, built-in
 * OpenBIOS on the ENHANCED backend. */
static int session_ok(const PSXModNetplayHostStartup *in, uint32_t fp_contract) {
    uint32_t slot_bits;
    if (!in || in->fp_contract != fp_contract ||
        in->compiled_backends != PSX_HOST_BACKEND_ENHANCED ||
        in->available_backends != PSX_HOST_BACKEND_ENHANCED ||
        !in->interpreted_overlays_sealed || !in->builtin_linked_bios ||
        !in->actual_executable_sha256 || !in->builtin_bios_image_id ||
        strcmp(in->builtin_bios_image_id, "OPENBIOS") != 0 ||
        in->builtin_bios_image_wordsum != FF_OPENBIOS_WORDSUM)
        return 0;
    if (in->slot_count < 2 || in->slot_count > 3) return 0;
    if (in->player_count && (in->player_count < 2 || in->player_count > in->slot_count)) return 0;
    slot_bits = (1u << (unsigned)in->slot_count) - 1u;
    if (in->occupied_mask & ~slot_bits) return 0;
    if (in->observer || in->aspect_index) return 0;
    if (in->renderer != 0 && in->renderer != 1) return 0; /* 0 software, 1 OpenGL */
    return 1;
}

static int make_rule(PSXHostProfileRule *rule, const char *name, uint32_t flags,
                     uint32_t architecture, const char *qualification_hex) {
    memset(rule, 0, sizeof *rule);
    if (strlen(name) >= sizeof rule->profile) return 0;
    strcpy(rule->profile, name);
    rule->revision = 1;
    rule->priority = 1;
    rule->flags = flags;
    rule->implementation_count = 1;
    rule->implementations[0].backend = PSX_HOST_BACKEND_ENHANCED;
    rule->implementations[0].architectures = 1u << architecture;
    rule->implementations[0].required_features = 0;
    return hex_to_bytes(qualification_hex, rule->qualification);
}

int ff_host_profiles(const PSXModNetplayHostStartup *in, PSXHostProfileCatalog *out) {
    static PSXHostProfileRule rule;
    static int made;
    if (out) { out->rules = 0; out->count = 0; }
    if (!in || !out) return 0;
#if defined(_WIN32) && (defined(__x86_64__) || defined(_M_X64))
    if (in->cpu.architecture != PSX_HOST_ARCH_X64 || !session_ok(in, 2u)) return 0;
    if (!made) {
        if (!make_rule(&rule, "ff-openbios-x64-v1", PSX_HOST_PROFILE_PORTABLE,
                       PSX_HOST_ARCH_X64, FF_HOST_QUALIFICATION_X64_SHA256))
            return 0;
        made = 1;
    }
#elif defined(__APPLE__) && defined(__aarch64__)
    if (in->cpu.architecture != PSX_HOST_ARCH_ARM64 || !in->direct_loopback_scope ||
        !session_ok(in, 1u))
        return 0;
    if (!made) {
        if (!make_rule(&rule, "ff-openbios-arm64-loopback-v1",
                       PSX_HOST_PROFILE_PORTABLE | PSX_HOST_PROFILE_DIRECT_LOOPBACK,
                       PSX_HOST_ARCH_ARM64, FF_HOST_QUALIFICATION_ARM64_LOOPBACK_SHA256))
            return 0;
        made = 1;
    }
#else
    (void)rule;
    (void)made;
    return 0; /* No FF contract covers this host. */
#endif
    out->rules = &rule;
    out->count = 1;
    return 1;
}

#else
/* Older framework: no host-profile admission, nothing to declare. */
typedef int ff_host_profiles_unused;
#endif
