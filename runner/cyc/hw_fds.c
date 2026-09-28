/*
 * hw_fds.c - the Famicom Disk System: the RAM Adapter (iNES mapper 20) and
 * the disk drive it talks to, clocked on the machine's CPU cycles.
 *
 * Board (hw_mapper.c maps it): 32 KiB PRG RAM at $6000-$DFFF, the BIOS ROM
 * at $E000-$FFFF, 8 KiB CHR RAM, CIRAM A10 from $4025.3. Registers:
 *   $4020/$4021 timer IRQ reload   $4022 timer IRQ control   $4023 I/O enable
 *   $4024 write data   $4025 control   $4026 external connector
 *   $4030 status   $4031 read data   $4032 drive status   $4033 external/battery
 *   $4040-$4092 sound (register side only; synthesis is phase 5)
 *
 * Reference. The FDS oracle is nesref's "Mesen 0.9.9" libretro core. Its
 * FDS is not the 0.9.9 release's: the drive clocks a byte every 150 CPU
 * cycles (measured: 39707 bytes in 200 frames of the SMB2J boot), which is
 * libretro/Mesen master (0102910) Core/FDS.cpp, the 0.9.9 file with the byte
 * delay changed from 150 to 149 (lr:324-330). The default profile
 * (CYC_FDS_PROFILE_MESEN) is that file transcribed per CPU cycle; "lr:" line
 * numbers below are from it. Mesen2 (SourMesen/Mesen2 b9fa69d,
 * Core/NES/Mappers/FDS/Fds.cpp, "m2:") and the nesdev wiki
 * (Family_Computer_Disk_System) disagree with it in places:
 * CYC_FDS_PROFILE_MESEN2 follows Mesen2, and CYC_FDS_PROFILE_HARDWARE takes
 * Mesen2's drive plus the nesdev behaviours Mesen leaves out. The hardware
 * column is a judgment call, not oracle-verified: no reference here can
 * measure it. The gates are measured on the default.
 *
 *   behaviour                  mesen (default)      mesen2            hardware
 *   cycles per byte            150 (lr:330)         150 (m2:375)      150
 *   spin-up after rewind       50001 (lr:246-252)   50001             50001
 *   end-of-side IRQ            no (lr:317-326)      yes (m2:359)      yes
 *   $4030 open-bus bits        2,3,5 (lr:431)       2,5 (m2:473)      2,5
 *   $4030.3 = $4025.3          no                   yes (m2:477)      yes (nesdev)
 *   $4030.4 bad CRC            never (lr:435)       .qd (m2:478)      always (nesdev)
 *   $4030.6 end of head        no (lr:436)          no                yes (nesdev)
 *   CRC register               augmented (lr:335)   direct (m2:380)   direct
 *   write lands at             position-2 (lr:96)   position (m2:131) position
 *   $4032.2 write protect      disk absent only     same              + the tab
 *   $4033.7 battery            = $4026.7 (lr:481)   same              1 while $4025.1 = 0 (nesdev)
 *
 * The drive's state machine (lr:222-333, m2:257-378): with no disk or the
 * motor off the head is "at the end" and the drive not scanning; when the
 * motor turns on the head rewinds to position 0 and the drive waits the
 * spin-up delay; then it clocks one byte of the side stream per byte period
 * until the end of the side, where it stops its own motor. Bytes before the
 * gap's $80 mark do not raise the transfer flag once $4025.6 is set.
 *
 * Timing: Mesen clocks the mapper at the start of each CPU cycle, before the
 * cycle's bus access (CPU.cpp StartCpuCycle -> Console::ProcessCpuClock), and
 * samples /IRQ at the end of the cycle (CPU.cpp EndCpuCycle). hw_machine.c
 * calls fds_cpu_clock() at tick 11, after the tick-7 IRQ sample and before the
 * next access, which is the same order (sample, clock, access), DMA cycles
 * included.
 *
 * Every register access, IRQ edge and acknowledge, clocked byte, motor and
 * rewind transition and side change goes to the always-on ring (cyc_ring.h).
 */
#include "hw_fds.h"

#include "cyc_core.h"
#include "cyc_ring.h"
#include "hw_internal.h"
#include "../../common/nes_fds.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define F (hw_cart.m.fds)

