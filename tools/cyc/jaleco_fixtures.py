"""Jaleco SS88006 (mapper 18) fixtures (nesdev wiki, INES Mapper 018)."""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract
from cart_variant_fixtures import nes2


def ss88006_irq(width_ctrl, dma=False):
    """Reload $0321 (1, 33, 801 or 801 counts for 4/8/12/16 bits), take
    exactly one IRQ; the handler disables and acknowledges with $F001. The
    main loop counts iterations until the IRQ and stores the 16-bit count at
    $01 and $02, so each width leaves a different memory hash."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0, 0)
    store(0x4017, 0x40)
    for a, v in ((0xe000, 1), (0xe001, 2), (0xe002, 3), (0xe003, 0), (0xf000, 0), (0xf001, width_ctrl)):
        store(a, v)
    if dma:
        store(0x4014, 2)
    # CLI; LDX #0; LDY #0; loop: INX; BNE +1; INY; LDA $00; BEQ loop; STX $01; STY $02
    code.extend([0x58, 0xa2, 0, 0xa0, 0, 0xe8, 0xd0, 1, 0xc8, 0xa5, 0, 0xf0, 0xf8,
                 0x86, 1, 0x84, 2, 0xa5, 0, 0xc9, 1, 0xf0, 5, 0xa9, 0xee])
    fail = 0x8000 + len(code)
    code.extend([0x4c, fail & 255, fail >> 8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    handler = bytes([0xa9, 0, 0x8d, 0x01, 0xf0, 0xe6, 0, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    header = b'NES\x1a' + bytes([8, 1, 0x20, 0x10]) + bytes(8)
    name = 'ss88006_irq_%x' % width_ctrl + ('_dma' if dma else '')
    return name, header + prg + bytes(8192), '00:8000\n00:8100\n', 'final:A=42'


def jaleco_fixtures():
    yield handoff('ss88006_prg', 18, [(0x8000, 3)], 3, chr_kb=8)
    yield handoff('ss88006_prg_hi', 18, [(0x8001, 1), (0x8000, 2)], 18, prg_kb=256, chr_kb=8)
    yield ppu_contract(18, 256, 256, [
        ('cpu', 0xa000, 5), ('cpu', 0xa001, 4), ('read', 0, 0x45),
        ('cpu', 0xd002, 0x0e), ('cpu', 0xd003, 0x0f), ('read', 0x1c00, 0xfe),
        ('cpu', 0xf002, 0), ('write', 0x2000, 0x31), ('read', 0x2400, 0x31),
        ('cpu', 0xf002, 1), ('read', 0x2800, 0x31),
        ('cpu', 0xf002, 2), ('read', 0x2c00, 0x31),
        ('cpu', 0xf002, 3), ('write', 0x2000, 0x32),
        ('cpu', 0xf002, 1), ('read', 0x2400, 0x32), ('cpu_read', 0x6000, 0x60)], '_ss88006')
    yield nes2(ppu_contract(18, 256, 256, [
        ('cpu_read', 0x6000, 0x60), ('cpu', 0x9002, 1), ('cpu', 0x6000, 0x11), ('cpu_read', 0x6000, 0x00),
        ('cpu', 0x9002, 3), ('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a),
        ('cpu', 0x9002, 2), ('cpu_read', 0x6000, 0x60),
        ('cpu', 0x9002, 1), ('cpu_read', 0x7fff, 0x00)], '_ss88006_ram'), ram=7)
    for ctrl in (1, 3, 5, 9):
        yield ss88006_irq(ctrl)
    yield ss88006_irq(1, dma=True)
