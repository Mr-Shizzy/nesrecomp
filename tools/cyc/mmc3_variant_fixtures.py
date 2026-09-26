"""MMC3 board variants: TxSROM (118) nametable wiring and the A12 IRQ.

Expected bytes come from the board documentation linked in MAPPERS.md: CHR ROM
pages hold their physical 1 KiB page number, and nametable writes are read back
through the address that the documented CIRAM A10 wiring must alias.
"""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract


def mmc3_irq_program(mapper, name, *, chr_kb=8, extra=b''):
    """Render with sprites at $1000 so A12 rises once per line, and take
    exactly one IRQ from a latch of 10. The handler acknowledges via $E000."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0, 0)
    store(0x4017, 0x40)
    store(0x2000, 0x08)
    store(0x2001, 0)
    code.extend([0x2c, 0x02, 0x20, 0x10, 0xfb] * 2)
    code.extend(extra)
    store(0xc000, 10)
    store(0xc001, 0)
    store(0xe001, 0)
    store(0x2001, 0x18)
    code.extend([0x58, 0xa5, 0, 0xf0, 0xfc, 0xc9, 1, 0xf0, 5, 0xa9, 0xee])
    fail = 0x8000 + len(code)
    code.extend([0x4c, fail & 255, fail >> 8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    assert len(code) < 0x100
    handler = bytes([0xa9, 0, 0x8d, 0x00, 0xe0, 0xe6, 0, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    chr_rom = b''.join(bytes([page & 255]) * 1024 for page in range(chr_kb))
    header = b'NES\x1a' + bytes([8, chr_kb // 8, (mapper & 15) << 4, mapper & 240]) + bytes(8)
    return name, header + prg + chr_rom, '00:8000\n00:8100\n', 'final:A=42'


def txsrom_fixtures():
    yield handoff('mmc3v_prg_118', 118, [(0x8000, 6), (0x8001, 3)], 3, chr_kb=128)
    yield handoff('mmc3v_prg_118_c000', 118, [(0x8000, 0x46), (0x8001, 5)], 5, chr_kb=128,
                  start=0xc000)
    # Mode 0: R0 bit 7 selects CIRAM for $2000-$27FF, R1 bit 7 for $2800-$2FFF.
    # $A000 is disconnected. Mode 1: R2-R5 select one nametable each.
    yield ppu_contract(118, 128, 128, [
        ('cpu', 0x8000, 0), ('cpu', 0x8001, 0x80),
        ('cpu', 0x8000, 1), ('cpu', 0x8001, 0x02),
        ('read', 0, 0), ('read', 0x0400, 1), ('read', 0x0800, 2),
        ('write', 0x2000, 0x31), ('write', 0x2800, 0x32),
        ('read', 0x2400, 0x31), ('read', 0x2c00, 0x32),
        ('cpu', 0xa000, 1), ('read', 0x2000, 0x31), ('read', 0x2800, 0x32),
        ('cpu', 0xa000, 0), ('read', 0x2400, 0x31),
        ('cpu', 0x8000, 1), ('cpu', 0x8001, 0x82), ('read', 0x2800, 0x31),
        ('cpu', 0x8000, 0x82), ('cpu', 0x8001, 0x05),
        ('read', 0x2000, 0x32), ('read', 0, 5), ('read', 0x1000, 0),
        ('cpu', 0x8000, 0x83), ('cpu', 0x8001, 0x86),
        ('read', 0x2400, 0x31), ('read', 0x0400, 6),
        ('cpu', 0x8000, 0x84), ('cpu', 0x8001, 0x87),
        ('cpu', 0x8000, 0x85), ('cpu', 0x8001, 0x08),
        ('read', 0x2800, 0x31), ('read', 0x2c00, 0x32),
        # $3000-$3EFF is PPU A12 high: the MMC3 answers with pattern pages 4-7,
        # which mode 1 gives to R0/R1 (both with bit 7 set here).
        ('read', 0x3000, 0x31), ('read', 0x3c00, 0x31),
        ('cpu', 0x8000, 0x80), ('cpu', 0x8001, 0x00), ('read', 0x3400, 0x32),
        # Work RAM behind the MMC3 protect register.
        ('cpu', 0xa001, 0x80), ('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a),
        ('cpu', 0xa001, 0xc0), ('cpu', 0x6000, 0x11), ('cpu_read', 0x6000, 0x5a),
    ], '_txsrom')
    # Nametable writes from a mode-1 configuration survive the IRQ program's
    # rendering; the IRQ itself must behave exactly as on mapper 4.
    yield mmc3_irq_program(118, 'mmc3v_irq_118')


def tqrom_fixtures():
    yield handoff('mmc3v_prg_119', 119, [(0x8000, 6), (0x8001, 9)], 9, chr_kb=64)
    # CHR ROM pages hold their page number; the RAM starts zeroed. Bank bit 6
    # selects RAM, addressed by bank bits 0-2; ROM uses bits 0-5.
    yield ppu_contract(119, 128, 64, [
        ('cpu', 0x8000, 0), ('cpu', 0x8001, 0x40),
        ('cpu', 0x8000, 2), ('cpu', 0x8001, 0x05),
        ('read', 0, 0), ('write', 0, 0x5a), ('write', 0x0400, 0xa5),
        ('read', 0, 0x5a), ('read', 0x0400, 0xa5),
        ('read', 0x1000, 5), ('write', 0x1000, 0x77), ('read', 0x1000, 5),
        ('cpu', 0x8000, 3), ('cpu', 0x8001, 0x78), ('read', 0x1400, 0x5a),
        ('cpu', 0x8000, 4), ('cpu', 0x8001, 0x41), ('read', 0x1800, 0xa5),
        ('write', 0x1800, 0x3c), ('read', 0x0400, 0x3c),
        ('cpu', 0x8000, 1), ('cpu', 0x8001, 0x7e), ('write', 0x0c00, 0x99),
        ('cpu', 0x8000, 5), ('cpu', 0x8001, 0x47), ('read', 0x1c00, 0x99),
        ('cpu', 0x8000, 2), ('cpu', 0x8001, 0xbf), ('read', 0x1000, 63),
        ('cpu', 0x8000, 2), ('cpu', 0x8001, 0x85), ('read', 0x1000, 5),
        # Mode 1 moves the RAM-backed 2 KiB bank to $1000.
        ('cpu', 0x8000, 0x80), ('read', 0x1000, 0x5a), ('read', 0x1400, 0x3c),
        ('read', 0, 5),
        # No work RAM: the read returns the open-bus address high byte.
        ('cpu', 0xa001, 0x80), ('cpu', 0x6000, 0x12), ('cpu_read', 0x6000, 0x60),
        ('cpu', 0x8000, 0),
    ], '_tqrom')
    yield mmc3_irq_program(119, 'mmc3v_irq_119', chr_kb=64)


def mmc3_variant_fixtures():
    yield from txsrom_fixtures()
    yield from tqrom_fixtures()