/* ------------------------------------------------------------------------- */
/* Media: the disk sides as the drive clocks them                            */
/* ------------------------------------------------------------------------- */

typedef struct {
    uint8_t *bytes;
    uint32_t len;
} FdsSide;

static struct {
    CycFdsOptions opt;
    FdsSide *sides;
    unsigned count;
    int      side;          /* in the drive, or -1 */
    bool     qd;
    uint64_t write_hash;    /* folds every byte written to a disk, for cyc_mem_state_hash */
    uint32_t writes;
} media = { .side = -1 };

typedef struct {
    uint32_t byte_delay, spinup;
    uint8_t  end_irq, open_4030, mirror_4030, end_of_head_4030, crc_direct, write_back2, battery_hw;
} FdsProfile;

static const FdsProfile PROFILES[3] = {
    /* MESEN    */ { 149, 50000, 0, 0x2C, 0, 0, 0, 1, 0 },
    /* MESEN2   */ { 149, 50000, 1, 0x24, 1, 0, 1, 0, 0 },
    /* HARDWARE */ { 149, 50000, 1, 0x24, 1, 1, 1, 0, 1 },
};

static const FdsProfile *prof(void)
{
    unsigned p = (unsigned)media.opt.profile;
    return &PROFILES[p < 3 ? p : 0];
}

static bool crc_reported(void)
{
    return media.opt.crc_check || media.opt.profile == CYC_FDS_PROFILE_HARDWARE ||
           (media.opt.profile == CYC_FDS_PROFILE_MESEN2 && media.qd);
}

void cyc_fds_default_options(CycFdsOptions *o)
{
    memset(o, 0, sizeof(*o));
    o->profile = CYC_FDS_PROFILE_MESEN;
    o->stream_crc = CYC_FDS_CRC_COMPUTED;
    o->boot_side = 0;
}

static void free_media(void)
{
    for (unsigned i = 0; i < media.count; ++i) free(media.sides[i].bytes);
    free(media.sides);
    media.sides = NULL;
    media.count = 0;
    media.side = -1;
}

bool cyc_fds_load_media(const uint8_t *image, size_t size, const CycFdsOptions *opt)
{
    NesFdsImage img;
    if (!nes_fds_image(image, size, opt->qd ? NES_FDS_QD : NES_FDS_NONE, &img)) return false;
    NesFdsProfile layout = opt->profile == CYC_FDS_PROFILE_MESEN ? NES_FDS_PROFILE_MESEN099 : NES_FDS_PROFILE_MESEN2;
    NesFdsCrc crc = opt->stream_crc == CYC_FDS_CRC_MESEN ? NES_FDS_CRC_MESEN : NES_FDS_CRC_COMPUTED;
    FdsSide *sides = (FdsSide *)calloc(img.sides, sizeof(FdsSide));
    if (!sides) return false;
    for (unsigned s = 0; s < img.sides; ++s) {
        size_t len = nes_fds_side_stream(&img, s, layout, crc, NULL, 0);
        sides[s].bytes = (uint8_t *)malloc(len ? len : 1);
        if (!sides[s].bytes || nes_fds_side_stream(&img, s, layout, crc, sides[s].bytes, len) != len) {
            for (unsigned i = 0; i <= s; ++i) free(sides[i].bytes);
            free(sides);
            return false;
        }
        sides[s].len = (uint32_t)len;
    }
    free_media();
    media.opt = *opt;
    media.sides = sides;
    media.count = img.sides;
    media.qd = img.format == NES_FDS_QD;
    media.write_hash = 0;
    media.writes = 0;
    media.side = opt->boot_side >= 0 && (unsigned)opt->boot_side < img.sides ? opt->boot_side : -1;
    return true;
}

bool     cyc_is_fds(void) { return hw_cart.mapper == NES_FDS_MAPPER; }
unsigned cyc_fds_side_count(void) { return media.count; }
int      cyc_fds_side(void) { return media.side; }
const uint8_t *cyc_fds_side_stream(unsigned side, uint32_t *len)
{
    if (side >= media.count) return NULL;
    *len = media.sides[side].len;
    return media.sides[side].bytes;
}
uint32_t cyc_fds_disk_writes(void) { return media.writes; }

