"""Namco 163 (mapper 19) and Namco 175/340 (mapper 210) execution fixtures.

Expected values follow https://www.nesdev.org/wiki/Namco_163, Namco_163_audio
and INES_Mapper_210. CHR ROM pages hold their page number; CIRAM and work RAM
start zeroed. The N163 tone is measured by test_cyc_expansion_audio.py.
"""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract
from cart_variant_fixtures import nes2

CPU_HZ = 21477272.7272727 / 12
N163_FREQ = 3867
# One channel, 16-sample square: CPU * freq / (15 cycles * 1 channel * 65536 * 16).
N163_TONES = {'nes2_n163_tone': CPU_HZ * N163_FREQ / (15 * 65536 * 16)}


def n163_contract():
    return ppu_contract(19, 256, 256, [
        ('cpu', 0x8000, 0x21), ('read', 0, 0x21), ('read', 0x03ff, 0x21),
        # CIRAM as CHR RAM: pattern page 0 and nametable 0 both select CIRAM page 0.
        ('cpu', 0x8000, 0xe0), ('cpu', 0xc000, 0xe0),
        ('write', 0x0005, 0x77), ('read', 0x2005, 0x77),
        ('write', 0x2006, 0x66), ('read', 0x0006, 0x66), ('read', 0x3006, 0x66),
        ('cpu', 0xc800, 0xe1), ('write', 0x2400, 0x55),
        ('cpu', 0x8000, 0xe1), ('read', 0x0000, 0x55),
        # CHR ROM nametable: reads the page, drops writes.
        ('cpu', 0xd000, 0x42), ('read', 0x2800, 0x42), ('write', 0x2800, 0x99), ('read', 0x2811, 0x42),
        ('cpu', 0xd800, 0xe1), ('read', 0x2c00, 0x55),
        # $E800 bit 6: $E0-$FF in the low half are CHR ROM again.
        ('cpu', 0xe800, 0x40), ('read', 0x0000, 0xe1), ('cpu', 0xe800, 0x00), ('read', 0x0000, 0x55),
        ('cpu', 0xb800, 0xe0), ('cpu', 0xe800, 0x80), ('read', 0x1c05, 0xe0),
        ('cpu', 0xe800, 0x00), ('read', 0x1c05, 0x77),
        # Internal RAM through $F800/$4800 (address $10, auto-increment).
        ('cpu', 0xf800, 0x90), ('cpu', 0x4800, 0x12), ('cpu', 0x4800, 0x34),
        ('cpu', 0xf800, 0x11), ('cpu_read', 0x4800, 0x34), ('cpu_read', 0x4800, 0x34),
        ('cpu', 0xf800, 0x90), ('cpu_read', 0x4800, 0x12), ('cpu_read', 0x4800, 0x34),
        # Auto-increment stops at $7F instead of wrapping to $00.
        ('cpu', 0xf800, 0xff), ('cpu', 0x4800, 0xaa), ('cpu', 0x4800, 0x0b),
        ('cpu', 0xf800, 0x00), ('cpu_read', 0x4800, 0x00),
        ('cpu', 0xf800, 0x7f), ('cpu_read', 0x4800, 0x0b), ('cpu', 0x4800, 0x00),
        # IRQ counter readback while disabled.
        ('cpu', 0x5000, 0x5a), ('cpu', 0x5800, 0x21), ('cpu_read', 0x5000, 0x5a), ('cpu_read', 0x5800, 0x21),
        # No work RAM on this board: open bus.
        ('cpu_read', 0x6000, 0x60),
        ('cpu', 0x8000, 0x00), ('cpu', 0xc000, 0xe0), ('cpu', 0xc800, 0xe1),
        ('cpu', 0xd000, 0xe0), ('cpu', 0xd800, 0xe1)], '_n163')


