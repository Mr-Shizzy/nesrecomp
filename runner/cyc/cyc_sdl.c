/*
 * cyc_sdl.c - SDL2 window and audio for NESRecomp --cycle-accurate builds.
 *
 * Keys: arrows = D-pad, X = A, Z = B, Enter = Start, Right Shift = Select,
 *       Tab (hold) = fast forward, F2 = switch between recompiled code and
 *       the interpreter (live; both produce the same machine, cycle for
 *       cycle), F12 = screenshot (cyc_shot_NNNN.png), Esc = quit.
 *       FDS: F1 = eject the disk / insert the selected side, F3 = select the
 *       next side (drive empty), F4 = show or hide the drive bar, F6 = auto
 *       swap on/off, F7 = fast load on/off (the HLE tier; the plan may refuse
 *       an axis the BIOS or disk cannot support, and the bar says so).
 *       nesref's keys are F1 and F2; F2 is taken here, so the side key is F3.
 * The first connected game controller also works.
 *
 * The drive bar (FDS only) is drawn below the picture, in window rows the
 * emulated picture never covers: which side is in the drive or that it is
 * empty (and the side F1 would insert), the motor, the disk save's state, and
 * the HLE tier's (HLE SWAP FAST / HLE OFF, LOADING while fast load runs a load
 * unpaced, SWAP TO SIDE x while an auto swap holds the drive empty).
 *
 * Fast load: while the drive is loading (cyc_fds_hle_loading), frames run back
 * to back without pacing and without audio, and the window shows the newest
 * one about every 1/60 s. The machine runs the same frames either way.
 * It is never part of cyc_frame_argb(), so screenshots, --hash-out and every
 * comparison see the machine's picture only.
 */
#define SDL_MAIN_HANDLED
#include <SDL.h>

#include "cyc_core.h"
#include "cyc_host.h"
#include "cyc_png.h"
#include "cyc_run.h"

#include <stdio.h>
#include <string.h>

#define NES_A      0x80
#define NES_B      0x40
#define NES_SELECT 0x20
#define NES_START  0x10
#define NES_UP     0x08
#define NES_DOWN   0x04
#define NES_LEFT   0x02
#define NES_RIGHT  0x01

#define AUDIO_RATE 48000
#define BAR_ROWS   15     /* logical rows under the 240 of the picture: two lines */

static uint8_t read_input(SDL_GameController *pad) {
    const Uint8 *k = SDL_GetKeyboardState(NULL);
    uint8_t b = 0;
    if (k[SDL_SCANCODE_X]) b |= NES_A;
    if (k[SDL_SCANCODE_Z]) b |= NES_B;
    if (k[SDL_SCANCODE_RSHIFT]) b |= NES_SELECT;
    if (k[SDL_SCANCODE_RETURN]) b |= NES_START;
    if (k[SDL_SCANCODE_UP]) b |= NES_UP;
    if (k[SDL_SCANCODE_DOWN]) b |= NES_DOWN;
    if (k[SDL_SCANCODE_LEFT]) b |= NES_LEFT;
    if (k[SDL_SCANCODE_RIGHT]) b |= NES_RIGHT;
    if (pad) {
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_A)) b |= NES_A;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_X)) b |= NES_B;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_BACK)) b |= NES_SELECT;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_START)) b |= NES_START;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_DPAD_UP)) b |= NES_UP;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_DPAD_DOWN)) b |= NES_DOWN;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_DPAD_LEFT)) b |= NES_LEFT;
        if (SDL_GameControllerGetButton(pad, SDL_CONTROLLER_BUTTON_DPAD_RIGHT)) b |= NES_RIGHT;
    }
    /* The NES pad cannot press opposite directions at once. */
    if ((b & (NES_UP | NES_DOWN)) == (NES_UP | NES_DOWN)) b &= (uint8_t)~(NES_UP | NES_DOWN);
    if ((b & (NES_LEFT | NES_RIGHT)) == (NES_LEFT | NES_RIGHT)) b &= (uint8_t)~(NES_LEFT | NES_RIGHT);
    return b;
}

/* ---- the drive bar: a 3x5 font, drawn in logical pixels ---- */

