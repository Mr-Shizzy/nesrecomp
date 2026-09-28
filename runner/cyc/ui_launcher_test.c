/* ui_launcher_test.c - the cycle host's side of recomp-ui's launcher
 * (cyc_ui_launcher.c) with the launcher itself replaced by a scripted one:
 * what the host hands the launcher (the NES profile in host-owned binding
 * mode, the FDS media names, the image's SHA-256, every binding and its
 * default), and that every edit a player makes there -- each controller
 * button's key and pad binding, each host shortcut's, the input sources,
 * the display and audio settings -- comes back into the host's settings and
 * survives a config.ini save and reload. Also: a launcher that closes keeps
 * the edits and quits, one that cannot open leaves everything as it was.
 * Needs recomp-ui's headers only (the launcher's ABI); no window. */
#include "cyc_input.h"
#include "cyc_settings.h"
#include "cyc_ui.h"
#include "launcher_profile.h"
#include "recomp_launcher.h"

#include <SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); exit(1); } } while (0)

/* cyc_recomp.h's program metadata, as a compiled FDS program has it */
const char *cyc_native_program_name = "TestGame";
const uint32_t cyc_native_fds_bios_crc32 = 0x5E607DCFu;

/* ---- the scripted launcher ---- */
static int script_result;
static RecompLauncherCGameInfo seen_game;
static RecompLauncherCSettings seen_io;
static char seen_initial[256];
static void (*script_edit)(RecompLauncherCSettings *io);
static const char *script_rom;

int recomp_launcher_run_window(const char *title, RecompLauncherCSettings *io, const RecompLauncherCGameInfo *game,
                               const char *assets, const char *initial_rom, char *out, size_t out_len)
{
    (void)title; (void)assets;
    seen_game = *game;
    seen_io = *io;
    snprintf(seen_initial, sizeof(seen_initial), "%s", initial_rom ? initial_rom : "");
    if (script_edit) script_edit(io);
    if (script_rom) snprintf(out, out_len, "%s", script_rom);
    return script_result;
}
void recomp_launcher_set_preserve_sdl(int preserve) { (void)preserve; }

