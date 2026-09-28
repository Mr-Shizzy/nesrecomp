# runner/cyc/cyc.cmake - sources for NESRecomp --cycle-accurate game projects.
#
#   set(NESRECOMP_ROOT <path to nesrecomp>)
#   include(${NESRECOMP_ROOT}/runner/cyc/cyc.cmake)
#   add_executable(MyGame ${NESRECOMP_CYC_SOURCES} generated/<prefix>_cyc.c)
#   target_include_directories(MyGame PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})
#   target_link_libraries(MyGame PRIVATE ${NESRECOMP_CYC_LIBRARIES})
#   nesrecomp_cyc_enable_sdl(MyGame)      # optional window and audio (runner/external/SDL2)
#   nesrecomp_cyc_enable_recomp_ui(MyGame) # optional launcher + in-game menu (after
#                                         # include(<recomp-ui>/recomp_ui.cmake) at file scope)
#   nesrecomp_cyc_add_oracle(cyc_oracle)  # optional TriCNES reference executable (needs CXX)
#
# NESRECOMP_DEV_UI (default OFF) builds the window's developer surface: the
# FDS drive bar and the dev keys (F1-F4, F6+ HLE toggles, coverage in the
# title). Production builds have neither; their HLE options are in the menu.
#
# The runtime is C11. Only the oracle and the recomp-ui glue are C++.

set(NESRECOMP_CYC_DIR ${CMAKE_CURRENT_LIST_DIR})

set(NESRECOMP_CYC_SOURCES
    ${NESRECOMP_CYC_DIR}/cpu6502.c
    ${NESRECOMP_CYC_DIR}/cpu6502_interp.c
    ${NESRECOMP_CYC_DIR}/hw_machine.c
    ${NESRECOMP_CYC_DIR}/hw_mapper.c
    ${NESRECOMP_CYC_DIR}/hw_fds.c
    ${NESRECOMP_CYC_DIR}/hw_fds_audio.c
    ${NESRECOMP_CYC_DIR}/hw_fds_hle.c
    ${NESRECOMP_CYC_DIR}/vendor/emu2413/emu2413.c
    ${NESRECOMP_CYC_DIR}/hw_ppu.c
    ${NESRECOMP_CYC_DIR}/hw_apu.c
    ${NESRECOMP_CYC_DIR}/hw_palette.c
    ${NESRECOMP_CYC_DIR}/cyc_trace.c
    ${NESRECOMP_CYC_DIR}/cyc_ring.c
    ${NESRECOMP_CYC_DIR}/cyc_run.c
    ${NESRECOMP_CYC_DIR}/cyc_ramview.c
    ${NESRECOMP_CYC_DIR}/cyc_host.c
    ${NESRECOMP_CYC_DIR}/cyc_accuracycoin.c
    ${NESRECOMP_CYC_DIR}/cyc_png.c
    ${NESRECOMP_CYC_DIR}/cyc_disk_action.c
    ${NESRECOMP_CYC_DIR}/cyc_overlay.c
)
set(NESRECOMP_CYC_INCLUDE_DIRS ${NESRECOMP_CYC_DIR})
option(NESRECOMP_DEV_UI "Window developer surface: FDS drive bar, dev keys (F1-F4, F6+), coverage title" OFF)
set(NESRECOMP_CYC_LIBRARIES "")
if(UNIX)
    # The C runtime's NTSC palette uses sin/cos. C++ oracle linking can hide
    # this dependency, but plain C executables need libm explicitly on Linux.
    list(APPEND NESRECOMP_CYC_LIBRARIES m)
endif()

# The TriCNES oracle: TriCNES's complete machine with the same host, writing
# the same --hash-out/--trace-out files as a recompiled build. Used only to
# check NESRecomp's implementation; it needs no generated code.
function(nesrecomp_cyc_add_oracle target)
    add_executable(${target}
        ${NESRECOMP_CYC_DIR}/tric_core.cpp
        ${NESRECOMP_CYC_DIR}/cyc_trace.c
        ${NESRECOMP_CYC_DIR}/cyc_host.c
        ${NESRECOMP_CYC_DIR}/cyc_accuracycoin.c
        ${NESRECOMP_CYC_DIR}/cyc_png.c
    )
    target_compile_definitions(${target} PRIVATE CYC_ORACLE)
    target_include_directories(${target} PRIVATE ${NESRECOMP_CYC_DIR})
    if(MSVC)
        # The ported core keeps C#'s implicit narrowing conversions.
        target_compile_options(${target} PRIVATE /W3 /wd4244 /wd4267 /wd4305 /wd4838 /wd4309 /bigobj)
        target_compile_definitions(${target} PRIVATE _CRT_SECURE_NO_WARNINGS)
    endif()