static const char GLYPH_CHARS[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.:/()=";
static const uint8_t GLYPHS[][5] = {
    {7, 5, 5, 5, 7}, {2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 7, 1, 7}, {5, 5, 7, 1, 1}, {7, 4, 7, 1, 7},
    {7, 4, 7, 5, 7}, {7, 1, 1, 1, 1}, {7, 5, 7, 5, 7}, {7, 5, 7, 1, 7}, {2, 5, 7, 5, 5}, {6, 5, 6, 5, 6},
    {3, 4, 4, 4, 3}, {6, 5, 5, 5, 6}, {7, 4, 6, 4, 7}, {7, 4, 6, 4, 4}, {3, 4, 5, 5, 3}, {5, 5, 7, 5, 5},
    {7, 2, 2, 2, 7}, {1, 1, 1, 5, 2}, {5, 5, 6, 5, 5}, {4, 4, 4, 4, 7}, {5, 7, 7, 5, 5}, {6, 5, 5, 5, 5},
    {2, 5, 5, 5, 2}, {6, 5, 6, 4, 4}, {2, 5, 5, 6, 3}, {6, 5, 6, 5, 5}, {3, 4, 2, 1, 6}, {7, 2, 2, 2, 2},
    {5, 5, 5, 5, 7}, {5, 5, 5, 5, 2}, {5, 5, 7, 7, 5}, {5, 5, 2, 5, 5}, {5, 5, 2, 2, 2}, {7, 1, 2, 4, 7},
    {0, 0, 7, 0, 0}, {0, 0, 0, 0, 2}, {0, 2, 0, 2, 0}, {1, 1, 2, 4, 4}, {1, 2, 2, 2, 1}, {4, 2, 2, 2, 4},
    {0, 7, 0, 7, 0},
};

/* Draws text at logical (x, y); returns the x after it. */
static int draw_text(SDL_Renderer *ren, int x, int y, const char *s) {
    for (; *s; ++s, x += 4) {
        const char *at = *s == ' ' ? NULL : strchr(GLYPH_CHARS, *s);
        if (!at) continue;
        const uint8_t *g = GLYPHS[at - GLYPH_CHARS];
        for (int r = 0; r < 5; ++r)
            for (int c = 0; c < 3; ++c)
                if (g[r] & (4 >> c)) {
                    SDL_Rect px = {x + c, y + r, 1, 1};
                    SDL_RenderFillRect(ren, &px);
                }
    }
    return x;
}

static void side_name(char *out, size_t n, unsigned side) {
    snprintf(out, n, "DISK %u SIDE %c", side / 2 + 1, 'A' + (int)(side % 2));
}

static void draw_drive_bar(SDL_Renderer *ren, unsigned selected, bool loading) {
    SDL_Rect bar = {0, 240, 256, BAR_ROWS};
    SDL_SetRenderDrawColor(ren, 24, 24, 32, 255);
    SDL_RenderFillRect(ren, &bar);
    char text[64], name[24];
    int side = cyc_fds_side();
    bool motor = cyc_fds_motor_on();
    /* the drive lamp: red while the motor runs, as on the real drive */
    SDL_Rect lamp = {3, 242, 5, 5};
    if (motor) SDL_SetRenderDrawColor(ren, 255, 48, 32, 255);
    else SDL_SetRenderDrawColor(ren, 70, 20, 20, 255);
    SDL_RenderFillRect(ren, &lamp);
    SDL_SetRenderDrawColor(ren, 230, 230, 230, 255);
    if (side >= 0) {
        side_name(name, sizeof(name), (unsigned)side);
        snprintf(text, sizeof(text), "%s%s", name, motor ? " MOTOR" : "");
    } else {
        side_name(name, sizeof(name), selected);
        snprintf(text, sizeof(text), "EMPTY - F1 INSERTS %s", name);
    }
    draw_text(ren, 11, 242, text);
    CycFdsHleStatus hle;
    cyc_fds_hle_status(&hle);
    char htext[64];
    if (hle.swap_target >= 0) {
        side_name(name, sizeof(name), (unsigned)hle.swap_target);
        snprintf(htext, sizeof(htext), "AUTO SWAP TO %s", name);
    } else {
        snprintf(htext, sizeof(htext), "%s%s", cyc_host_hle_text(), loading ? " LOADING" : "");
    }
    SDL_SetRenderDrawColor(ren, 255, 210, 90, 255);
    draw_text(ren, 11, 249, htext);
    const char *save = cyc_host_disk_save_status();
    if (save && *save) {
        int w = (int)strlen(save) * 4;
        SDL_SetRenderDrawColor(ren, 150, 200, 255, 255);
        draw_text(ren, 256 - 2 - w, 242, save);
    }
}

int cyc_sdl_main(const char *title, int scale) {
    SDL_SetMainReady();
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER | SDL_INIT_AUDIO) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    if (scale < 1) scale = 1;
    const bool fds = cyc_is_fds();
    bool bar = fds;
    SDL_Window *win = SDL_CreateWindow(title, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 256 * scale,
                                       (240 + (bar ? BAR_ROWS : 0)) * scale, SDL_WINDOW_RESIZABLE);
    SDL_Renderer *ren = win ? SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED) : NULL;
    SDL_Texture *tex = ren ? SDL_CreateTexture(ren, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STREAMING, 256, 240)
                           : NULL;
    if (!tex) {
        fprintf(stderr, "SDL window: %s\n", SDL_GetError());
        SDL_Quit();
        return 1;
    }
    SDL_RenderSetLogicalSize(ren, 256, 240 + (bar ? BAR_ROWS : 0));

    /* Audio is queued as frames produce it; fast forward drops it. */
    SDL_AudioSpec want = {0}, have;
    want.freq = AUDIO_RATE;
    want.format = AUDIO_S16SYS;
    want.channels = 1;
    want.samples = 1024;
    SDL_AudioDeviceID dev = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);
    if (dev && cyc_audio_enable(have.freq)) SDL_PauseAudioDevice(dev, 0);
    else if (!dev) fprintf(stderr, "SDL audio: %s (continuing without sound)\n", SDL_GetError());

    SDL_GameController *pad = NULL;
    for (int i = 0; i < SDL_NumJoysticks() && !pad; i++)
        if (SDL_IsGameController(i)) pad = SDL_GameControllerOpen(i);

    const double frame_seconds = cyc_host_frame_seconds();
    const Uint64 freq = SDL_GetPerformanceFrequency();
    Uint64 next = SDL_GetPerformanceCounter();
    Uint64 fps_mark = next, shown = 0;
    uint64_t native_mark = cyc_run_native_cycles;
    uint64_t cycles_mark = cyc_cycle_count();
    int frames = 0, shot = 0;
    long frames_done = 0;
    unsigned fds_selected = cyc_fds_side() > 0 ? (unsigned)cyc_fds_side() : 0;
    bool running = true;

    while (running) {
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) running = false;
            if (ev.type == SDL_KEYDOWN && !ev.key.repeat) {
                switch (ev.key.keysym.scancode) {
                case SDL_SCANCODE_ESCAPE: running = false; break;
                case SDL_SCANCODE_F2: cyc_run_native = !cyc_run_native; break;
                case SDL_SCANCODE_F1:
                    if (!fds) break;
                    if (cyc_fds_side() >= 0) {
                        fds_selected = (unsigned)cyc_fds_side();
                        cyc_fds_eject();
                        printf("[cyc disk] f=%ld eject (F1)\n", frames_done);
                        cyc_host_disk_ejected();
                    } else if (cyc_fds_insert(fds_selected)) {
                        printf("[cyc disk] f=%ld insert side %u (F1)\n", frames_done, fds_selected);
                    }
                    fflush(stdout);
                    break;
                case SDL_SCANCODE_F3:
                    if (fds && cyc_fds_side() < 0 && cyc_fds_side_count()) {
                        fds_selected = (fds_selected + 1) % cyc_fds_side_count();
                        printf("[cyc disk] f=%ld select side %u (F3)\n", frames_done, fds_selected);
                        fflush(stdout);
                    }
                    break;
                case SDL_SCANCODE_F6:
                case SDL_SCANCODE_F7:
                    if (!fds) break;
                    printf("[cyc disk] f=%ld %s (%s)\n", frames_done,
                           cyc_host_hle_toggle(ev.key.keysym.scancode == SDL_SCANCODE_F6 ? 0 : 1),
                           ev.key.keysym.scancode == SDL_SCANCODE_F6 ? "F6" : "F7");
                    fflush(stdout);
                    break;
                case SDL_SCANCODE_F4:
                    if (!fds) break;
                    bar = !bar;
                    SDL_RenderSetLogicalSize(ren, 256, 240 + (bar ? BAR_ROWS : 0));
                    printf("[cyc disk] f=%ld drive bar %s (F4)\n", frames_done, bar ? "shown" : "hidden");
                    fflush(stdout);
                    break;
                case SDL_SCANCODE_F12: {
                    char name[64];
                    snprintf(name, sizeof(name), "cyc_shot_%04d.png", shot++);
                    if (cyc_write_png(name, cyc_frame_argb(), 256, 240)) printf("saved %s\n", name);
                    break;
                }
                default: break;
                }
            }
            if (ev.type == SDL_CONTROLLERDEVICEADDED && !pad) pad = SDL_GameControllerOpen(ev.cdevice.which);
        }

        cyc_set_controller(0, read_input(pad));
        cyc_run_frame();
        frames++;
        cyc_host_frame_done(++frames_done);

        const bool loading = fds && cyc_host_frame_unpaced();
        const bool fast = SDL_GetKeyboardState(NULL)[SDL_SCANCODE_TAB] != 0 || loading;
        int16_t pcm[4096];
        size_t n;
        while ((n = cyc_audio_read(pcm, 4096)) > 0) {
            /* Keep latency bounded: skip a frame's audio if ~100 ms are queued. */
            if (dev && !fast && SDL_GetQueuedAudioSize(dev) < (Uint32)(have.freq / 10) * 2)
                SDL_QueueAudio(dev, pcm, (Uint32)(n * sizeof(int16_t)));
        }

        /* A fast-loaded frame is shown only if 1/60 s passed since the last one. */
        Uint64 now = SDL_GetPerformanceCounter();
        if (!loading || now - shown >= (Uint64)(frame_seconds * (double)freq)) {
            SDL_UpdateTexture(tex, NULL, cyc_frame_argb(), 256 * 4);
            SDL_SetRenderDrawColor(ren, 0, 0, 0, 255);
            SDL_RenderClear(ren);
            SDL_Rect picture = {0, 0, 256, 240};
            SDL_RenderCopy(ren, tex, NULL, &picture);
            if (bar) draw_drive_bar(ren, fds_selected, loading);
            SDL_RenderPresent(ren);
            shown = now;
        }
        now = SDL_GetPerformanceCounter();
        if (now - fps_mark >= freq) {
            double secs = (double)(now - fps_mark) / (double)freq;
            uint64_t cycles = cyc_cycle_count() - cycles_mark;
            double native_pct = cycles ? 100.0 * (double)(cyc_run_native_cycles - native_mark) / (double)cycles : 0.0;
            char t[256];
            snprintf(t, sizeof(t), "%s - %.1f%% of CPU cycles recompiled%s - %.0f fps", title, native_pct,
                     cyc_run_native ? "" : " [interpreter only, F2]", frames / secs);
            SDL_SetWindowTitle(win, t);
            fps_mark = now;
            native_mark = cyc_run_native_cycles;
            cycles_mark = cyc_cycle_count();
            frames = 0;
        }

        if (fast) {
            next = now;
            continue;
        }
        next += (Uint64)(frame_seconds * (double)freq);
        if (next > now) {
            Uint32 ms = (Uint32)((next - now) * 1000 / freq);
            if (ms > 1) SDL_Delay(ms - 1);
            while (SDL_GetPerformanceCounter() < next) {}
        } else if (now - next > freq / 4) {
            next = now; /* fell behind: don't try to catch up */
        }
    }

    if (dev) SDL_CloseAudioDevice(dev);
    if (pad) SDL_GameControllerClose(pad);
    SDL_DestroyTexture(tex);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}
