/* FDS RAM Adapter and drive contracts (hw_fds.c), on the whole machine.
 *
 * Part 1 drives the board directly: register accesses through the CPU bus
 * decode (hw_bus_read/hw_bus_write) and the drive through its per-cycle clock,
 * with expectations from the reference the default profile transcribes
 * (libretro/Mesen 0102910 Core/FDS.cpp, "lr:") and, for the other profiles,
 * Mesen2 b9fa69d Fds.cpp ("m2:") and the nesdev wiki.
 *
 * Part 2 runs the synthetic BIOS of tools/cyc/fds_board_fixtures.py on the
 * CPU (interpreter) for each case in <fixtures>/cases.txt and checks the CPU
 * RAM it leaves.
 *
 *   cyc_fds_board_test <fixtures dir>
 */
#include "cyc_core.h"
#include "cyc_ring.h"
#include "cyc_run.h"
#include "hw_internal.h"
#include "../../common/nes_fds.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { \
    fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); exit(1); \
} } while (0)

static uint8_t *read_file(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot read %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *p = (uint8_t *)malloc((size_t)n);
    if (!p || fread(p, 1, (size_t)n, f) != (size_t)n) exit(1);
    fclose(f);
    *size = (size_t)n;
    return p;
}

static uint8_t *bios, *image;
static size_t bios_size, image_size;

static void load(CycFdsProfile profile, CycFdsStreamCrc crc, bool check, bool protect, int side)
{
    CycFdsOptions o;
    cyc_fds_default_options(&o);
    o.profile = profile;
    o.stream_crc = crc;
    o.crc_check = check;
    o.write_protect = protect;
    o.boot_side = side;
    CHECK(cyc_load_fds(bios, bios_size, image, image_size, &o));
    cyc_power_on(0);
    /* One cycle with the motor off: the head is at the end (lr:235-240), as it
     * is by the time any program starts the motor (FDS.h:51 powers it on
     * false, which a motor-on write at cycle 0 would expose). */
    hw_cart_cpu_clock_late();
}

/* A register read as `LDA $40xx` makes it: the open bus holds $40. */
static uint8_t rd_bus(uint16_t addr, uint8_t open)
{
    hw.data_bus = open;
    hw.cpu_addr = addr;
    return hw_bus_read(addr);
}
static uint8_t rd(uint16_t addr) { return rd_bus(addr, (uint8_t)(addr >> 8)); }
static void wr(uint16_t addr, uint8_t v) { hw.cpu_addr = addr; hw_bus_write(addr, v); }
static void clock(unsigned n) { while (n--) hw_cart_cpu_clock_late(); }
static bool irq(void) { return hw_cart_irq(); }

/* Clocks until the drive has clocked another byte (ring fds.byte), at most
 * limit; returns the clocks taken, 0 if none came. */
static unsigned until_byte(unsigned limit, CycRingEvent *out)
{
    uint64_t before = cyc_ring_kind_total(CYC_EV_FDS_BYTE);
    for (unsigned n = 1; n <= limit; ++n) {
        clock(1);
        if (cyc_ring_kind_total(CYC_EV_FDS_BYTE) != before) {
            for (uint64_t i = cyc_ring_total(); i-- > cyc_ring_oldest();) {
                CycRingEvent e;
                if (cyc_ring_get(i, &e) && e.kind == CYC_EV_FDS_BYTE) { *out = e; break; }
            }
            return n;
        }
    }
    return 0;
}

