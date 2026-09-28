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
/* The FDS HLE tier: flip one axis with a live toggle (0 auto swap, 1 fast
 * load), re-planned through nes_fds_hle_plan(); returns the plan's text for the
 * drive bar ("SWAP+FAST", "SWAP", "FAST", "HLE OFF", "SWAP DENIED"...). */
const char *cyc_host_hle_toggle(int axis);
const char *cyc_host_hle_text(void);
/* Pacing: seconds per frame, and whether the frame just run may skip pacing
 * and presentation (fast load during a disk load). */
double cyc_host_frame_seconds(void);
bool   cyc_host_frame_unpaced(void);

#ifdef __cplusplus
}
#endif
