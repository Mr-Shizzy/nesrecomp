/*
 * hw_fds.h - the Famicom Disk System RAM Adapter and disk drive (hw_fds.c),
 * as the cartridge-side board hw_mapper.c dispatches to for mapper 20.
 * Hosts use cyc_core.h (cyc_load_fds and the cyc_fds_* drive controls).
 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cyc_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Power-on: registers cleared as Mesen's constructor leaves them
 * (FDS.h:22-66), the memory map set up by hw_mapper.c. */
void     fds_power_on(void);
/* CPU writes to $4020-$FFFF and reads of $4020-$7FFF. */
void     fds_cpu_write(uint16_t addr, uint8_t value);
bool     fds_cpu_read(uint16_t addr, uint8_t *value);
/* One CPU cycle of the timer and the drive, before the cycle's access. */
void     fds_cpu_clock(void);
bool     fds_irq(void);
/* Build the side streams from a disk image (cyc_load_fds, hw_machine.c). */
bool     cyc_fds_load_media(const uint8_t *image, size_t size, const CycFdsOptions *options);
uint64_t fds_state_hash(uint64_t acc);
uint64_t fds_media_hash(uint64_t h);
void     fds_state_dump(void *file);

#ifdef __cplusplus
}
#endif