static void test_memory_map(void)
{
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, 0);
    CHECK(hw_cart.mapper == 20 && hw_cart.chr_ram && hw_cart.chr_len == 8192 && hw_cart.wram_len == 32768);
    wr(0x6000, 0x12); wr(0x7FFF, 0x34); wr(0x8000, 0x56); wr(0xDFFF, 0x78);
    CHECK(rd(0x6000) == 0x12 && rd(0x7FFF) == 0x34 && rd(0x8000) == 0x56 && rd(0xDFFF) == 0x78);
    CHECK(hw_cart.wram[0] == 0x12 && hw_cart.wram[0x7FFF] == 0x78);
    uint8_t rom = bios[0];
    wr(0xE000, (uint8_t)~rom);
    CHECK(rd(0xE000) == rom && rd(0xFFFC) == bios[0x1FFC]);          /* ROM: writes ignored */
    for (unsigned a = 0x8000; a < 0xE000; a += 0x1000) CHECK(!hw_prg_is_rom((uint16_t)a));
    CHECK(hw_prg_is_rom(0xE000) && hw_prg_is_rom(0xF000));
    CHECK(hw_prg_bank4(0xE000) == 0 && hw_prg_bank4(0xF123) == 1);
    uint8_t v;
    CHECK(cyc_debug_peek(0x8000, &v) && v == 0x56 && cyc_debug_peek(0x6000, &v) && v == 0x12);
    /* $4034-$5FFF: nothing answers (lr:422-486 returns open bus). */
    CHECK(rd_bus(0x4034, 0xA5) == 0xA5 && rd_bus(0x5000, 0x5A) == 0x5A && rd_bus(0x4024, 0x3C) == 0x3C);
    /* Nametable arrangement: vertical at power-on (FdsLoader.cpp:141), then
     * $4025.3: 1 = horizontal (lr:399). */
    CHECK(hw_cart.mirroring == HW_MIRROR_VERTICAL);
    wr(0x4025, 0x08); CHECK(hw_cart.mirroring == HW_MIRROR_HORIZONTAL);
    CHECK(hw_cart_ciram_a10(0x2400) == 0 && hw_cart_ciram_a10(0x2800) == 0x400);
    wr(0x4025, 0x00); CHECK(hw_cart.mirroring == HW_MIRROR_VERTICAL);
    CHECK(hw_cart_ciram_a10(0x2400) == 0x400 && hw_cart_ciram_a10(0x2800) == 0);
    NesCartInfo info;
    nes_fds_cart_info(&info);
    CHECK(nes_cart_variant_supported(&info) && nes_cart_identity(&info) == nes_cart_identity(&hw_cart.info));
}

static void test_timer(void)
{
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, -1);
    /* lr:103-116: loaded on enable, counts down each cycle, fires on the
     * cycle after it reaches 0: reload + 1 cycles per IRQ. */
    wr(0x4020, 5); wr(0x4021, 0); wr(0x4022, 0x02);
    clock(5); CHECK(!irq());
    clock(1); CHECK(irq());
    uint8_t s = rd(0x4030);
    CHECK((s & 1) && !irq());                         /* $4030 read acknowledges */
    CHECK(!(rd(0x4030) & 1));
    clock(100); CHECK(!irq());                        /* one-shot: disabled after firing */
    wr(0x4022, 0x03);                                 /* repeat */
    for (int k = 0; k < 3; ++k) {
        clock(5); CHECK(!irq());
        clock(1); CHECK(irq());
        rd(0x4030);
    }
    clock(3);
    wr(0x4020, 9);                                    /* reload changes take effect on the next reload */
    clock(3); CHECK(irq()); rd(0x4030);
    clock(9); CHECK(!irq()); clock(1); CHECK(irq());
    wr(0x4022, 0x01); CHECK(!irq());                  /* disabling acknowledges (lr:372) */
    clock(50); CHECK(!irq());
    /* $4023.0 = 0: timer off and acknowledged; $4022 cannot enable it; the
     * reload registers still take writes (nesdev, $4020). */
    wr(0x4022, 0x03); clock(10); CHECK(irq());
    wr(0x4023, 0x02); CHECK(!irq());
    wr(0x4020, 2); wr(0x4022, 0x02); clock(20); CHECK(!irq());
    CHECK(rd_bus(0x4030, 0x77) == 0x77);             /* disk registers off: open bus */
    wr(0x4023, 0x03); wr(0x4022, 0x02); clock(2); CHECK(!irq()); clock(1); CHECK(irq());
}