/* lr:537-547: ejecting empties the drive; a side goes in only when the
 * drive is empty. The drive's lines follow on its next clock. */
bool cyc_fds_eject(void)
{
    if (media.side < 0) return false;
    media.side = -1;
    cyc_ring_push(CYC_EV_FDS_SIDE, 0, 0xFF);
    return true;
}

bool cyc_fds_insert(unsigned side)
{
    if (media.side >= 0 || side >= media.count) return false;
    media.side = (int)side;
    cyc_ring_push(CYC_EV_FDS_SIDE, 0, side);
    return true;
}

/* ------------------------------------------------------------------------- */
/* Registers                                                                 */
/* ------------------------------------------------------------------------- */

void fds_power_on(void)
{
    /* FDS.h:22-66: everything clear except the two I/O enables. */
    memset(&F, 0, sizeof(F));
    F.disk_regs = F.sound_regs = 1;
    hw_cart.mirroring = HW_MIRROR_VERTICAL;    /* FdsLoader.cpp:141 */
    cyc_ring_push(CYC_EV_FDS_SIDE, 1, media.side < 0 ? 0xFF : (uint32_t)media.side);
}

static void ack(uint16_t addr, uint8_t sources)
{
    uint8_t cleared = (uint8_t)((F.timer_irq ? sources & CYC_FDS_IRQ_TIMER : 0) |
                                (F.disk_irq ? sources & CYC_FDS_IRQ_DISK : 0));
    if (sources & CYC_FDS_IRQ_TIMER) F.timer_irq = 0;
    if (sources & CYC_FDS_IRQ_DISK) F.disk_irq = 0;
    if (cleared) cyc_ring_push(CYC_EV_FDS_IRQ_ACK, addr, cleared);
}

/* The sound unit's registers as far as the CPU can read them back
 * (Mesen FdsAudio.h:96-160): the wavetable, writable while $4089.7 is set,
 * and the two gains, which a write with bit 7 set (envelope off) loads
 * directly. Envelope ticking and synthesis are phase 5. */
static void sound_write(uint16_t addr, uint8_t value)
{
    if (addr <= 0x407F) {
        if (F.wave_write) F.wave[addr & 0x3F] = value & 0x3F;
        return;
    }
    if (addr >= 0x4080 && addr <= 0x408A) F.sound_reg[addr - 0x4080] = value;
    if (addr == 0x4080 && (value & 0x80)) F.vol_gain = value & 0x3F;
    if (addr == 0x4084 && (value & 0x80)) F.mod_gain = value & 0x3F;
    if (addr == 0x4089) F.wave_write = (value & 0x80) != 0;
}

static bool sound_read(uint16_t addr, uint8_t *value)
{
    /* lr FdsAudio.h: with writes disabled a read returns the sample at the
     * current wave position, which only advances once synthesis runs (phase 5). */
    if (addr <= 0x407F) {
        *value = (uint8_t)((*value & 0xC0) | F.wave[F.wave_write ? (addr & 0x3F) : 0]);
        return true;
    }
    if (addr == 0x4090) { *value = (uint8_t)((*value & 0xC0) | F.vol_gain); return true; }
    if (addr == 0x4092) { *value = (uint8_t)((*value & 0xC0) | F.mod_gain); return true; }
    return false;
}