/* The player's edits: every row of the Controls page and the settings. */
static void edit_everything(RecompLauncherCSettings *io)
{
    io->window_scale = 5;
    io->fullscreen = 1;
    io->integer_scale = 0;
    io->linear_filter = 1;
    io->enable_audio = 0;
    io->volume = 40;
    io->skip_launcher = 1;
    io->player_src[0] = 2;
    io->player_src[1] = 0;
    io->deadzone[0] = 12;
    snprintf(io->player_gamepad_guid[0], sizeof(io->player_gamepad_guid[0]), "030000005e0400008e02000000007801");
    for (int p = 0; p < 2; ++p)
        for (int b = 0; b < 8; ++b) {
            io->player_key_bind[p][b] = SDL_SCANCODE_A + p * 8 + b;
            io->player_pad_bind[p][b] = b == 6 ? RECOMP_LAUNCHER_PAD_AXIS(SDL_CONTROLLER_AXIS_TRIGGERLEFT, 1)
                                      : b == 7 ? RECOMP_LAUNCHER_PAD_BUTTON_COMBO((1 << 4) | (1 << 6))
                                               : RECOMP_LAUNCHER_PAD_BUTTON(b + p);
        }
    for (int i = 0; i < CYC_SC_COUNT; ++i) {
        io->assist_key_bind[i] = SDL_SCANCODE_F1 + i;
        io->assist_pad_bind[i] = RECOMP_LAUNCHER_PAD_BUTTON(SDL_CONTROLLER_BUTTON_Y + i);
    }
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : ".";
    char cfg[1024], image[1024];
    snprintf(cfg, sizeof(cfg), "%s/ui_launcher_test.ini", dir);
    snprintf(image, sizeof(image), "%s/game.fds", dir);
    remove(cfg);

    /* what the host hands over */
    CycSettings s;
    cyc_settings_default(&s);
    const char *rom = image;
    script_result = RECOMP_LAUNCHER_RESULT_QUIT;
    CHECK(cyc_ui_launcher(&s, cfg, NULL, &rom, true) == 1);
    CHECK(!strcmp(seen_initial, image));
    CHECK(seen_game.settings_bindings == 1);
    CHECK(!strcmp(seen_game.theme, "nes") && !strcmp(seen_game.platform, "FAMICOM DISK SYSTEM"));
    CHECK(!strcmp(seen_game.rom_noun, "Disk") && seen_game.num_rom_patterns == 2);
    CHECK(!strcmp(seen_game.rom_patterns[0], "*.fds") && !strcmp(seen_game.rom_patterns[1], "*.qd"));
    CHECK(!strcmp(seen_game.name, "TestGame"));
    CHECK(seen_game.assist_binding_count == CYC_SC_COUNT);            /* Disk offered: an FDS program */
    CHECK(!strcmp(seen_game.assist_binding_labels[0], cyc_shortcut_label(CYC_SC_DISK)));
    CHECK(seen_game.assist_default_key_bind[0] == SDL_SCANCODE_D);
    CHECK(seen_game.assist_default_pad_bind[0] == RECOMP_LAUNCHER_PAD_BUTTON(SDL_CONTROLLER_BUTTON_LEFTSHOULDER));
    CHECK(seen_game.default_settings && seen_game.default_settings->player_key_bind[0][4] == SDL_SCANCODE_Z);
    CHECK(seen_game.rom_cache_path && strstr(seen_game.rom_cache_path, "rom.cfg"));
    /* the profile's button order (Up Down Left Right A B Start Select) */
    CHECK(seen_io.player_key_bind[0][0] == SDL_SCANCODE_UP && seen_io.player_key_bind[0][4] == SDL_SCANCODE_Z);
    CHECK(seen_io.player_key_bind[0][6] == SDL_SCANCODE_RETURN && seen_io.player_key_bind[0][7] == SDL_SCANCODE_BACKSLASH);
    CHECK(seen_io.player_pad_bind[0][5] == RECOMP_LAUNCHER_PAD_BUTTON(SDL_CONTROLLER_BUTTON_X));
    CHECK(seen_io.player_src[0] == 1 && seen_io.player_src[1] == 2 && seen_io.volume == 100);
#ifdef CYC_LAUNCHER_ROM_SHA256
    CHECK(seen_game.num_known_sha256 == 1 && seen_game.known_sha256[0][0] == 0xAB && seen_game.known_sha256[0][31] == 0x01);
#endif
    /* a cartridge: no Disk row, no disk media names */
    cyc_settings_default(&s);
    CHECK(cyc_ui_launcher(&s, cfg, NULL, &rom, false) == 1);
    CHECK(seen_game.assist_binding_count == CYC_SC_COUNT - 1);
    CHECK(strcmp(seen_game.assist_binding_labels[0], cyc_shortcut_label(CYC_SC_DISK)));
    CHECK(seen_game.num_rom_patterns == 0);

    /* every edit comes back, then survives config.ini */
    cyc_settings_default(&s);
    script_edit = edit_everything;
    script_rom = "F:/somewhere/else.fds";
    script_result = RECOMP_LAUNCHER_RESULT_LAUNCH;
    rom = image;
    CHECK(cyc_ui_launcher(&s, cfg, NULL, &rom, true) == 0);
    CHECK(!strcmp(rom, "F:/somewhere/else.fds"));
    CHECK(s.window_scale == 5 && s.fullscreen == 1 && s.integer_scale == 0 && s.linear_filter == 1);
    CHECK(s.audio_enabled == 0 && s.volume == 40 && s.skip_launcher == 1);
    CHECK(s.bind.source[0] == 2 && s.bind.source[1] == 0 && s.bind.deadzone[0] == 12);
    CHECK(!strcmp(s.bind.device[0], "030000005e0400008e02000000007801"));
    static const int SPEC_TO_CYC[8] = { 4, 5, 6, 7, 0, 1, 3, 2 };
    for (int p = 0; p < 2; ++p)
        for (int b = 0; b < 8; ++b) {
            CHECK(s.bind.button[p][SPEC_TO_CYC[b]].key == SDL_SCANCODE_A + p * 8 + b);
            int want = b == 6 ? CYC_PAD_AXIS(SDL_CONTROLLER_AXIS_TRIGGERLEFT, 1)
                     : b == 7 ? CYC_PAD_COMBO((1 << 4) | (1 << 6)) : CYC_PAD_BUTTON(b + p);
            CHECK(s.bind.button[p][SPEC_TO_CYC[b]].pad == want);
        }
    for (int i = 0; i < CYC_SC_COUNT; ++i) {
        CHECK(s.bind.shortcut[i].key == SDL_SCANCODE_F1 + i);
        CHECK(s.bind.shortcut[i].pad == CYC_PAD_BUTTON(SDL_CONTROLLER_BUTTON_Y + i));
    }
    CHECK(cyc_settings_save(&s, cfg, NULL));
    CycSettings back;
    cyc_settings_default(&back);
    CHECK(cyc_settings_load(&back, cfg, stderr, NULL));
    CHECK(!memcmp(&back.bind, &s.bind, sizeof(s.bind)) && back.volume == 40 && back.skip_launcher == 1);
    /* the edited settings go back into the launcher next time */
    script_edit = NULL;
    script_rom = NULL;
    script_result = RECOMP_LAUNCHER_RESULT_QUIT;
    CHECK(cyc_ui_launcher(&back, cfg, NULL, &rom, true) == 1);
    CHECK(seen_io.assist_key_bind[CYC_SC_DISK] == SDL_SCANCODE_F1 && seen_io.volume == 40);

    /* a cartridge launcher's shortcut rows map around the missing Disk row */
    cyc_settings_default(&s);
    script_edit = edit_everything;
    CHECK(cyc_ui_launcher(&s, cfg, NULL, &rom, false) == 1);      /* closed: edits kept, quit */
    CHECK(s.bind.shortcut[CYC_SC_DISK].key == SDL_SCANCODE_D);    /* untouched */
    CHECK(s.bind.shortcut[CYC_SC_MENU].key == SDL_SCANCODE_F1);   /* row 0 there is Menu */
    /* unavailable: go on without it */
    cyc_settings_default(&s);
    script_edit = NULL;
    script_result = RECOMP_LAUNCHER_RESULT_UNAVAILABLE;
    rom = image;
    CHECK(cyc_ui_launcher(&s, cfg, NULL, &rom, true) == 2 && rom == image);
    remove(cfg);
    printf("ui_launcher_test: %u checks passed\n", checks);
    return 0;
}