static void test_status_bits(void)
{
    static const struct { CycFdsProfile p; uint8_t open, mirror; } CASES[] = {
        { CYC_FDS_PROFILE_MESEN, 0x2C, 0 }, { CYC_FDS_PROFILE_MESEN2, 0x24, 8 }, { CYC_FDS_PROFILE_HARDWARE, 0x24, 8 },
    };
    for (unsigned i = 0; i < 3; ++i) {
        load(CASES[i].p, CYC_FDS_CRC_COMPUTED, false, false, -1);
        CHECK(rd_bus(0x4030, 0xFF) == CASES[i].open);           /* lr:431 / m2:473 */
        wr(0x4025, 0x08);
        CHECK(rd_bus(0x4030, 0xFF) == (CASES[i].open | CASES[i].mirror));
        CHECK(rd_bus(0x4030, 0x00) == CASES[i].mirror);
        /* $4032 with no disk: bits 0-2 set, 3-7 open bus (lr:449-456). */
        CHECK(rd_bus(0x4032, 0x00) == 0x07 && rd_bus(0x4032, 0xFF) == 0xFF && rd_bus(0x4032, 0x40) == 0x47);
        /* $4033: $4026 as written (lr:479-481); the hardware profile reads the
         * battery in bit 7 while $4025.1 = 0 and ANDs the connector bits. */
        wr(0x4026, 0x5A);
        wr(0x4025, 0x00);
        CHECK(rd(0x4033) == (CASES[i].p == CYC_FDS_PROFILE_HARDWARE ? 0xDA : 0x5A));
        wr(0x4025, 0x02);
        CHECK(rd(0x4033) == (CASES[i].p == CYC_FDS_PROFILE_HARDWARE ? 0x5A : 0x5A));
    }
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, 0);
    CHECK(rd_bus(0x4032, 0x00) == 0x02);                        /* inserted, not ready, writable */
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, true, 0);
    CHECK(rd_bus(0x4032, 0x00) == 0x06);                        /* write-protect tab */
}