/* lr:350-420. */
static void register_write(uint16_t addr, uint8_t value)
{
    cyc_ring_push(CYC_EV_FDS_WRITE, addr, value);
    if ((!F.disk_regs && addr >= 0x4024 && addr <= 0x4026) || (!F.sound_regs && addr >= 0x4040)) return;
    switch (addr) {
    case 0x4020: F.irq_reload = (uint16_t)((F.irq_reload & 0xFF00) | value); break;
    case 0x4021: F.irq_reload = (uint16_t)((F.irq_reload & 0x00FF) | value << 8); break;
    case 0x4022:
        F.irq_repeat = value & 1;
        F.irq_enabled = (value & 2) && F.disk_regs;
        if (F.irq_enabled) F.irq_counter = F.irq_reload;
        else ack(addr, CYC_FDS_IRQ_TIMER);
        break;
    case 0x4023:
        F.disk_regs = value & 1;
        F.sound_regs = (value >> 1) & 1;
        if (!F.disk_regs) {
            F.irq_enabled = 0;
            ack(addr, CYC_FDS_IRQ_TIMER | CYC_FDS_IRQ_DISK);
        }
        break;
    case 0x4024:
        F.write_data = value;
        F.transfer = 0;
        ack(addr, CYC_FDS_IRQ_DISK);
        break;
    case 0x4025: {
        uint8_t was_on = F.motor_on;
        F.ctrl = value;
        F.motor_on = value & 1;
        F.reset_transfer = (value >> 1) & 1;
        F.read_mode = (value >> 2) & 1;
        hw_cart.mirroring = (value & 8) ? HW_MIRROR_HORIZONTAL : HW_MIRROR_VERTICAL;
        F.crc_control = (value >> 4) & 1;
        F.crc_enable = (value >> 6) & 1;
        F.transfer_irq = value >> 7;
        ack(addr, CYC_FDS_IRQ_DISK);
        if (was_on != F.motor_on) cyc_ring_push(CYC_EV_FDS_MOTOR, 0, F.motor_on);
        break;
    }
    case 0x4026: F.ext_out = value; break;
    default:
        if (addr >= 0x4040) sound_write(addr, value);
        break;
    }
}

/* lr:422-486. False leaves the bus open. */
static bool register_read(uint16_t addr, uint8_t *value)
{
    const FdsProfile *p = prof();
    uint8_t v = *value;
    bool driven = false;
    if (F.sound_regs && addr >= 0x4040) {
        driven = sound_read(addr, &v);
    } else if (F.disk_regs && addr >= 0x4030 && addr <= 0x4033) {
        driven = true;
        switch (addr) {
        case 0x4030:
            v &= p->open_4030;
            v |= F.timer_irq ? 0x01 : 0;
            v |= F.transfer ? 0x02 : 0;
            if (p->mirror_4030 && hw_cart.mirroring == HW_MIRROR_HORIZONTAL) v |= 0x08;
            if (crc_reported() && F.bad_crc) v |= 0x10;
            if (p->end_of_head_4030 && F.at_end) v |= 0x40;
            F.transfer = 0;
            ack(addr, CYC_FDS_IRQ_TIMER | CYC_FDS_IRQ_DISK);
            break;
        case 0x4031:
            F.transfer = 0;
            ack(addr, CYC_FDS_IRQ_DISK);
            v = F.read_data;
            break;
        case 0x4032: {
            bool in = media.side >= 0;
            v &= 0xF8;
            v |= !in ? 0x01 : 0;
            v |= (!in || !F.scanning) ? 0x02 : 0;
            v |= (!in || media.opt.write_protect) ? 0x04 : 0;
            break;
        }
        default: /* $4033 */
            if (p->battery_hw)
                v = (uint8_t)((F.ext_out & 0x7F) | (!F.reset_transfer ? 0x80 : 0));
            else
                v = F.ext_out;
            break;
        }
    }
    if (driven) {
        *value = v;
        cyc_ring_push(CYC_EV_FDS_READ, addr, v);
    }
    return driven;
}

void fds_cpu_write(uint16_t addr, uint8_t value)
{
    if (addr >= 0x6000) {
        if (addr < 0xE000) hw_cart.wram[addr - 0x6000] = value;
        return;                                  /* $E000-$FFFF: the BIOS ROM */
    }
    if (addr <= 0x4092) register_write(addr, value);
}

bool fds_cpu_read(uint16_t addr, uint8_t *value)
{
    if (addr >= 0x6000) {                         /* $8000+ is read through prg_off */
        *value = hw_cart.wram[(addr - 0x6000) & 0x7FFF];
        return true;
    }
    if (addr <= 0x4092) return register_read(addr, value);
    return false;
}

bool fds_irq(void) { return F.timer_irq || F.disk_irq; }

/* ------------------------------------------------------------------------- */
/* The drive, one CPU cycle at a time                                        */
/* ------------------------------------------------------------------------- */

/* lr:335-348: the register shifts right and takes each data bit in at
 * the top, so a message followed by its CRC leaves 0 two bytes late. */
