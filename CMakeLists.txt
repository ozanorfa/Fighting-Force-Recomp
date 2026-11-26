cmake_minimum_required(VERSION 3.20)
set(PSXRECOMP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/psxrecomp" CACHE PATH
    "Path to psxrecomp")
if(NOT EXISTS "${PSXRECOMP_ROOT}/runtime/runtime.cmake")
    message(FATAL_ERROR
        "PSXRecomp runtime is missing: ${PSXRECOMP_ROOT}/runtime/runtime.cmake\n"
        "This project expects the psxrecomp framework checkout at ${PSXRECOMP_ROOT}.\n"
        "Run: git submodule update --init --recursive\n"
        "If psxrecomp/ is not a submodule, set -DPSXRECOMP_ROOT=<path-to-psxrecomp>.")
endif()

project(FightingForceRecomp LANGUAGES C CXX)

# --- Opt-in product surfaces (OFF by default in the framework) -------------
# Scaffold prompts / flags fill these. Without recomp-ui, PSX_RECOMP_UI is OFF.
# The wizard (WIZARD_CMAKE_BLOCK / ENABLE_SETUP_WIZARD) is the first-run disc
# picker. This project never commits generated/ C: every build generates it
# from the builder's own disc (tools/ff_build.py, or the release packager).
# recomp-ui submodule present (PSX_RECOMP_UI defaults ON).
# Full netplay UI — enable after adding recomp-ui + testing:
# if(EXISTS "${PSXRECOMP_ROOT}/lib/recomp-net/CMakeLists.txt")
#     set(PSX_NETPLAY ON CACHE BOOL "…" FORCE)
# endif()
set(PSX_SETUP_WIZARD ON CACHE BOOL
    "Advertise first-run setup wizard + Generate & rebuild in recomp-ui" FORCE)
# Netplay (recomp-net, Phase 5): off by default so the other build folders
# stay as they are. Configure a separate folder with -DFF_NETPLAY=ON; add
# -DRNET_ENABLE_ICE=OFF for a LAN / Direct IP build without the libjuice
# download (online lobbies need ICE).
option(FF_NETPLAY "Build with recomp-net netplay" OFF)
set(FF_NETPLAY_ARG "")
if(FF_NETPLAY)
    if(NOT EXISTS "${PSXRECOMP_ROOT}/lib/recomp-net/CMakeLists.txt")
        message(FATAL_ERROR "FF_NETPLAY=ON needs ${PSXRECOMP_ROOT}/lib/recomp-net")
    endif()
    set(PSX_NETPLAY ON CACHE BOOL "Netplay (recomp-net)" FORCE)
    set(FF_NETPLAY_ARG ENABLE_NETPLAY_IF_PRESENT)
endif()
include("${PSXRECOMP_ROOT}/runtime/runtime.cmake")

# Controller seats compiled into the runtime. The shipped game is 2 players;
# a 3rd player arrives as a multitap seat, which needs >= 5 seats with the
# tap on port 2 (seat 0 = port 1, seats 1..4 = tap A..D). Configure a
# separate build directory with -DFF_MAX_PLAYERS=5 for 3-player work.
set(FF_MAX_PLAYERS 2 CACHE STRING "Controller seats compiled into the runtime (1-8)")

# A runtime with the multitap seats opens the 3-player config when started
# without --game (double-click, launcher): its launcher then offers three
# players, and its LAN rooms three seats (a room's seat limit is the config's
# `players`). The stock build keeps game.toml.
if(FF_MAX_PLAYERS GREATER_EQUAL 5)
    set(FF_DEFAULT_CONFIG "game_3p.toml")
else()
    set(FF_DEFAULT_CONFIG "game.toml")
endif()

# This project's own mod catalog. The FRAMEWORK stages it -- together with its
# builtin psx.* packages -- into <exe-dir>/mods/bundled, and refuses to
# configure if a catalog exists here but is not declared. Shared Internal
# Resolution is included even with a builtin allowlist, enabled at 4x on fresh
# profiles, with 1x..32x choices; do not supply a title-local copy of that id.
# Never copy the catalog by hand: five titles did, and framework 4cc04be3's rename of the staged
# directory broke all five silently (bead beads-eio.3.101).
set(PSX_PRELOADED_MODS "${CMAKE_CURRENT_SOURCE_DIR}/mods/preloaded")
if(NOT IS_DIRECTORY "${PSX_PRELOADED_MODS}")
    set(PSX_PRELOADED_MODS "NONE")
endif()