static void test_drive(CycFdsProfile profile, unsigned period)
{
    load(profile, CYC_FDS_CRC_COMPUTED, false, false, 0);
    uint32_t len;
    const uint8_t *side = cyc_fds_side_stream(0, &len);
    CHECK(side && len >= NES_FDS_SIDE_BYTES);
    CycRingEvent e;
    /* Motor on, transfer reset held: nothing moves (lr:242-244). */
    wr(0x4025, 0x07);
    CHECK(until_byte(60000, &e) == 0 && (rd_bus(0x4032, 0) & 2));
    /* Released: the head rewinds, waits 50000 cycles, clocks byte 0 on the
     * next (lr:246-257). */
    wr(0x4025, 0x05);
    CHECK(until_byte(60000, &e) == 50002);                      /* the rewind clock, 50000 waits, the byte */
    CHECK((e.value & 0xFFFFFF) == 0 && !(rd_bus(0x4032, 0) & 2));
    for (int k = 1; k < 5; ++k) {
        CHECK(until_byte(1000, &e) == period);
        CHECK((e.value & 0xFFFFFF) == (uint32_t)k);
    }
    /* Without $4025.6 no byte transfers. With it, the first nonzero byte ends
     * the gap and transfers without an IRQ; every later byte raises one
     * (lr:264-285). */
    CHECK(!(rd(0x4030) & 2));
    wr(0x4025, 0x05 | 0x40 | 0x80);
    uint32_t pos = 5, mark = 0;
    while (side[pos] != NES_FDS_GAP_MARK) ++pos;
    mark = pos;
    for (;;) {
        CHECK(until_byte(1000, &e));
        if ((e.value & 0xFFFFFF) == mark) break;
        CHECK(!irq() && !(rd(0x4030) & 2));
    }
    CHECK((e.addr & CYC_FDS_BYTE_GAP_END) && !irq());
    CHECK(rd(0x4031) == NES_FDS_GAP_MARK);
    for (uint32_t k = 1; k <= 20; ++k) {
        CHECK(until_byte(1000, &e) && irq());
        uint8_t s = rd(0x4030);                                  /* transfer flag, then acknowledged */
        CHECK((s & 2) && !irq() && !(rd(0x4030) & 2));
        CHECK(rd(0x4031) == side[mark + k]);
    }
    /* Each acknowledge: $4031 read, $4024 write, $4025 write (lr:387-408). */
    CHECK(until_byte(1000, &e) && irq()); rd(0x4031); CHECK(!irq());
    CHECK(until_byte(1000, &e) && irq()); wr(0x4024, 0); CHECK(!irq() && !(rd(0x4030) & 2));
    CHECK(until_byte(1000, &e) && irq()); wr(0x4025, 0x05 | 0x40 | 0x80); CHECK(!irq());
    /* To the end of the side: the drive stops its motor (lr:317-326); the
     * Mesen2/hardware drive raises an end IRQ (m2:359-364). */
    wr(0x4025, 0x05);
    uint64_t ends = cyc_ring_kind_total(CYC_EV_FDS_END);
    unsigned guard = 0;
    while (cyc_ring_kind_total(CYC_EV_FDS_END) == ends && guard++ < 70000) until_byte(1000, &e);
    CHECK(cyc_ring_kind_total(CYC_EV_FDS_END) == ends + 1 && (e.value & 0xFFFFFF) == len - 1);
    clock(1);
    CHECK((rd_bus(0x4032, 0) & 2) && until_byte(1000, &e) == 0);
    /* The motor bit rising again rewinds (lr:235-252). */
    wr(0x4025, 0x04); clock(1); wr(0x4025, 0x05);
    CHECK(until_byte(60000, &e) == 50002 && (e.value & 0xFFFFFF) == 0);
    /* Eject: not ready and no disk at once; insert only into an empty drive. */
    CHECK(!cyc_fds_insert(0));
    CHECK(cyc_fds_eject() && !cyc_fds_eject() && cyc_fds_side() == -1);
    clock(1);
    CHECK(rd_bus(0x4032, 0) == 0x07 && until_byte(1000, &e) == 0);
    CHECK(!cyc_fds_insert(7) && cyc_fds_insert(1) && cyc_fds_side() == 1);
    CHECK(until_byte(60000, &e) == 50002);                      /* a new disk: rewound, spin-up */
}

static void test_end_irq(void)
{
    for (int p = 0; p < 2; ++p) {
        load(p ? CYC_FDS_PROFILE_MESEN2 : CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, 0);
        uint32_t len;
        cyc_fds_side_stream(0, &len);
        wr(0x4025, 0x85);                                        /* IRQ enabled, $4025.6 = 0: no transfers */
        CycRingEvent e;
        do CHECK(until_byte(60000, &e)); while ((e.value & 0xFFFFFF) != len - 1);
        CHECK(irq() == (p == 1));
    }
}

static void test_crc(void)
{
    /* Block 1 through the transfer flag, then the CRC check the BIOS makes
     * ($E706: CRC transfer control rises after the first CRC byte). */
    static const struct { CycFdsProfile p; CycFdsStreamCrc crc; bool check; uint8_t flag; } CASES[] = {
        { CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, 0 },
        { CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_MESEN, false, 0 },      /* never reported (lr:435) */
        { CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_MESEN, true, 0x10 },
        { CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, true, 0 },
        { CYC_FDS_PROFILE_HARDWARE, CYC_FDS_CRC_COMPUTED, false, 0 },
        { CYC_FDS_PROFILE_HARDWARE, CYC_FDS_CRC_MESEN, false, 0x10 },
        { CYC_FDS_PROFILE_MESEN2, CYC_FDS_CRC_MESEN, false, 0 },     /* reported for .qd only (m2:478) */
    };
    for (unsigned i = 0; i < sizeof(CASES) / sizeof(CASES[0]); ++i) {
        load(CASES[i].p, CASES[i].crc, CASES[i].check, false, 0);
        wr(0x4025, 0x45);
        CycRingEvent e;
        do CHECK(until_byte(60000, &e)); while (!(e.addr & CYC_FDS_BYTE_GAP_END));
        for (int k = 0; k < NES_FDS_INFO_BYTES + 1; ++k) CHECK(until_byte(1000, &e));   /* block, CRC lo */
        wr(0x4025, 0x55);
        CHECK(until_byte(1000, &e));                                                     /* CRC hi */
        CHECK((rd(0x4030) & 0x10) == CASES[i].flag);
    }
}