static void crc_augmented(uint8_t value)
{
    for (unsigned n = 1; n <= 0x80; n <<= 1) {
        unsigned carry = F.crc & 1;
        F.crc >>= 1;
        if (carry) F.crc ^= 0x8408;
        if (value & n) F.crc ^= 0x8000;
    }
}

/* m2:380-390: CRC-16/KERMIT, which reaches 0 right after the CRC bytes. */
static void crc_direct(uint8_t value)
{
    F.crc ^= value;
    for (unsigned n = 0; n < 8; ++n) F.crc = (uint16_t)((F.crc >> 1) ^ (0x8408u & (0u - (F.crc & 1u))));
}

static void crc_update(uint8_t value)
{
    if (prof()->crc_direct) crc_direct(value);
    else crc_augmented(value);
}

static void disk_write(uint32_t index, uint8_t value, uint16_t *flags)
{
    FdsSide *s = &media.sides[media.side];
    if (media.opt.write_protect || index >= s->len) return;
    if (s->bytes[index] != value) {
        s->bytes[index] = value;
        media.write_hash = (media.write_hash ^ ((uint64_t)media.side << 32 | index << 8 | value)) * 0x100000001B3ull;
        media.writes++;
    }
    *flags |= CYC_FDS_BYTE_STORED;
}

static void clock_byte(void)
{
    const FdsProfile *p = prof();
    FdsSide *s = &media.sides[media.side];
    uint8_t data = 0;
    uint16_t flags = 0;
    bool need_irq = F.transfer_irq != 0;
    if (!F.scanning) cyc_ring_push(CYC_EV_FDS_READY, 0, F.position);
    F.scanning = 1;
    if (F.read_mode) {
        /* lr:264-285, m2:301-327. */
        data = F.position < s->len ? s->bytes[F.position] : 0;
        if (!F.prev_crc_control) crc_update(data);
        if (!F.crc_enable) {
            F.gap_ended = 0;
            F.crc = 0;
            if (p->crc_direct) F.bad_crc = 0;
        } else if (data && !F.gap_ended) {
            F.gap_ended = 1;
            need_irq = false;
            flags |= CYC_FDS_BYTE_GAP_END;
        }
        if (F.gap_ended) {
            F.transfer = 1;
            F.read_data = data;
            flags |= CYC_FDS_BYTE_TRANSFER;
            if (need_irq) {
                if (!F.disk_irq) cyc_ring_push(CYC_EV_FDS_IRQ, 0, CYC_FDS_IRQ_DISK);
                F.disk_irq = 1;
                flags |= CYC_FDS_BYTE_IRQ;
            }
        }
        if (!F.prev_crc_control && F.crc_control) {
            /* The check runs whatever the profile; whether $4030.4 shows it
             * is crc_reported(). */
            F.bad_crc = F.crc != 0;
            cyc_ring_push(CYC_EV_FDS_CRC, F.bad_crc, F.crc);
        }
    } else {
        /* lr:286-313, m2:328-352. */
        flags |= CYC_FDS_BYTE_WRITE;
        if (!F.crc_control) {
            F.transfer = 1;
            data = F.write_data;
            flags |= CYC_FDS_BYTE_TRANSFER;
            if (need_irq) {
                if (!F.disk_irq) cyc_ring_push(CYC_EV_FDS_IRQ, 0, CYC_FDS_IRQ_DISK);
                F.disk_irq = 1;
                flags |= CYC_FDS_BYTE_IRQ;
            }
        }
        if (!F.crc_enable) {
            data = 0;
            if (p->crc_direct) F.crc = 0;
        }
        if (!F.crc_control) {
            crc_update(data);
        } else {
            if (!p->crc_direct && !F.prev_crc_control) {
                crc_augmented(0);        /* lr:302-306: finish the augmented CRC */
                crc_augmented(0);
            }
            data = (uint8_t)F.crc;
            F.crc >>= 8;
        }
        if (p->write_back2) {
            if (F.position >= 2) disk_write(F.position - 2, data, &flags);
        } else {
            disk_write(F.position, data, &flags);
        }
        F.gap_ended = 0;
        if (p->crc_direct) F.bad_crc = 0;
    }
    if (F.crc_control) flags |= CYC_FDS_BYTE_CRC;
    cyc_ring_push(CYC_EV_FDS_BYTE, flags, (F.position & 0xFFFFFF) | (uint32_t)data << 24);
    F.prev_crc_control = F.crc_control;
    F.position++;
    if (F.position >= s->len) {
        /* lr:317-326: the drive stops at the end of the side. */
        F.motor_on = 0;
        F.at_end = 1;
        cyc_ring_push(CYC_EV_FDS_END, 0, s->len);
        cyc_ring_push(CYC_EV_FDS_MOTOR, 1, 0);
        if (p->end_irq && F.transfer_irq) {
            if (!F.disk_irq) cyc_ring_push(CYC_EV_FDS_IRQ, 0, CYC_FDS_IRQ_DISK);
            F.disk_irq = 1;              /* m2:359-364 */
        }
    } else {
        F.delay = p->byte_delay;
    }
}