endfunction()

# Exhaustive check of the CPU operation helpers in cpu6502.h. Build it with
# the game's compiler and flags.
set(NESRECOMP_CYC_HELPER_TEST_SOURCES ${NESRECOMP_CYC_DIR}/cyc_helper_test.c)

# Adds the SDL2 window and audio (cyc_sdl.c), its input actions and bindings
# (cyc_input.c), settings (cyc_settings.c, config.ini) and TCP debug server
# (cyc_tcp.c) to a host executable if SDL2 is found. Without it the executable
# is headless only. DEV_UI / NO_DEV_UI override NESRECOMP_DEV_UI for this target. A game's own
# host extras (cyc_host_extras.h) are added beforehand with the target property
# NESRECOMP_CYC_HOST_EXTRAS set; otherwise cyc_host_extras_none.c is linked.
function(nesrecomp_cyc_enable_sdl target)
    cmake_parse_arguments(SDLOPT "DEV_UI;NO_DEV_UI" "" "" ${ARGN})
    list(APPEND CMAKE_PREFIX_PATH "${NESRECOMP_CYC_DIR}/../external/SDL2/cmake")
    find_package(SDL2 CONFIG QUIET)
    if(NOT SDL2_FOUND)
        message(STATUS "${target}: SDL2 not found, building headless only")
        return()
    endif()
    target_sources(${target} PRIVATE ${NESRECOMP_CYC_DIR}/cyc_sdl.c ${NESRECOMP_CYC_DIR}/cyc_input.c
        ${NESRECOMP_CYC_DIR}/cyc_settings.c ${NESRECOMP_CYC_DIR}/cyc_tcp.c)
    if(WIN32)
        target_link_libraries(${target} PRIVATE ws2_32)
    endif()
    get_target_property(extras ${target} NESRECOMP_CYC_HOST_EXTRAS)
    if(NOT extras)
        target_sources(${target} PRIVATE ${NESRECOMP_CYC_DIR}/cyc_host_extras_none.c)
    endif()
    target_compile_definitions(${target} PRIVATE CYC_WITH_SDL)
    if((NESRECOMP_DEV_UI OR SDLOPT_DEV_UI) AND NOT SDLOPT_NO_DEV_UI)
        target_compile_definitions(${target} PRIVATE CYC_DEV_UI)
    endif()
    set_property(TARGET ${target} PROPERTY NESRECOMP_CYC_SDL TRUE)
    target_link_libraries(${target} PRIVATE SDL2::SDL2)
    if(WIN32)
        add_custom_command(TARGET ${target} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different $<TARGET_FILE:SDL2::SDL2> $<TARGET_FILE_DIR:${target}>)
    endif()
endfunction()

# recomp-ui on a windowed host (cyc_ui.h): the pre-boot launcher and the
# in-game runtime menu. Needs nesrecomp_cyc_enable_sdl() first (it is skipped
# for a headless target) and recomp-ui's recomp_ui.cmake included at file
# scope (it enables C++ there; project.cmake does both for game projects).
#   BOXART file.tga       the launcher's box art
#   ROM_SHA256 hex        the image the launcher verifies (as it hashes: after
#                         an iNES header; a disk image whole)
function(nesrecomp_cyc_enable_recomp_ui target)
    cmake_parse_arguments(RUI "" "BOXART;ROM_SHA256" "" ${ARGN})
    get_target_property(sdl ${target} NESRECOMP_CYC_SDL)
    if(NOT sdl)
        return()
    endif()
    if(NOT COMMAND recomp_target_launcher_ui)
        message(FATAL_ERROR "nesrecomp_cyc_enable_recomp_ui(${target}): include recomp_ui.cmake first")
    endif()
    set(boxart "")
    if(RUI_BOXART)
        set(boxart BOXART "${RUI_BOXART}")
    endif()
    recomp_target_launcher_ui(${target} CONSOLE nes ${boxart})
    recomp_target_runtime_ui_sdlrenderer2(${target})
    target_sources(${target} PRIVATE ${NESRECOMP_CYC_DIR}/cyc_ui_menu.c ${NESRECOMP_CYC_DIR}/cyc_ui_launcher.c
        ${NESRECOMP_CYC_DIR}/cyc_ui_imgui.cpp)
    target_compile_definitions(${target} PRIVATE CYC_WITH_RECOMP_UI)
    if(RUI_ROM_SHA256)
        target_compile_definitions(${target} PRIVATE "CYC_LAUNCHER_ROM_SHA256=\"${RUI_ROM_SHA256}\"")
    endif()
endfunction()