static void test_write(void)
{
    /* p: 0 mesen profile (under the head, the default), 1 mesen2, 2 write
     * protected, 3 mesen profile with Mesen's two-behind write position. */
    for (int p = 0; p < 4; ++p) {
        bool protect = p == 2;
        load(p == 1 ? CYC_FDS_PROFILE_MESEN2 : CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, protect, 0);
        if (p == 3) {
            CycFdsOptions o;
            cyc_fds_default_options(&o);
            o.write_at = CYC_FDS_WRITE_MESEN;
            CHECK(cyc_load_fds(bios, bios_size, image, image_size, &o));
            cyc_power_on(0);
            hw_cart_cpu_clock_late();
        }
        uint64_t gen0 = cyc_fds_disk_generation();
        uint32_t len;
        const uint8_t *side = cyc_fds_side_stream(0, &len);
        uint8_t before[64];
        memcpy(before, side + 100, sizeof(before));
        wr(0x4025, 0x01 | 0x40 | 0x80);                          /* write mode, enabled, IRQ */
        wr(0x4024, 0xA5);
        CycRingEvent e;
        do CHECK(until_byte(60000, &e)); while ((e.value & 0xFFFFFF) < 110);
        CHECK(irq() && (e.addr & CYC_FDS_BYTE_WRITE) && (e.addr & CYC_FDS_BYTE_TRANSFER));
        wr(0x4024, 0x5C);                                        /* acknowledges; byte 111 carries it */
        CHECK(!irq());
        CHECK(until_byte(1000, &e) && (e.value & 0xFFFFFF) == 111 && (e.value >> 24) == 0x5C);
        /* Under the head by default (m2:131); Mesen 0.9.9 two bytes behind it
         * (lr:96-98); a protected disk keeps its bytes. */
        side = cyc_fds_side_stream(0, &len);
        uint32_t base_len;
        const uint8_t *base = cyc_fds_side_base(0, &base_len);
        CHECK(base_len == len && !memcmp(base + 100, before, sizeof(before)));   /* the image's stream stays */
        if (protect) {
            CHECK(!memcmp(before, side + 100, sizeof(before)) && cyc_fds_disk_writes() == 0);
            CHECK(cyc_fds_disk_generation() == gen0);
        } else {
            uint32_t at = p == 3 ? 109 : 111;
            CHECK(side[at] == 0x5C && side[at - 1] == 0xA5 && side[at + 1] == before[at + 1 - 100]);
            CHECK(cyc_fds_disk_writes() > 0 && cyc_fds_disk_generation() > gen0);
        }
        /* The register side of writes leaves the read path intact. */
        wr(0x4025, 0x05 | 0x40);
        CHECK(until_byte(1000, &e) && !(e.addr & CYC_FDS_BYTE_WRITE));
        /* Leaving write mode ends the write run: one ring event, from the
         * first stored position. */
        bool run = false;
        for (uint64_t i = cyc_ring_total(); i-- > cyc_ring_oldest();) {
            CycRingEvent r;
            if (cyc_ring_get(i, &r) && r.kind == CYC_EV_FDS_WRITE_RUN) { run = true; e = r; break; }
        }
        CHECK(run == !protect);
        if (run) CHECK((e.value >> 24) == 0 && e.repeat > 10 && e.addr >= 2);
    }
    /* A saved side goes back in only with the motor off or the side out. */
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, 0);
    uint32_t len;
    const uint8_t *side = cyc_fds_side_stream(0, &len);
    uint8_t *copy = (uint8_t *)malloc(len);
    memcpy(copy, side, len);
    copy[5000] ^= 0xFF;
    uint64_t gen = cyc_fds_disk_generation();
    wr(0x4025, 0x05);                                          /* motor on */
    clock(4);
    CHECK(cyc_fds_motor_on() && !cyc_fds_set_side_stream(0, copy, len));
    CHECK(cyc_fds_set_side_stream(1, copy, len));             /* not in the drive */
    wr(0x4025, 0x04);
    CHECK(!cyc_fds_motor_on() && cyc_fds_set_side_stream(0, copy, len) && cyc_fds_disk_generation() > gen);
    CHECK(cyc_fds_side_stream(0, &len)[5000] == copy[5000]);
    free(copy);
}