def n163_wram_contract():
    """8 KiB work RAM: writes need $F800 upper nibble $4 and a clear window bit."""
    case = ppu_contract(19, 256, 256, [
        ('cpu', 0xf800, 0x40), ('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a),
        ('cpu', 0xf800, 0x41), ('cpu', 0x6000, 0x11), ('cpu_read', 0x6000, 0x5a),
        ('cpu', 0x6800, 0x22), ('cpu_read', 0x6800, 0x22),
        ('cpu', 0xf800, 0xc0), ('cpu', 0x7000, 0x33), ('cpu_read', 0x7000, 0x00),
        ('cpu', 0xf800, 0x48), ('cpu', 0x7fff, 0x44), ('cpu_read', 0x7fff, 0x00),
        ('cpu', 0x7000, 0x55), ('cpu_read', 0x7000, 0x55)], '_n163_wram')
    return nes2(case, sub=3, ram=7)


def n175_contract():
    case = ppu_contract(210, 256, 256, [
        ('cpu', 0x8000, 0xe1), ('read', 0, 0xe1),
        ('cpu_read', 0x6000, 0x60),
        ('cpu', 0xc000, 1), ('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a),
        ('cpu', 0xc800, 0), ('cpu_read', 0x6000, 0x5a),
        ('cpu', 0xc7ff, 0), ('cpu_read', 0x6000, 0x60),
        # Mirroring is hardwired (horizontal header): $2400 aliases $2000.
        ('cpu', 0xe000, 0xc0), ('write', 0x2000, 0x31), ('read', 0x2400, 0x31)], '_n175')
    return nes2(case, sub=1, ram=7)


def n340_contract():
    case = ppu_contract(210, 256, 256, [
        ('cpu', 0x8000, 0x33), ('read', 0x0100, 0x33),
        ('cpu', 0xe000, 0x00), ('write', 0x2000, 0x31), ('read', 0x2c00, 0x31),   # one-screen A
        ('cpu', 0xe000, 0x40), ('read', 0x2800, 0x31),                             # vertical
        ('cpu', 0xe000, 0x80), ('write', 0x2000, 0x32), ('read', 0x2400, 0x32),   # one-screen B
        ('cpu', 0xe000, 0xc0), ('read', 0x2400, 0x31), ('read', 0x2800, 0x32),    # horizontal
        ('cpu', 0xc000, 1), ('cpu_read', 0x6000, 0x60)], '_n340')
    return nes2(case, sub=2)


def n163_irq_program():
    """The 15-bit up-counter from $7C00 interrupts once at $7FFF; the handler
    acknowledges and disables it by writing $5800."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0, 0)
    store(0x4017, 0x40)
    store(0x5000, 0x00)
    store(0x5800, 0xfc)
    code.extend([0x58, 0xa5, 0, 0xf0, 0xfc, 0xc9, 1, 0xf0, 5, 0xa9, 0xee])
    fail = 0x8000 + len(code)
    code.extend([0x4c, fail & 255, fail >> 8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    handler = bytes([0xa9, 0, 0x8d, 0, 0x58, 0xe6, 0, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    header = b'NES\x1a' + bytes([8, 1, 0x30, 0x10]) + bytes(8)
    return 'n163_irq', header + prg + bytes(8192), '00:8000\n00:8100\n', 'final:A=42'


def n163_tone():
    """Channel 8 alone at volume 15 playing a 16-sample square (8 x 0, 8 x 15)."""
    code = bytearray([0x78, 0xd8])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x4015, 0)
    store(0x4017, 0x40)
    store(0xf800, 0x80)
    for i in range(8):
        store(0x4800, 0x00 if i < 4 else 0xff)
    store(0xf800, 0x80 | 0x78)
    for value in (N163_FREQ & 255, 0, N163_FREQ >> 8, 0, 256 - 16, 0, 0, 0x0f):
        store(0x4800, value)
    code.extend([0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        prg[bank * 8192:bank * 8192 + len(code)] = code
        prg[(bank + 1) * 8192 - 6:(bank + 1) * 8192] = bytes([0, 0x80]) * 3
    header = b'NES\x1a' + bytes([8, 1, 0x30, 0x10]) + bytes(8)
    return nes2(('n163_tone', header + prg + bytes(8192), '00:8000\n', 'A=42'), sub=3)


def n163_phase_sampler():
    """The generator is a free-running 15-cycle divider from power-on, and its
    phase registers are CPU-readable. Sample channel 8's phase low byte 256
    times in a 14-cycle loop (co-prime with 15) into $0300-$03FF, so any
    divider phase difference between implementations changes memory."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0xf800, 0x80 | 0x78)
    for value in (0x01, 0, 0, 0, 256 - 16, 0, 0, 0x0f):   # freq 1, one channel
        store(0x4800, value)
    store(0xf800, 0x79)                                    # phase low, no increment
    # LDX #0; loop: LDA $4800 (4); STA $0300,X (5); INX (2); BNE loop (3) = 14 cycles
    code.extend([0xa2, 0, 0xad, 0x00, 0x48, 0x9d, 0x00, 0x03, 0xe8, 0xd0, 0xf7,
                 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        prg[bank * 8192:bank * 8192 + len(code)] = code
        prg[(bank + 1) * 8192 - 6:(bank + 1) * 8192] = bytes([0, 0x80]) * 3
    header = b'NES\x1a' + bytes([8, 1, 0x30, 0x10]) + bytes(8)
    return 'n163_phase', header + prg + bytes(8192), '00:8000\n', 'A=42'


def namco_fixtures():
    yield handoff('namco163_prg', 19, [(0xe000, 3)], 3, chr_kb=8)
    yield handoff('namco163_prg_a000', 19, [(0xe800, 0xc7)], 0, chr_kb=8)
    yield nes2(handoff('namco175_prg', 210, [(0xe000, 5)], 5, chr_kb=8), sub=1)
    yield nes2(handoff('namco340_prg', 210, [(0xe000, 0xc5)], 5, chr_kb=8), sub=2)
    yield handoff('namco210_ines', 210, [(0xe000, 6)], 6, chr_kb=8)
    yield n163_contract()
    yield n163_wram_contract()
    yield n175_contract()
    yield n340_contract()
    yield n163_irq_program()
    yield n163_tone()
    yield n163_phase_sampler()
