"""Sunsoft FME-7 banks, $6000 window, CPU-cycle IRQ, and 5B sound programs.

Expected values come from https://www.nesdev.org/wiki/Sunsoft_FME-7 and
https://www.nesdev.org/wiki/Sunsoft_5B_audio; CHR ROM pages hold their page
number. The 5B programs are measured by test_cyc_expansion_audio.py.
"""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract


def _cmd(command, value):
    return [('cpu', 0x8000, command), ('cpu', 0xa000, value)]


def fme7_contract():
    ops = []
    ops += _cmd(0, 0x21) + _cmd(7, 0xfe) + [('read', 0, 0x21), ('read', 0x1c00, 0xfe)]
    ops += _cmd(3, 0x80) + [('read', 0x0c00, 0x80)]
    # Mirroring 0-3: vertical, horizontal, one-screen A, one-screen B.
    ops += _cmd(12, 0) + [('write', 0x2000, 0x31), ('read', 0x2800, 0x31)]
    ops += _cmd(12, 1) + [('read', 0x2400, 0x31)]
    ops += _cmd(12, 2) + [('read', 0x2c00, 0x31)]
    ops += _cmd(12, 3) + [('write', 0x2000, 0x32)]
    ops += _cmd(12, 0) + [('read', 0x2400, 0x32), ('read', 0x2000, 0x31)]
    # $6000: ROM bank 5 (its first code byte is SEI, the vectors end it),
    # enabled RAM, and open bus with RAM selected but disabled.
    ops += _cmd(8, 0x05) + [('cpu_read', 0x6000, 0x78), ('cpu_read', 0x7ffb, 0x80)]
    ops += _cmd(8, 0xc0) + [('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a)]
    ops += _cmd(8, 0x40) + [('cpu', 0x6000, 0x11), ('cpu_read', 0x6000, 0x60)]
    ops += _cmd(8, 0xc0) + [('cpu_read', 0x6000, 0x5a)]
    return ppu_contract(69, 128, 256, ops, '_fme7')


def fme7_irq_program(dma=False):
    """Take exactly one IRQ from a counter of $03E8; the handler acknowledges
    and disables through command 13. With dma, an OAM DMA runs while counting."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0, 0)
    store(0x4017, 0x40)
    for command, value in ((14, 0xe8), (15, 0x03), (13, 0x81)):
        store(0x8000, command)
        store(0xa000, value)
    if dma:
        store(0x4014, 2)
    code.extend([0x58, 0xa5, 0, 0xf0, 0xfc, 0xc9, 1, 0xf0, 5, 0xa9, 0xee])
    fail = 0x8000 + len(code)
    code.extend([0x4c, fail & 255, fail >> 8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    handler = bytes([0xa9, 13, 0x8d, 0, 0x80, 0xa9, 0, 0x8d, 0, 0xa0, 0xe6, 0, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    header = b'NES\x1a' + bytes([8, 1, 0x50, 0x40]) + bytes(8)
    name = 'fme7_irq' + ('_dma' if dma else '')
    return name, header + prg + bytes(8192), '00:8000\n00:8100\n', 'final:A=42'


def s5b_program(name, registers):
    """Silence the APU, write 5B registers in order, then idle."""
    code = bytearray([0x78, 0xd8])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x4015, 0)
    store(0x4017, 0x40)
    for reg, value in registers:
        store(0xc000, reg)
        store(0xe000, value)
    code.extend([0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        prg[bank * 8192:bank * 8192 + len(code)] = code
        prg[(bank + 1) * 8192 - 6:(bank + 1) * 8192] = bytes([0, 0x80]) * 3
    header = b'NES\x1a' + bytes([8, 1, 0x50, 0x40]) + bytes(8)
    return name, header + prg + bytes(8192), '00:8000\n', 'A=42'


# Measured by test_cyc_expansion_audio.py: name -> expected Hz (None = noise).
CPU_HZ = 21477272.7272727 / 12
S5B_TONES = {
    # Channel B tone, period 254: a square of CPU/(32*254).
    's5b_tone': CPU_HZ / (32 * 254),
    # Channel C holds its envelope level (tone/noise off); //// with period 14
    # is a 32-step sawtooth of CPU/(16*14*32).
    's5b_envelope': CPU_HZ / (16 * 14 * 32),
    # Channel A noise only, period 4: aperiodic, measured for level and parity.
    's5b_noise': None,
}


def s5b_fixtures():
    yield s5b_program('s5b_tone', [(7, 0x3d), (2, 254), (3, 0), (9, 15)])
    yield s5b_program('s5b_envelope', [(7, 0x3f), (8, 0), (9, 0), (10, 0x10),
                                       (11, 14), (12, 0), (13, 0x0c)])
    yield s5b_program('s5b_noise', [(7, 0x37), (6, 4), (8, 15)])


def sunsoft_fixtures():
    yield handoff('fme7_prg', 69, [(0x8000, 9), (0xa000, 3)], 3, chr_kb=8)
    yield handoff('fme7_prg_masked', 69, [(0x8000, 0xf9), (0xa000, 0xcb)], 11, chr_kb=8)
    yield fme7_contract()
    yield fme7_irq_program()
    yield fme7_irq_program(dma=True)
    yield from s5b_fixtures()