static void test_disabled_registers(void)
{
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, 0);
    wr(0x4026, 0x11);
    wr(0x4023, 0x02);                                              /* disk registers off */
    wr(0x4026, 0x22); wr(0x4025, 0x08); wr(0x4024, 0x33);          /* ignored (lr:352-354) */
    CHECK(hw_cart.mirroring == HW_MIRROR_VERTICAL && hw_cart.m.fds.write_data == 0);
    for (uint16_t a = 0x4030; a <= 0x4033; ++a) CHECK(rd_bus(a, 0x6B) == 0x6B);
    wr(0x4023, 0x03);
    CHECK(rd(0x4033) == 0x11);
    /* Sound: the wavetable reads back while $4089.7 is set; the gains a write
     * with bit 7 loads read at $4090/$4092 (FdsAudio.h ReadRegister). */
    wr(0x4089, 0x80); wr(0x4045, 0x3F); wr(0x4046, 0xFF);
    CHECK(rd_bus(0x4045, 0xC0) == 0xFF && rd_bus(0x4046, 0x00) == 0x3F);
    wr(0x4089, 0x00); wr(0x4045, 0x01);
    CHECK(hw_cart.m.fds.wave[5] == 0x3F);                          /* write-protected wavetable */
    CHECK(rd_bus(0x4045, 0) == hw_cart.m.fds.wave[0]);             /* the current position, 0 until synthesis */
    wr(0x4080, 0x80 | 0x15); wr(0x4084, 0x80 | 0x2A);
    CHECK(rd_bus(0x4090, 0x40) == 0x55 && rd_bus(0x4092, 0x00) == 0x2A);
    wr(0x4023, 0x01);                                              /* sound registers off */
    CHECK(rd_bus(0x4090, 0x40) == 0x40);
}

static void test_ring(void)
{
    load(CYC_FDS_PROFILE_MESEN, CYC_FDS_CRC_COMPUTED, false, false, 0);
    uint64_t t0 = cyc_ring_total();
    for (int i = 0; i < 100; ++i) rd(0x4032);                      /* a polling loop folds */
    CHECK(cyc_ring_total() == t0 + 1 && cyc_ring_kind_total(CYC_EV_FDS_READ) >= 100);
    CycRingEvent e;
    CHECK(cyc_ring_get(t0, &e) && e.kind == CYC_EV_FDS_READ && e.addr == 0x4032 && e.repeat == 99);
    wr(0x4020, 1); wr(0x4022, 2); clock(2);
    rd(0x4030);
    bool saw_irq = false, saw_ack = false;
    for (uint64_t i = t0; i < cyc_ring_total(); ++i)
        if (cyc_ring_get(i, &e)) {
            saw_irq |= e.kind == CYC_EV_FDS_IRQ && e.value == CYC_FDS_IRQ_TIMER;
            saw_ack |= e.kind == CYC_EV_FDS_IRQ_ACK && e.addr == 0x4030 && e.value == CYC_FDS_IRQ_TIMER;
        }
    CHECK(saw_irq && saw_ack);
    /* Eviction: the newest CYC_RING_CAPACITY events stay. */
    uint64_t start = cyc_ring_total();
    for (unsigned i = 0; i < CYC_RING_CAPACITY + 10; ++i) wr(0x4026, (uint8_t)i);
    CHECK(cyc_ring_total() == start + CYC_RING_CAPACITY + 10);
    CHECK(cyc_ring_oldest() == cyc_ring_total() - CYC_RING_CAPACITY);
    CHECK(!cyc_ring_get(cyc_ring_oldest() - 1, &e) && cyc_ring_get(cyc_ring_oldest(), &e));
    CHECK(cyc_ring_get(cyc_ring_total() - 1, &e) && e.kind == CYC_EV_FDS_WRITE && e.value == (uint8_t)(CYC_RING_CAPACITY + 9));
}

