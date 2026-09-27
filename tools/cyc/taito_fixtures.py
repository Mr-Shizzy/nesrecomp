"""Taito TC0190 (mapper 33) and TC0690 (mapper 48) fixtures (nesdev wiki,
INES Mapper 033 and 048). CHR ROM pages hold their page number."""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract


def tc0690_irq_program(dma=False):
    """Render with sprites at $1000 so A12 rises once per line; latch 10
    (written as $F5, inverted), take exactly one IRQ; the handler disables and
    acknowledges with $C003. The main loop counts iterations until the IRQ
    into $01/$02, so the IRQ's timing reaches the memory hash."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0, 0)
    store(0x4017, 0x40)
    store(0x2000, 0x08)
    store(0x2001, 0)
    code.extend([0x2c, 0x02, 0x20, 0x10, 0xfb] * 2)
    store(0xc000, 0xff ^ 10)
    store(0xc001, 0)
    store(0xc002, 0)
    store(0x2001, 0x18)
    if dma:
        store(0x4014, 2)
    code.extend([0x58, 0xa2, 0, 0xa0, 0, 0xe8, 0xd0, 1, 0xc8, 0xa5, 0, 0xf0, 0xf8,
                 0x86, 1, 0x84, 2, 0xa5, 0, 0xc9, 1, 0xf0, 5, 0xa9, 0xee])
    fail = 0x8000 + len(code)
    code.extend([0x4c, fail & 255, fail >> 8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    assert len(code) < 0x100
    handler = bytes([0xa9, 0, 0x8d, 0x03, 0xc0, 0xe6, 0, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    chr_rom = b''.join(bytes([page & 255]) * 1024 for page in range(8))
    header = b'NES\x1a' + bytes([8, 1, 0x00, 0x30]) + bytes(8)
    return 'tc0690_irq' + ('_dma' if dma else ''), header + prg + chr_rom, '00:8000\n00:8100\n', 'final:A=42'


def scanline_irq_phase(mapper):
    """Take 64 consecutive scanline IRQs (latch 0) and store, in each, the
    low byte of a main-loop counter that advances every 13 cycles. The PPU's
    fractional CPU-cycle scanline sweeps the A12 edge through every CPU-cycle
    phase, so a one-cycle difference in when /IRQ asserts changes $0300-$033F.
    mapper 48 writes the TC0690 registers (inverted latch, $C002/$C003);
    mapper 4 writes the MMC3's ($C000/$C001/$E001/$E000)."""
    tc0690 = mapper == 48
    enable, disable = (0xc002, 0xc003) if tc0690 else (0xe001, 0xe000)
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x10, 0)
    store(0x11, 0)
    store(0x4017, 0x40)
    store(0x2000, 0x08)
    store(0x2001, 0)
    code.extend([0x2c, 0x02, 0x20, 0x10, 0xfb] * 2)
    store(0xc000, 0xff if tc0690 else 0x00)
    store(0xc001, 0)
    store(enable, 0)
    store(0x2001, 0x18)
    # CLI; loop: INC $10; LDA $11; CMP #64; BCC loop; LDA #$42; done: JMP done
    code.extend([0x58, 0xe6, 0x10, 0xa5, 0x11, 0xc9, 64, 0x90, 0xf8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    assert len(code) < 0x100
    # PHA; TXA; PHA; LDX $11; LDA $10; STA $0300,X; INC $11;
    # STA disable; (CPX #63; BCS +3; STA enable); PLA; TAX; PLA; RTI
    handler = bytearray([0x48, 0x8a, 0x48, 0xa6, 0x11, 0xa5, 0x10, 0x9d, 0x00, 0x03, 0xe6, 0x11,
                         0x8d, disable & 255, disable >> 8, 0xe0, 63, 0xb0, 3,
                         0x8d, enable & 255, enable >> 8, 0x68, 0xaa, 0x68, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    chr_rom = b''.join(bytes([page & 255]) * 1024 for page in range(8))
    header = b'NES\x1a' + bytes([8, 1, (mapper & 15) << 4, mapper & 0xf0]) + bytes(8)
    return 'irq_phase_%d' % mapper, header + prg + chr_rom, '00:8000\n00:8100\n', 'final:A=42'


def taito_fixtures():
    yield handoff('tc0190_prg', 33, [(0x8000, 0x45)], 5, chr_kb=8)
    yield handoff('tc0190_prg_c000', 33, [(0xc000, 0x06)], 6, chr_kb=8)   # A14 not decoded
    yield handoff('tc0690_prg', 48, [(0x8000, 0x47)], 7, chr_kb=8)
    yield ppu_contract(33, 256, 256, [
        ('cpu', 0x8002, 0x13), ('read', 0, 0x26), ('read', 0x0400, 0x27),
        ('cpu', 0x8003, 0x7f), ('read', 0x0800, 0xfe), ('read', 0x0c00, 0xff),
        ('cpu', 0xa000, 0x21), ('cpu', 0xe003, 0xfe), ('read', 0x1000, 0x21), ('read', 0x1c00, 0xfe),
        ('cpu', 0x8000, 0x00), ('write', 0x2000, 0x31), ('read', 0x2800, 0x31),   # vertical
        ('cpu', 0xc000, 0x40), ('read', 0x2400, 0x31)], '_tc0190')                 # horizontal via A14 mirror
    yield ppu_contract(48, 256, 256, [
        ('cpu', 0x8002, 0x13), ('read', 0, 0x26), ('cpu', 0xa003, 0xfe), ('read', 0x1c00, 0xfe),
        ('cpu', 0xe000, 0x00), ('write', 0x2000, 0x31), ('read', 0x2800, 0x31),
        ('cpu', 0x8000, 0x40), ('read', 0x2800, 0x31),                              # $8000 bit 6 ignored
        ('cpu', 0xe000, 0x40), ('read', 0x2400, 0x31)], '_tc0690')
    yield tc0690_irq_program()
    yield tc0690_irq_program(dma=True)
    yield scanline_irq_phase(48)
    yield scanline_irq_phase(4)