psxrecomp_add_game_runtime(psx-runtime
    PRELOADED_MODS_DIR "${PSX_PRELOADED_MODS}"
    ${FF_NETPLAY_ARG}
    # NETPLAY_LOBBY_URL "ws://netplay.retcomm.net:8765"
    ENABLE_SETUP_WIZARD
    WINDOW_TITLE "FightingForce Recompiled"
    DEFAULT_GAME_CONFIG_PATH "${FF_DEFAULT_CONFIG}"
    MAX_PLAYERS ${FF_MAX_PLAYERS}
    # LAUNCHER_BOXART "${CMAKE_CURRENT_SOURCE_DIR}/launcher_assets/img/boxart.tga"
    APP_ICON "${CMAKE_CURRENT_SOURCE_DIR}/assets/psxrecomp.ico"
    # Game C from psxrecomp_cli.py generate (never committed here).
    # GAME.EXE (the game proper, chain-loaded by the boot EXE) compiled from
    # the player's disc by aot/overlays.json during psxrecomp_cli.py generate.
    # Keep it above the multi-value GEN_FULL_GLOB / CODEGEN_SETUP_SOURCES,
    # which would otherwise swallow it as extra list items.
    GAME_OVERLAY_STATIC_C "${CMAKE_CURRENT_SOURCE_DIR}/generated/overlays_static.c"
    GEN_MARKER "generated/SLUS_004.33_dispatch.c"
    GEN_FULL_GLOB "generated/SLUS_004.33_full_*.c"
    CODEGEN_SETUP_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/codegen_setup.c"
)

# Trusted mod plugins (selected by the packages in mods/preloaded).
target_sources(psx-runtime PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src/mods/third_player.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/src/mods/quick_start.c")

# Netplay host-profile catalog (src/mods/ff_host_profiles.c). Newer frameworks
# start a match only on a host profile the title declares. Each rule's
# qualification is the SHA-256 of its contract in netplay/, so a build whose
# contract changed cannot meet an older one. On a framework without that API
# the source compiles to nothing.
set(FF_HOST_CONTRACT_X64 "${CMAKE_CURRENT_SOURCE_DIR}/netplay/ff-openbios-x64-v1.json")
set(FF_HOST_CONTRACT_ARM64 "${CMAKE_CURRENT_SOURCE_DIR}/netplay/ff-openbios-arm64-loopback-v1.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${FF_HOST_CONTRACT_X64}" "${FF_HOST_CONTRACT_ARM64}")
file(SHA256 "${FF_HOST_CONTRACT_X64}" FF_HOST_QUALIFICATION_X64_SHA256)
file(SHA256 "${FF_HOST_CONTRACT_ARM64}" FF_HOST_QUALIFICATION_ARM64_LOOPBACK_SHA256)
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/ff-netplay")
file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/ff-netplay/ff_host_profiles_contract.h"
    CONTENT "#define FF_HOST_QUALIFICATION_X64_SHA256 \"@FF_HOST_QUALIFICATION_X64_SHA256@\"\n#define FF_HOST_QUALIFICATION_ARM64_LOOPBACK_SHA256 \"@FF_HOST_QUALIFICATION_ARM64_LOOPBACK_SHA256@\"\n"
    @ONLY)
target_include_directories(psx-runtime PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/ff-netplay")
if(TARGET psx-runtime-pgxp) # optional PGXP sibling: it gets the source too
    target_include_directories(psx-runtime-pgxp PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/ff-netplay")
endif()
psxrecomp_add_implementation(psx-runtime
    NAME ff-host-profile CONTRACT ff-host-profiles-v1
    HLE_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/mods/ff_host_profiles.c"
    LLE_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/mods/ff_host_profiles.c"
    CONTRACT_FILES "${FF_HOST_CONTRACT_X64}" "${FF_HOST_CONTRACT_ARM64}")

# Stage runtime configs next to the exe for direct build/ launches. Only copy
# files that exist — never fail POST_BUILD when game_options.toml is absent.
foreach(_PSX_RUNTIME_CONFIG game.toml game_3p.toml game_options.toml)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${_PSX_RUNTIME_CONFIG}")
        add_custom_command(TARGET psx-runtime POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/${_PSX_RUNTIME_CONFIG}"
                "$<TARGET_FILE_DIR:psx-runtime>/${_PSX_RUNTIME_CONFIG}"
            COMMENT "Staging ${_PSX_RUNTIME_CONFIG}"
            VERBATIM)
    endif()
endforeach()
