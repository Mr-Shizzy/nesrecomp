/*
 * cyc_host.h - what the windowed host (cyc_sdl.c) calls back into cyc_host.c.
 */
#pragma once
#ifdef __cplusplus
extern "C" {
#endif

/* After every frame (frames completed so far): the FDS disk save cadence. */
void cyc_host_frame_done(long frames_done);
/* The player ejected the disk: save it now if it changed. */
void cyc_host_disk_ejected(void);
/* The disk save's state for the drive indicator ("SAVED F1234", "UNSAVED", ...). */
const char *cyc_host_disk_save_status(void);

#ifdef __cplusplus
}
#endif