/* ---- part 2: the synthetic BIOS on the CPU ---- */

static unsigned run_cases(const char *dir)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/cases.txt", dir);
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot read %s\n", path); exit(1); }
    char line[512], name[64] = "";
    unsigned cases = 0;
    bool active = false;
    while (fgets(line, sizeof(line), f)) {
        unsigned addr, value;
        if (sscanf(line, " expect %x %x", &addr, &value) == 2) {
            CHECK(active);
            if (cyc_cpu_ram()[addr & 0x7FF] != value) {
                fprintf(stderr, "case %s: $%04X = $%02X, expected $%02X\n", name, addr, cyc_cpu_ram()[addr & 0x7FF], value);
                exit(1);
            }
            ++checks;
            continue;
        }
        char disk[64];
        int frames, used = 0;
        if (sscanf(line, "%63s %63s %d%n", name, disk, &frames, &used) < 3) continue;
        CycFdsOptions o;
        cyc_fds_default_options(&o);
        char *opt = strtok(line + used, " \t\r\n");
        while (opt) {
            char *arg = NULL;
            if (!strcmp(opt, "--fds-crc-check")) o.crc_check = true;
            else if (!strcmp(opt, "--fds-write-protect")) o.write_protect = true;
            else if ((arg = strtok(NULL, " \t\r\n")) != NULL) {
                if (!strcmp(opt, "--fds-boot-disk")) o.boot_side = strcmp(arg, "none") ? atoi(arg) : -1;
                else if (!strcmp(opt, "--fds-crc")) o.stream_crc = strcmp(arg, "mesen") ? CYC_FDS_CRC_COMPUTED : CYC_FDS_CRC_MESEN;
                else if (!strcmp(opt, "--fds-profile"))
                    o.profile = !strcmp(arg, "mesen2") ? CYC_FDS_PROFILE_MESEN2 :
                                !strcmp(arg, "hardware") ? CYC_FDS_PROFILE_HARDWARE : CYC_FDS_PROFILE_MESEN;
                else { fprintf(stderr, "case %s: option %s\n", name, opt); exit(1); }
            }
            opt = strtok(NULL, " \t\r\n");
        }
        CHECK(cyc_load_fds(bios, bios_size, image, image_size, &o));
        cyc_power_on(0);
        cyc_run_power_on();
        for (int i = 0; i < frames; ++i) cyc_run_frame();
        active = true;
        ++cases;
    }
    fclose(f);
    return cases;
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: %s <fixtures dir>\n", argv[0]); return 2; }
    char path[1024];
    snprintf(path, sizeof(path), "%s/bios/disksys.rom", argv[1]);
    bios = read_file(path, &bios_size);
    snprintf(path, sizeof(path), "%s/disk.fds", argv[1]);
    image = read_file(path, &image_size);
    CHECK(bios_size == NES_FDS_BIOS_BYTES);
    test_memory_map();
    test_timer();
    test_status_bits();
    test_drive(CYC_FDS_PROFILE_MESEN, 150);
    test_drive(CYC_FDS_PROFILE_MESEN2, 150);
    test_end_irq();
    test_crc();
    test_write();
    test_disabled_registers();
    test_ring();
    unsigned cases = run_cases(argv[1]);
    printf("cyc_fds_board_test: %u checks, %u machine cases passed\n", checks, cases);
    return 0;
}
