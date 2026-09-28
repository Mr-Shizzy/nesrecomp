/*
 * cyc_host_extras.h - what a game adds to the windowed cycle host.
 *
 * A game project passes its own sources to nesrecomp_add_cycle_game(...
 * HOST_EXTRAS <files>) and defines cyc_host_extras() there; without it the
 * host links cyc_host_extras_none.c, which returns NULL. Everything is
 * optional: leave a field NULL/0 and the host does what it does today.
 *
 *   present      The picture the window shows. Default: the machine's 256x240
 *                frame (cyc_frame_argb). A game that renders more of its world
 *                (widescreen) returns its own ARGB8888 buffer; the width may
 *                change from frame to frame and the window follows it. It is
 *                presentation: screenshots from the window use it, headless
 *                --hash-out / --screenshot / --frame-log never do.
 *
 *   view_modes   RECOMP_RUNTIME_UI_VIEW_MODE_* bits (recomp_runtime_ui.h) the
 *                game can present; the runtime menu then shows the standard
 *                "View mode" row, and get/set_view_mode read and apply it
 *                live. The value persists in config.ini [Game] ViewMode.
 *
 *   menu_items   Extra rows for recomp-ui's runtime menu (struct
 *                RecompRuntimeUiItem, recomp_runtime_ui.h), in the game's own
 *                sections; keys must not start with "cyc.". The host routes
 *                every callback for these keys to menu_callbacks (whose
 *                context is its own). Rows need a recomp-ui build.
 *
 *   load_setting / save_settings
 *                The game's own persistent values, in config.ini [Game]: the
 *                host calls load_setting for every key of that section at
 *                start, and save_settings (write "Key = value" lines) whenever
 *                it saves.
 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#ifdef __cplusplus
extern "C" {
#endif

struct RecompRuntimeUiItem;
struct RecompRuntimeUiCallbacks;

typedef struct CycHostExtras {
    void *ctx;
    const uint32_t *(*present)(void *ctx, int *width, int *height);
    unsigned view_modes;
    int  (*get_view_mode)(void *ctx);
    bool (*set_view_mode)(void *ctx, int mode);
    const struct RecompRuntimeUiItem *menu_items;
    size_t menu_item_count;
    const struct RecompRuntimeUiCallbacks *menu_callbacks;
    void (*load_setting)(void *ctx, const char *key, const char *value);
    void (*save_settings)(void *ctx, FILE *f);
} CycHostExtras;

const CycHostExtras *cyc_host_extras(void);

#ifdef __cplusplus
}
#endif