void fds_cpu_clock(void)
{
    /* lr:103-116: the timer. It counts down from the reload value and
     * fires on the cycle after it reaches 0. */
    if (F.irq_enabled) {
        if (F.irq_counter == 0) {
            if (!F.timer_irq) cyc_ring_push(CYC_EV_FDS_IRQ, 0, CYC_FDS_IRQ_TIMER);
            F.timer_irq = 1;
            F.irq_counter = F.irq_reload;
            if (!F.irq_repeat) F.irq_enabled = 0;
        } else {
            F.irq_counter--;
        }
    }
    /* lr:235-252: no disk or no motor -> the head is at the end; the
     * motor turning on rewinds it and starts the spin-up delay. */
    if (media.side < 0 || !F.motor_on) {
        F.end_of_head = 1;
        F.scanning = 0;
        return;
    }
    if (F.reset_transfer && !F.scanning) return;
    if (F.end_of_head) {
        F.delay = prof()->spinup;
        F.end_of_head = 0;
        F.at_end = 0;
        F.position = 0;
        F.gap_ended = 0;
        cyc_ring_push(CYC_EV_FDS_REWIND, 0, F.delay);
        return;
    }
    if (F.delay > 0) {
        F.delay--;
        return;
    }
    clock_byte();
}

/* ------------------------------------------------------------------------- */
/* Comparison                                                                */
/* ------------------------------------------------------------------------- */

uint64_t fds_state_hash(uint64_t acc)
{
    const uint8_t *b = (const uint8_t *)&F;
    for (size_t i = 0; i < sizeof(F); ++i) acc = acc * 131 + b[i];
    acc = acc * 131 + (uint64_t)(media.side + 1);
    acc = acc * 131 + (uint64_t)media.opt.profile;
    return acc;
}

/* The disks are memory a program can read back after writing them. */
uint64_t fds_media_hash(uint64_t h)
{
    return cyc_trace_mix(h, media.write_hash ^ ((uint64_t)(media.side + 1) << 56));
}

void fds_state_dump(void *file)
{
    FILE *f = (FILE *)file;
    fprintf(f, "fds.side %d\nfds.profile %d\nfds.irq_reload %04X\nfds.irq_counter %04X\nfds.irq_enabled %u\n"
               "fds.irq_repeat %u\nfds.disk_regs %u\nfds.sound_regs %u\nfds.write_data %02X\nfds.ctrl %02X\n"
               "fds.ext_out %02X\nfds.timer_irq %u\nfds.disk_irq %u\nfds.transfer %u\nfds.read_data %02X\n"
               "fds.bad_crc %u\nfds.end_of_head %u\nfds.gap_ended %u\nfds.scanning %u\nfds.prev_crc_control %u\n"
               "fds.at_end %u\nfds.crc %04X\nfds.delay %u\nfds.position %u\nfds.disk_writes %u\n",
            media.side, (int)media.opt.profile, F.irq_reload, F.irq_counter, F.irq_enabled, F.irq_repeat,
            F.disk_regs, F.sound_regs, F.write_data, F.ctrl, F.ext_out, F.timer_irq, F.disk_irq, F.transfer,
            F.read_data, F.bad_crc, F.end_of_head, F.gap_ended, F.scanning, F.prev_crc_control, F.at_end, F.crc,
            F.delay, F.position, media.writes);
}
