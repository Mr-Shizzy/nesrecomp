/*
 * cyc_ring.h - the machine's always-on event ring.
 *
 * Devices whose behaviour a run needs to be explained after the fact (the FDS
 * RAM Adapter and drive first) record every event here from power-on, in
 * Release builds too. Nothing arms it: a probe joins late and queries the
 * window it wants by event index or by frame. The ring keeps the newest
 * CYC_RING_CAPACITY events; older ones are evicted, and per-kind totals keep
 * counting from power-on, so an evicted window is visible as such rather than
 * silently missing.
 *
 * Identical consecutive reads of one register (a polling loop) fold into one
 * event whose `repeat` counts the extra reads, so a wait loop costs one slot.
 *
 * Host access: cyc_ring_dump() (cyc_host --ring-out FILE, --ring-frames A:B,
 * or NESRECOMP_CYC_RING_DUMP=FILE for any host, written at exit).
 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CYC_EV_NONE,
    /* FDS RAM Adapter registers ($4020-$4033, and the sound registers
     * $4040-$4092 whose register side exists in phase 2). addr = register,
     * value = the byte read or written. */
    CYC_EV_FDS_READ,
    CYC_EV_FDS_WRITE,
    /* An interrupt source becoming asserted. value = FDS_IRQ_TIMER/DISK. */
    CYC_EV_FDS_IRQ,
    /* Asserted sources cleared by a register access. addr = register,
     * value = the sources it cleared. */
    CYC_EV_FDS_IRQ_ACK,
    /* The drive clocked one byte. addr = CYC_FDS_BYTE_* flags, value =
     * position (bits 0-23) | byte (bits 24-31): the byte read from the disk,
     * or the one written to it. */
    CYC_EV_FDS_BYTE,
    /* Motor line: value 1 running, 0 stopped. addr: 0 = $4025, 1 = the drive
     * stopped itself at the end of the side. */
    CYC_EV_FDS_MOTOR,
    /* The head went back to the start of the side and the spin-up delay began.
     * value = the delay in CPU cycles. */
    CYC_EV_FDS_REWIND,
    /* The first byte after a rewind: the drive reports ready ($4032.1 = 0). */
    CYC_EV_FDS_READY,
    /* The head passed the last byte of the side. value = side length. */
    CYC_EV_FDS_END,
    /* Drive contents changed. value = side index, or 0xFF when ejected.
     * addr: 0 = host/script, 1 = power-on. */
    CYC_EV_FDS_SIDE,
    /* The CRC check the drive makes when $4025.4 rises in read mode.
     * addr = 1 bad / 0 good, value = the accumulator. */
    CYC_EV_FDS_CRC,
    /* Compiled RAM views (cyc_ramview.c). value = view index (the host's
     * --ram-view-list names them), addr = CPU address:
     *   VIEW_VALID   a view was checked against RAM, matched, and entered at addr
     *   VIEW_REJECT  a view was checked at addr and RAM no longer holds its bytes
     *   VIEW_INVALID a store to addr changed a byte of a validated view
     *   VIEW_EXIT    a view returned at addr because a store invalidated a view
     *   RAM_INTERP   the interpreter ran a RAM instruction at addr that no view
     *                covers; value = its 1KB chunk; the following ones in the
     *                same chunk and frame fold into repeat
     *   VIEW_FRAME   end of a frame in which RAM views ran: value = entries
     *                into RAM views that frame, addr = views validated (<= $FFFF) */
    CYC_EV_VIEW_VALID,
    CYC_EV_VIEW_REJECT,
    CYC_EV_VIEW_INVALID,
    CYC_EV_VIEW_EXIT,
    CYC_EV_RAM_INTERP,
    CYC_EV_VIEW_FRAME,
    CYC_EV_KINDS
} CycRingKind;

enum {
    CYC_FDS_IRQ_TIMER = 1,
    CYC_FDS_IRQ_DISK  = 2,
};

enum {
    CYC_FDS_BYTE_WRITE    = 0x01,  /* write mode ($4025.2 = 0) */
    CYC_FDS_BYTE_GAP_END  = 0x02,  /* this byte ended the gap (the $80 mark) */
    CYC_FDS_BYTE_TRANSFER = 0x04,  /* the byte transfer flag was raised */
    CYC_FDS_BYTE_IRQ      = 0x08,  /* ...and asserted the disk IRQ */
    CYC_FDS_BYTE_CRC      = 0x10,  /* $4025.4 (CRC transfer) was set */
    CYC_FDS_BYTE_STORED   = 0x20,  /* write mode: the byte reached the disk image */
};

typedef struct {
    uint64_t cycle;     /* CPU cycles since power-on (cyc_cycle_count) */
    uint32_t frame;     /* frames completed when the event happened */
    uint16_t kind;      /* CycRingKind */
    uint16_t addr;
    uint32_t value;
    uint32_t repeat;    /* identical reads folded into this event */
} CycRingEvent;

#define CYC_RING_CAPACITY (1u << 20)

/* Frames completed; the scheduler (cyc_run.c) advances it. */
extern uint32_t cyc_ring_frame;

void cyc_ring_reset(void);
/* Record an event at the current cycle. Reads fold into an identical
 * previous read, RAM_INTERP events into the previous one of the same chunk
 * and frame. */
void cyc_ring_push(CycRingKind kind, uint16_t addr, uint32_t value);

/* Events ever recorded (the index the next one gets), and the oldest index
 * still held. */
uint64_t cyc_ring_total(void);
uint64_t cyc_ring_oldest(void);
/* Events of each kind ever recorded, including folded repeats. */
uint64_t cyc_ring_kind_total(CycRingKind kind);
bool     cyc_ring_get(uint64_t index, CycRingEvent *out);
const char *cyc_ring_kind_name(unsigned kind);

/* Write the held events whose frame is in [first_frame, last_frame] as text,
 * one per line, with the per-kind totals first. last_frame UINT32_MAX = all. */
void cyc_ring_dump(void *file, uint32_t first_frame, uint32_t last_frame);
/* Install an atexit dump to $NESRECOMP_CYC_RING_DUMP if it is set. */
void cyc_ring_dump_at_exit_from_env(void);

#ifdef __cplusplus
}
#endif
